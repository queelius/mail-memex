# CLI Reorganization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reorganize mtk CLI by grouping scattered tag and rebuild commands into proper command groups.

**Architecture:** Replace 3 top-level tag commands (`tag`, `list-tags`, `tag-batch`) with a `tag` sub-app containing `add`, `remove`, `list`, `batch`. Replace 2 top-level rebuild commands (`rebuild-index`, `rebuild-threads`) with a `rebuild` sub-app containing `index`, `threads`. No logic changes — only command routing.

**Tech Stack:** Typer (CLI framework), pytest + typer.testing.CliRunner

---

### Task 1: Create `tag` command group

**Files:**
- Modify: `src/mtk/cli/main.py` — lines 994-1060 (old `tag` command), 1289-1325 (`list_tags`), 1329-1424 (`tag_batch`)
- Modify: `tests/test_cli.py` — lines 84-91 (`TestTagCommand`)

**Step 1: Write failing tests for new tag subcommands**

In `tests/test_cli.py`, replace `TestTagCommand` (line 84-91) with:

```python
class TestTagCommand:
    """Tests for the tag command group."""

    def test_tag_help(self) -> None:
        """tag group should have help."""
        result = runner.invoke(app, ["tag", "--help"])
        assert result.exit_code == 0
        assert "add" in result.output
        assert "remove" in result.output
        assert "list" in result.output
        assert "batch" in result.output

    def test_tag_add_help(self) -> None:
        result = runner.invoke(app, ["tag", "add", "--help"])
        assert result.exit_code == 0

    def test_tag_remove_help(self) -> None:
        result = runner.invoke(app, ["tag", "remove", "--help"])
        assert result.exit_code == 0

    def test_tag_list_help(self) -> None:
        result = runner.invoke(app, ["tag", "list", "--help"])
        assert result.exit_code == 0

    def test_tag_batch_help(self) -> None:
        result = runner.invoke(app, ["tag", "batch", "--help"])
        assert result.exit_code == 0
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py::TestTagCommand -v`
Expected: FAIL — `tag` is still a command, not a group, so `tag add --help` etc. will fail.

**Step 3: Rewrite tag commands in `main.py`**

Replace the three scattered commands with a `tag_app` Typer sub-app. Key changes:

1. Create `tag_app = typer.Typer(help="Manage email tags")` and `app.add_typer(tag_app, name="tag")`
2. Move old `tag()` function logic into two new functions:
   - `tag_add(message_id: str, tags: list[str], json: bool)` — registered as `@tag_app.command("add")`
   - `tag_remove(message_id: str, tags: list[str], json: bool)` — registered as `@tag_app.command("remove")`
   - Tags become `tags: list[str] = typer.Argument(..., help="Tag names to add/remove")`
3. Move `list_tags()` → `@tag_app.command("list")`
4. Move `tag_batch()` → `@tag_app.command("batch")`

The IMAP push queueing logic stays in `tag_add` and `tag_remove`. The `tag_batch` function keeps its `--add`/`--remove` option flags (unchanged interface except the command path).

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py::TestTagCommand -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `pytest --tb=short -q`
Expected: All 332 tests pass.

**Step 6: Commit**

```bash
git add src/mtk/cli/main.py tests/test_cli.py
git commit -m "Reorganize tag commands into command group"
```

---

### Task 2: Create `rebuild` command group

**Files:**
- Modify: `src/mtk/cli/main.py` — lines 937-990 (`rebuild_index`, `rebuild_threads`)
- Modify: `tests/test_cli.py`

**Step 1: Write failing tests for new rebuild subcommands**

Add to `tests/test_cli.py`:

```python
class TestRebuildCommand:
    """Tests for the rebuild command group."""

    def test_rebuild_help(self) -> None:
        result = runner.invoke(app, ["rebuild", "--help"])
        assert result.exit_code == 0
        assert "index" in result.output
        assert "threads" in result.output

    def test_rebuild_index_help(self) -> None:
        result = runner.invoke(app, ["rebuild", "index", "--help"])
        assert result.exit_code == 0

    def test_rebuild_threads_help(self) -> None:
        result = runner.invoke(app, ["rebuild", "threads", "--help"])
        assert result.exit_code == 0
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py::TestRebuildCommand -v`
Expected: FAIL — no `rebuild` group yet.

**Step 3: Create rebuild sub-app in `main.py`**

1. Create `rebuild_app = typer.Typer(help="Rebuild indexes and threads")` and `app.add_typer(rebuild_app, name="rebuild")`
2. Change `@app.command("rebuild-index")` → `@rebuild_app.command("index")`
3. Change `@app.command("rebuild-threads")` → `@rebuild_app.command("threads")`

No logic changes at all — just re-register the existing functions under the new sub-app.

**Step 4: Run tests**

Run: `pytest --tb=short -q`
Expected: All tests pass (332 + new tests).

**Step 5: Commit**

```bash
git add src/mtk/cli/main.py tests/test_cli.py
git commit -m "Reorganize rebuild commands into command group"
```

---

### Task 3: Update SPEC.md and CLAUDE.md references

**Files:**
- Modify: `SPEC.md` — lines 249-250, 400
- Modify: `CLAUDE.md`

**Step 1: Update old command references**

In `SPEC.md`:
- `mtk tag-batch <query> --add TAG` → `mtk tag batch <query> --add TAG`
- `mtk list-tags [--tree]` → `mtk tag list`
- `mtk tag-batch --stdin` → `mtk tag batch --stdin`

In `CLAUDE.md`, update CLI command list if tag/rebuild are mentioned.

**Step 2: Commit**

```bash
git add SPEC.md CLAUDE.md
git commit -m "Update docs for CLI reorganization"
```

---

### Task 4: Final verification

**Step 1: Run full test suite + lint**

```bash
pytest --tb=short -q
ruff check src/mtk tests
ruff format --check src/mtk tests
```

Expected: All pass, no lint issues, no format issues.
