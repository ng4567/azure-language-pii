
import os
import sys

from azure.ai.language.conversations import ConversationAnalysisClient
from azure.ai.textanalytics import ExtractiveSummaryAction, TextAnalyticsClient
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

TEXT = "hello, world"


def require_endpoint() -> str:
    endpoint = os.getenv("LANGUAGE_ENDPOINT", "").rstrip("/")
    if not endpoint:
        raise RuntimeError("LANGUAGE_ENDPOINT is required.")
    if "/api/projects/" in endpoint:
        raise RuntimeError(
            "LANGUAGE_ENDPOINT must be the root Language resource endpoint, not a project endpoint."
        )
    return endpoint


def conversational_summary(client: ConversationAnalysisClient) -> None:
    poller = client.begin_conversation_analysis(
        {
            "displayName": "Hello world conversational summary",
            "analysisInput": {
                "conversations": [
                    {
                        "id": "hello-world",
                        "language": "en",
                        "modality": "text",
                        "conversationItems": [
                            {
                                "id": "1",
                                "participantId": "user",
                                "role": "Customer",
                                "text": TEXT,
                            }
                        ],
                    }
                ]
            },
            "tasks": [
                {
                    "taskName": "issue-summary",
                    "kind": "ConversationalSummarizationTask",
                    "parameters": {"summaryAspects": ["issue"]},
                }
            ],
        }
    )
    result = poller.result()
    task_result = result["tasks"]["items"][0]["results"]
    if task_result["errors"]:
        raise RuntimeError(f"Conversational Summarization failed: {task_result['errors']}")

    print("Conversational Summarization:")
    for summary in task_result["conversations"][0]["summaries"]:
        print(f"- {summary['aspect']}: {summary['text']}")


def extractive_summary(client: TextAnalyticsClient) -> None:
    result = next(
        client.begin_analyze_actions(
            [TEXT],
            actions=[ExtractiveSummaryAction(max_sentence_count=1)],
        ).result()
    )[0]
    if result.is_error:
        raise RuntimeError(f"Extractive Summarization failed: {result.message}")

    print("\nExtractive Summarization:")
    for sentence in result.sentences:
        print(f"- {sentence.text}")


def pii_detection(client: TextAnalyticsClient) -> None:
    result = client.recognize_pii_entities([TEXT])[0]
    if result.is_error:
        raise RuntimeError(f"PII detection failed: {result.message}")

    print("\nPII Detection:")
    print(f"- Redacted text: {result.redacted_text}")
    for entity in result.entities:
        print(
            f"- {entity.category}: {entity.text} "
            f"(confidence: {entity.confidence_score:.2f})"
        )


def main() -> None:
    load_dotenv()
    endpoint = require_endpoint()
    credential = DefaultAzureCredential()

    with ConversationAnalysisClient(endpoint, credential) as conversation_client:
        conversational_summary(conversation_client)
    with TextAnalyticsClient(endpoint, credential) as text_client:
        extractive_summary(text_client)
        pii_detection(text_client)


if __name__ == "__main__":
    try:
        main()
    except (HttpResponseError, RuntimeError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error

