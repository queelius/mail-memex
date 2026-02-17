"""LLM integration for mtk.

Provides email classification, summarization, and analysis using LLMs.
Supports Ollama for local inference.
"""

from mtk.llm.classifier import ClassificationResult, EmailClassifier
from mtk.llm.providers import LLMProvider, OllamaProvider

__all__ = [
    "LLMProvider",
    "OllamaProvider",
    "EmailClassifier",
    "ClassificationResult",
]
