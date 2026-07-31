from __future__ import annotations

import os
from typing import Any

from azure.ai.language.conversations import ConversationAnalysisClient
from azure.identity import DefaultAzureCredential

from benchmark import PiiResult
from conversation import Conversation, Turn

SUMMARY_ASPECTS = ("issue", "resolution")

# The SDK's default api-version (2023-04-01) predates ConversationalPIITask;
# 2024-05-01 is the GA version that supports both tasks.
API_VERSION = "2024-05-01"

# The SDK default is 5 seconds, which would quantise every measurement to the
# poll cadence rather than reflecting service latency. Both conversation tasks
# are long-running jobs, so the speculative-parallel pipeline would pay that
# artefact on every operation.
DEFAULT_POLLING_INTERVAL_SECONDS = 1


def _analysis_input(conv: Conversation) -> dict[str, Any]:
    return {
        "conversations": [
            {
                "id": "1",
                "language": "en",
                "modality": "text",
                "conversationItems": conv.to_conversation_items(),
            }
        ]
    }


class AzureLanguageService:
    def __init__(
        self,
        pii_client: ConversationAnalysisClient,
        summary_client: ConversationAnalysisClient,
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
            ConversationAnalysisClient(endpoint, credential, api_version=API_VERSION),
            ConversationAnalysisClient(endpoint, credential, api_version=API_VERSION),
            credential=credential,
        )

    def _run_task(
        self,
        client: ConversationAnalysisClient,
        conv: Conversation,
        task: dict[str, Any],
    ) -> dict[str, Any]:
        poller = client.begin_conversation_analysis(
            task={"analysisInput": _analysis_input(conv), "tasks": [task]},
            polling_interval=self._polling_interval,
        )
        item = poller.result()["tasks"]["items"][0]
        if item.get("status") != "succeeded":
            errors = item.get("errors") or [item.get("status", "unknown status")]
            raise RuntimeError(f"{task['kind']} failed: {errors}")
        return item["results"]["conversations"][0]

    def detect_pii(self, conv: Conversation) -> PiiResult:
        result = self._run_task(
            self._pii_client,
            conv,
            {"taskName": "pii", "kind": "ConversationalPIITask", "parameters": {}},
        )
        items = result["conversationItems"]
        if len(items) != len(conv.turns):
            raise RuntimeError(
                "Conversation PII returned a different number of items than sent."
            )
        redacted = Conversation(
            tuple(
                Turn(role=turn.role, text=item["redactedContent"]["text"])
                for turn, item in zip(conv.turns, items)
            )
        )
        categories = tuple(
            sorted(
                {
                    entity["category"]
                    for item in items
                    for entity in item.get("entities", [])
                }
            )
        )
        return PiiResult(redacted=redacted, categories=categories)

    def summarize(self, conv: Conversation) -> str:
        result = self._run_task(
            self._summary_client,
            conv,
            {
                "taskName": "summary",
                "kind": "ConversationalSummarizationTask",
                "parameters": {"summaryAspects": list(SUMMARY_ASPECTS)},
            },
        )
        parts = [
            f"{summary['aspect'].capitalize()}: {summary['text']}"
            for summary in result.get("summaries", [])
        ]
        return " ".join(parts) or "No conversation summary was returned."

    def close(self) -> None:
        self._pii_client.close()
        self._summary_client.close()
        if self._credential is not None:
            self._credential.close()
