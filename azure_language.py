from __future__ import annotations

import os

from azure.ai.textanalytics import TextAnalyticsClient
from azure.identity import DefaultAzureCredential

from benchmark import PiiResult

MAX_SENTENCE_COUNT = 3

# The SDK default is 5 seconds, which would quantise every summarization
# measurement to multiples of the poll cadence rather than reflecting service
# latency. The speculative-parallel pipeline puts two summarizations on its
# critical path, so it would pay that artefact twice.
DEFAULT_POLLING_INTERVAL_SECONDS = 1


class AzureLanguageService:
    def __init__(
        self,
        pii_client: TextAnalyticsClient,
        summary_client: TextAnalyticsClient,
        credential=None,
        polling_interval: int = DEFAULT_POLLING_INTERVAL_SECONDS,
    ) -> None:
        self._pii_client = pii_client
        self._summary_client = summary_client
        self._credential = credential
        self._polling_interval = polling_interval

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
            credential=credential,
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
        poller = self._summary_client.begin_extract_summary(
            [text],
            max_sentence_count=MAX_SENTENCE_COUNT,
            polling_interval=self._polling_interval,
        )
        result = next(iter(poller.result()))
        if result.is_error:
            raise RuntimeError(f"Extractive summarization failed: {result.message}")
        sentences = [sentence.text for sentence in result.sentences]
        return " ".join(sentences) or "No extractive summary was returned."

    def close(self) -> None:
        self._pii_client.close()
        self._summary_client.close()
        if self._credential is not None:
            self._credential.close()
