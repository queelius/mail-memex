"""LLM provider implementations.

Supports local inference via Ollama.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol


class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        """Get completion from LLM.

        Args:
            prompt: The prompt to complete.
            max_tokens: Maximum tokens in response.

        Returns:
            The LLM's response text.
        """
        ...

    def is_available(self) -> bool:
        """Check if the provider is available.

        Returns:
            True if the provider can be used.
        """
        ...


class OllamaProvider:
    """Ollama provider for local LLM inference.

    Requires Ollama to be running locally (default: http://localhost:11434).

    Usage:
        provider = OllamaProvider(model="llama3.2")
        if provider.is_available():
            response = provider.complete("Summarize this email: ...")
    """

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
        timeout: float = 60.0,
    ) -> None:
        """Initialize Ollama provider.

        Args:
            model: Model name to use (default: llama3.2).
            base_url: Ollama API base URL.
            timeout: Request timeout in seconds.
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def is_available(self) -> bool:
        """Check if Ollama is running and model is available."""
        try:
            import httpx

            response = httpx.get(
                f"{self.base_url}/api/tags",
                timeout=5.0,
            )
            if response.status_code != 200:
                return False

            # Check if our model is available
            data = response.json()
            models = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
            return self.model in models or f"{self.model}:latest" in [
                m.get("name", "") for m in data.get("models", [])
            ]

        except Exception:
            return False

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        """Get completion from Ollama.

        Args:
            prompt: The prompt to complete.
            max_tokens: Maximum tokens in response.

        Returns:
            The model's response text.

        Raises:
            RuntimeError: If Ollama request fails.
        """
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx package required. Install with: pip install httpx")

        try:
            response = httpx.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "num_predict": max_tokens,
                    },
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json().get("response", "").strip()

        except httpx.TimeoutException:
            raise RuntimeError(f"Ollama request timed out after {self.timeout}s")
        except httpx.HTTPError as e:
            raise RuntimeError(f"Ollama request failed: {e}")

    def list_models(self) -> list[str]:
        """List available models in Ollama.

        Returns:
            List of model names.
        """
        try:
            import httpx

            response = httpx.get(
                f"{self.base_url}/api/tags",
                timeout=5.0,
            )
            response.raise_for_status()
            data = response.json()
            return [m.get("name", "") for m in data.get("models", [])]

        except Exception:
            return []
