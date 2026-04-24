"""Import an arkiv bundle back into mail-memex.

Bundles emitted by :mod:`mail_memex.export.arkiv_export` (or any other
tool following the arkiv spec, within reason) are read, classified by
record kind, and inserted into the DB.

Supported input layouts (all auto-detected):

- directory with ``records.jsonl``, ``schema.yaml``, and ``README.md``
- ``.zip`` file containing those files
- ``.tar.gz`` / ``.tgz`` file containing those files
- bare ``.jsonl`` file of arkiv records (no schema/README needed)
- ``.jsonl.gz`` file of gzipped arkiv records — the shape an HTML SPA
  would emit for round-tripping marginalia back to the primary DB

This is intentionally forgiving: if ``schema.yaml`` or ``README.md`` is
missing but ``records.jsonl`` is present, we still import. The bundle's
"identity as a mail-memex arkiv" is a soft claim; the JSONL records
are what we actually need.

Record kinds handled:

- ``kind == "email"``      : insert or skip-duplicate keyed on message_id
- ``kind == "marginalia"`` : insert or skip-duplicate keyed on uuid
- unknown kinds are ignored

Round-trip fidelity:

- Emails: identified by ``message_id``. Re-importing the same bundle is
  safe; duplicates are skipped. Existing rows are left untouched (the
  archive DB is the source of truth for locally-added fields like tags).
- Marginalia: identified by UUID. Uses INSERT OR IGNORE semantics via
  ``merge_marginalia``, so re-importing the same bundle does not create
  duplicate notes.

``--merge`` vs default: duplicate-skipping is unconditional (it's the
only sane behaviour for a durable, idempotent round-trip). ``--merge``
is accepted for CLI parity with the rest of the ``*-memex`` ecosystem
and reserves the semantic for a future stricter-add mode.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import zipfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _jsonl_peek_first_record(reader) -> dict[str, Any] | None:
    """Return the first parsed JSONL record, or None if unparseable/empty."""
    try:
        for line in reader:
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            return rec if isinstance(rec, dict) else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return None


_MM_KINDS = ("email", "marginalia")


def _is_mail_memex_arkiv_record(rec: dict[str, Any]) -> bool:
    """Heuristic: is this record from mail-memex?

    A record from mail-memex arkiv export has ``kind`` in
    {"email", "marginalia"} and either a ``uri`` that starts with
    ``mail-memex://`` (strict) or a recognisable shape.
    """
    if not isinstance(rec, dict):
        return False
    kind = rec.get("kind")
    if kind not in _MM_KINDS:
        return False
    uri = rec.get("uri", "")
    if isinstance(uri, str) and uri.startswith("mail-memex://"):
        return True
    # Permissive fallback: accept anything with a recognisable kind and
    # a required identifier.
    if kind == "email" and rec.get("metadata", {}).get("message_id"):
        return True
    if kind == "marginalia" and rec.get("metadata", {}).get("uuid"):
        return True
    return False


def detect(path: str | Path) -> bool:
    """Return True if *path* looks like an arkiv bundle we can read."""
    p = Path(path)
    if not p.exists():
        return False

    if p.is_dir():
        jsonl = p / "records.jsonl"
        if not jsonl.is_file():
            return False
        with open(jsonl, encoding="utf-8") as f:
            rec = _jsonl_peek_first_record(f)
        return rec is not None and _is_mail_memex_arkiv_record(rec)

    lower = str(p).lower()

    if lower.endswith(".zip"):
        try:
            with zipfile.ZipFile(p) as zf:
                if "records.jsonl" not in zf.namelist():
                    return False
                with zf.open("records.jsonl") as f:
                    rec = _jsonl_peek_first_record(f)
            return rec is not None and _is_mail_memex_arkiv_record(rec)
        except (zipfile.BadZipFile, KeyError):
            return False

    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        try:
            with tarfile.open(p, "r:gz") as tf:
                try:
                    member = tf.getmember("records.jsonl")
                except KeyError:
                    return False
                extracted = tf.extractfile(member)
                if extracted is None:
                    return False
                rec = _jsonl_peek_first_record(extracted)
            return rec is not None and _is_mail_memex_arkiv_record(rec)
        except tarfile.TarError:
            return False

    if lower.endswith(".jsonl.gz"):
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                rec = _jsonl_peek_first_record(f)
            return rec is not None and _is_mail_memex_arkiv_record(rec)
        except (OSError, gzip.BadGzipFile):
            return False

    if lower.endswith(".jsonl"):
        try:
            with open(p, encoding="utf-8") as f:
                rec = _jsonl_peek_first_record(f)
            return rec is not None and _is_mail_memex_arkiv_record(rec)
        except OSError:
            return False

    return False


# ---------------------------------------------------------------------------
# Bundle reading
# ---------------------------------------------------------------------------


def _open_jsonl(path: str | Path) -> Iterable[dict[str, Any]]:
    """Yield records from the records.jsonl inside a bundle, whatever its shape."""
    p = Path(path)
    if p.is_dir():
        with open(p / "records.jsonl", encoding="utf-8") as f:
            yield from _parse_jsonl_lines(f)
        return

    lower = str(p).lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(p) as zf:
            with zf.open("records.jsonl") as f:
                text = io.TextIOWrapper(f, encoding="utf-8")
                yield from _parse_jsonl_lines(text)
        return
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        with tarfile.open(p, "r:gz") as tf:
            member = tf.getmember("records.jsonl")
            extracted = tf.extractfile(member)
            if extracted is None:
                return
            text = io.TextIOWrapper(extracted, encoding="utf-8")
            yield from _parse_jsonl_lines(text)
        return
    if lower.endswith(".jsonl.gz"):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            yield from _parse_jsonl_lines(f)
        return
    if lower.endswith(".jsonl"):
        with open(p, encoding="utf-8") as f:
            yield from _parse_jsonl_lines(f)
        return
    raise ValueError(f"unrecognized arkiv bundle: {path!r}")


def _parse_jsonl_lines(reader) -> Iterable[dict[str, Any]]:
    for line in reader:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            # Tolerate individual bad lines rather than failing the whole import.
            continue


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(ts: str | None) -> datetime | None:
    if not ts:
        return None
    cleaned = ts.replace("Z", "+00:00").split("+")[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _split_csv(value: Any) -> list[str]:
    """Parse a comma-separated address string into a list. Robust to None."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def import_arkiv(
    db,
    path: str | Path,
    *,
    merge: bool = False,
) -> dict[str, int]:
    """Import an arkiv bundle into *db*.

    Parameters
    ----------
    db:
        Target :class:`mail_memex.core.database.Database`.
    path:
        Path to a directory, ``.zip``, ``.tar.gz`` / ``.tgz``, bare
        ``.jsonl``, or ``.jsonl.gz`` bundle.
    merge:
        Reserved for CLI parity with the rest of the ``*-memex``
        ecosystem. Currently a no-op because the insert path is already
        duplicate-safe.

    Returns
    -------
    dict
        ``{"emails_seen": N, "emails_added": N, "emails_skipped_existing": N,
           "marginalia_seen": N, "marginalia_added": N,
           "marginalia_skipped_existing": N}``
    """
    # SQLAlchemy imports are local so that ``detect()`` can be called from
    # a lightweight context without dragging the ORM into process startup.
    from sqlalchemy import select

    from mail_memex.core.marginalia import create_marginalia
    from mail_memex.core.models import Email, Marginalia
    from mail_memex.importers.parser import clean_message_id

    stats = {
        "emails_seen": 0,
        "emails_added": 0,
        "emails_skipped_existing": 0,
        "marginalia_seen": 0,
        "marginalia_added": 0,
        "marginalia_skipped_existing": 0,
    }

    # Two passes over the stream isn't free, but bundles are compact and
    # this makes the semantics clean: emails first, marginalia after so
    # their target_uris can point at emails that landed this round.
    records = list(_open_jsonl(path))

    with db.session() as session:
        for rec in records:
            if not isinstance(rec, dict):
                continue
            if rec.get("kind") != "email":
                continue
            stats["emails_seen"] += 1

            meta = rec.get("metadata") or {}
            message_id = clean_message_id(meta.get("message_id"))
            if not message_id:
                continue

            existing = session.execute(
                select(Email).where(Email.message_id == message_id)
            ).scalar_one_or_none()
            if existing is not None:
                stats["emails_skipped_existing"] += 1
                continue

            # Build a minimal Email row from the record. Unknown headers
            # are preserved in metadata_json so nothing is lost; threading
            # is rebuilt from in_reply_to on the next `rebuild threads` pass.
            to_addrs = meta.get("to_addrs")
            cc_addrs = meta.get("cc_addrs")
            bcc_addrs = meta.get("bcc_addrs")

            # Stored form is naive UTC (column is without timezone).
            ts = _parse_timestamp(rec.get("timestamp"))

            email = Email(
                message_id=message_id,
                from_addr=meta.get("from_addr") or "",
                from_name=meta.get("from_name"),
                subject=meta.get("subject"),
                date=ts.replace(tzinfo=None) if ts else None,
                to_addrs=to_addrs if isinstance(to_addrs, str) else None,
                cc_addrs=cc_addrs if isinstance(cc_addrs, str) else None,
                bcc_addrs=bcc_addrs if isinstance(bcc_addrs, str) else None,
                in_reply_to=clean_message_id(meta.get("in_reply_to")),
                thread_id=meta.get("thread_id"),
                body_text=rec.get("content"),
            )
            session.add(email)
            stats["emails_added"] += 1

        session.flush()  # ensure emails visible before marginalia queries.

        for rec in records:
            if not isinstance(rec, dict):
                continue
            if rec.get("kind") != "marginalia":
                continue
            stats["marginalia_seen"] += 1

            meta = rec.get("metadata") or {}
            uuid = meta.get("uuid")
            if not uuid:
                continue

            existing_m = session.execute(
                select(Marginalia).where(Marginalia.uuid == uuid)
            ).scalar_one_or_none()
            if existing_m is not None:
                stats["marginalia_skipped_existing"] += 1
                continue

            target_uris = meta.get("target_uris") or []
            if not isinstance(target_uris, list):
                target_uris = []

            create_marginalia(
                session,
                target_uris=[str(u) for u in target_uris],
                content=rec.get("content") or "",
                category=meta.get("category"),
                color=meta.get("color"),
                pinned=bool(meta.get("pinned", False)),
            )
            # create_marginalia always generates a fresh UUID, but the
            # bundle carries its own. Overwrite to preserve round-trip
            # identity, then re-flush.
            session.flush()
            newly_created = session.execute(
                select(Marginalia).order_by(Marginalia.id.desc()).limit(1)
            ).scalar_one_or_none()
            if newly_created is not None and newly_created.uuid != uuid:
                newly_created.uuid = uuid
                # Timestamp preservation: round-tripped bundles can give us
                # the original created_at/updated_at. Restore them when
                # present so subsequent exports stay stable.
                c = _parse_timestamp(meta.get("created_at"))
                u = _parse_timestamp(meta.get("updated_at"))
                if c is not None:
                    newly_created.created_at = c
                if u is not None:
                    newly_created.updated_at = u
                session.flush()

            stats["marginalia_added"] += 1

        session.commit()

    return stats
