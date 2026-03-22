"""Bidirectional mapping between IMAP flags/Gmail labels and mtk tags."""

from __future__ import annotations

from dataclasses import dataclass, field

# Standard IMAP flag → mtk tag mappings
_IMAP_FLAG_TO_TAG: dict[str, str] = {
    "\\Seen": "read",
    "\\Flagged": "flagged",
    "\\Answered": "replied",
    "\\Draft": "draft",
    "\\Deleted": "deleted",
}

# Gmail label → mtk tag mappings
_GMAIL_LABEL_TO_TAG: dict[str, str] = {
    "\\Inbox": "inbox",
    "\\Sent": "sent",
    "\\Draft": "draft",
    "\\Spam": "spam",
    "\\Trash": "trash",
    "\\Starred": "starred",
    "\\Important": "important",
    "CATEGORY_PERSONAL": "personal",
    "CATEGORY_SOCIAL": "social",
    "CATEGORY_PROMOTIONS": "promotions",
    "CATEGORY_UPDATES": "updates",
    "CATEGORY_FORUMS": "forums",
}


@dataclass
class TagMapper:
    """Bidirectional mapper between IMAP flags/Gmail labels and mtk tags.

    Args:
        is_gmail: Whether to use Gmail label mapping in addition to IMAP flags.
        custom_mappings: Additional custom mappings (imap_value → mtk_tag).
    """

    is_gmail: bool = False
    custom_mappings: dict[str, str] = field(default_factory=dict)

    def imap_to_tags(self, flags: list[str], labels: list[str] | None = None) -> set[str]:
        """Convert IMAP flags (and optionally Gmail labels) to mtk tags.

        Args:
            flags: IMAP flags (e.g., ["\\Seen", "\\Flagged"]).
            labels: Gmail X-GM-LABELS (only used if is_gmail=True).

        Returns:
            Set of mtk tag names.
        """
        tags: set[str] = set()

        # Standard IMAP flags
        for flag in flags:
            flag_str = flag.decode() if isinstance(flag, bytes) else str(flag)
            tag = _IMAP_FLAG_TO_TAG.get(flag_str)
            if tag:
                tags.add(tag)
            # Check custom mappings
            custom_tag = self.custom_mappings.get(flag_str)
            if custom_tag:
                tags.add(custom_tag)

        # Gmail labels
        if self.is_gmail and labels:
            for label in labels:
                label_str = label.decode() if isinstance(label, bytes) else str(label)
                tag = _GMAIL_LABEL_TO_TAG.get(label_str)
                if tag:
                    tags.add(tag)
                else:
                    # Pass through unknown Gmail labels as-is (lowercased)
                    tags.add(label_str.lower().replace(" ", "-"))

        return tags
