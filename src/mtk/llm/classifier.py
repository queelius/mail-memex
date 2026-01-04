"""Email classification and analysis using LLMs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mtk.core.models import Email
    from mtk.llm.providers import LLMProvider


@dataclass
class ClassificationResult:
    """Result of classifying an email."""

    message_id: str
    category: str
    confidence: str = "unknown"  # high, medium, low
    reasoning: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "message_id": self.message_id,
            "category": self.category,
        }
        if self.confidence != "unknown":
            result["confidence"] = self.confidence
        if self.reasoning:
            result["reasoning"] = self.reasoning
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class SummaryResult:
    """Result of summarizing an email or thread."""

    message_id: str
    summary: str
    key_points: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "message_id": self.message_id,
            "summary": self.summary,
        }
        if self.key_points:
            result["key_points"] = self.key_points
        if self.action_items:
            result["action_items"] = self.action_items
        if self.error:
            result["error"] = self.error
        return result


class EmailClassifier:
    """Classify and analyze emails using LLMs.

    Usage:
        from mtk.llm import OllamaProvider, EmailClassifier

        provider = OllamaProvider(model="llama3.2")
        classifier = EmailClassifier(provider)

        result = classifier.classify(email, ["work", "personal", "newsletter"])
        summary = classifier.summarize(email)
    """

    def __init__(self, provider: LLMProvider) -> None:
        """Initialize classifier with an LLM provider.

        Args:
            provider: LLM provider to use for inference.
        """
        self.provider = provider

    def classify(
        self,
        email: Email,
        categories: list[str],
        include_reasoning: bool = False,
    ) -> ClassificationResult:
        """Classify an email into one of the given categories.

        Args:
            email: Email to classify.
            categories: List of category names.
            include_reasoning: Whether to include explanation.

        Returns:
            ClassificationResult with the predicted category.
        """
        prompt = self._build_classification_prompt(email, categories, include_reasoning)

        try:
            response = self.provider.complete(prompt, max_tokens=200)
            return self._parse_classification_response(
                email.message_id, response, categories
            )
        except Exception as e:
            return ClassificationResult(
                message_id=email.message_id,
                category="error",
                error=str(e),
            )

    def summarize(self, email: Email) -> SummaryResult:
        """Summarize an email.

        Args:
            email: Email to summarize.

        Returns:
            SummaryResult with summary and key points.
        """
        prompt = self._build_summary_prompt(email)

        try:
            response = self.provider.complete(prompt, max_tokens=300)
            return self._parse_summary_response(email.message_id, response)
        except Exception as e:
            return SummaryResult(
                message_id=email.message_id,
                summary="",
                error=str(e),
            )

    def extract_actions(self, email: Email) -> list[str]:
        """Extract action items from an email.

        Args:
            email: Email to analyze.

        Returns:
            List of action items.
        """
        prompt = f"""Extract action items from this email. List only concrete tasks that need to be done.
If there are no action items, respond with "None".

From: {email.from_name or email.from_addr}
Subject: {email.subject}

{(email.body_text or "")[:2000]}

Action items (one per line):"""

        try:
            response = self.provider.complete(prompt, max_tokens=200)
            if "none" in response.lower():
                return []
            return [
                line.strip().lstrip("•-*123456789. ")
                for line in response.strip().split("\n")
                if line.strip() and not line.lower().startswith("none")
            ]
        except Exception:
            return []

    def suggest_reply(self, email: Email, tone: str = "professional") -> str:
        """Suggest a reply to an email.

        Args:
            email: Email to reply to.
            tone: Tone of the reply (professional, casual, formal).

        Returns:
            Suggested reply text.
        """
        prompt = f"""Write a {tone} reply to this email.

From: {email.from_name or email.from_addr}
Subject: {email.subject}

{(email.body_text or "")[:1500]}

Reply:"""

        try:
            return self.provider.complete(prompt, max_tokens=400)
        except Exception:
            return ""

    def _build_classification_prompt(
        self,
        email: Email,
        categories: list[str],
        include_reasoning: bool,
    ) -> str:
        """Build prompt for classification."""
        categories_str = ", ".join(categories)

        prompt = f"""Classify this email into exactly one of these categories: {categories_str}

From: {email.from_name or email.from_addr}
Subject: {email.subject}

{(email.body_text or "")[:1500]}

"""
        if include_reasoning:
            prompt += "Respond with the category name followed by a brief explanation.\nCategory:"
        else:
            prompt += "Respond with only the category name, nothing else.\nCategory:"

        return prompt

    def _parse_classification_response(
        self,
        message_id: str,
        response: str,
        categories: list[str],
    ) -> ClassificationResult:
        """Parse LLM response into ClassificationResult."""
        response = response.strip()

        # Check if response matches a category
        response_lower = response.lower()
        for cat in categories:
            if cat.lower() in response_lower:
                # Extract reasoning if present
                reasoning = ""
                if ":" in response or "because" in response_lower:
                    parts = response.split(":", 1) if ":" in response else response.split("because", 1)
                    if len(parts) > 1:
                        reasoning = parts[1].strip()

                return ClassificationResult(
                    message_id=message_id,
                    category=cat,
                    confidence="high" if response_lower.startswith(cat.lower()) else "medium",
                    reasoning=reasoning,
                )

        # No match found
        return ClassificationResult(
            message_id=message_id,
            category=response.split()[0] if response else "unknown",
            confidence="low",
            reasoning=f"Model response did not match categories: {response}",
        )

    def _build_summary_prompt(self, email: Email) -> str:
        """Build prompt for summarization."""
        return f"""Summarize this email in 1-2 sentences. Then list any key points or action items.

From: {email.from_name or email.from_addr}
Subject: {email.subject}

{(email.body_text or "")[:2000]}

Summary:"""

    def _parse_summary_response(
        self,
        message_id: str,
        response: str,
    ) -> SummaryResult:
        """Parse LLM response into SummaryResult."""
        lines = response.strip().split("\n")

        # First line(s) are the summary
        summary_lines = []
        key_points = []
        action_items = []

        current_section = "summary"
        for line in lines:
            line = line.strip()
            if not line:
                continue

            lower = line.lower()
            if "key point" in lower or "main point" in lower:
                current_section = "key_points"
                continue
            elif "action" in lower or "to do" in lower or "task" in lower:
                current_section = "actions"
                continue

            if current_section == "summary":
                summary_lines.append(line)
            elif current_section == "key_points":
                key_points.append(line.lstrip("•-*123456789. "))
            elif current_section == "actions":
                action_items.append(line.lstrip("•-*123456789. "))

        return SummaryResult(
            message_id=message_id,
            summary=" ".join(summary_lines),
            key_points=key_points,
            action_items=action_items,
        )
