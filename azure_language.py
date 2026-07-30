from __future__ import annotations

import os

from azure.ai.textanalytics import ExtractiveSummaryAction, TextAnalyticsClient
from azure.identity import DefaultAzureCredential

from benchmark import PiiResult


class AzureLanguageService:
    def __init__(
        self,
        pii_client: TextAnalyticsClient,
        summary_client: TextAnalyticsClient,
    ) -> None:
        self._pii_client = pii_client
        self._summary_client = summary_client

    @classmethod
    def from_environment(cls) -> "AzureLanguageService":
        endpoint = os.getenv("LANGUAGE_ENDPOINT", "").rstrip("/")
        if not endpoint:
            raise RuntimeError("LANGUAGE_ENDPOINT is required.")
        if "/api/projects/" in endpoint:
            raise RuntimeError(
                "LANGUAGE_ENDPOINT must be the root Language resource endpoint."
            )
        credential = DefaultAzureCredential()
        return cls(
            TextAnalyticsClient(endpoint, credential),
            TextAnalyticsClient(endpoint, credential),
        )

    def detect_pii(self, text: str) -> PiiResult:
        result = self._pii_client.recognize_pii_entities([text])[0]
        if result.is_error:
            raise RuntimeError(f"PII detection failed: {result.message}")
        categories = tuple(sorted({entity.category for entity in result.entities}))
        return PiiResult(
            redacted_text=result.redacted_text,
            categories=categories,
        )

    def summarize(self, text: str) -> str:
        pages = self._summary_client.begin_analyze_actions(
            [text],
            actions=[ExtractiveSummaryAction(max_sentence_count=3)],
        ).result()
        result = next(pages)[0]
        if result.is_error:
            raise RuntimeError(f"Extractive summarization failed: {result.message}")
        sentences = [sentence.text for sentence in result.sentences]
        return " ".join(sentences) or "No extractive summary was returned."

    def close(self) -> None:
        self._pii_client.close()
        self._summary_client.close()
