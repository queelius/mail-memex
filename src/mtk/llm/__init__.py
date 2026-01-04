"""LLM integration for mtk.

Provides email classification, summarization, and analysis using LLMs.
Supports Ollama for local inference.
"""

from mtk.llm.providers import LLMProvider, OllamaProvider
from mtk.llm.classifier import EmailClassifier, ClassificationResult

__all__ = [
    "LLMProvider",
    "OllamaProvider",
    "EmailClassifier",
    "ClassificationResult",
]
