import unittest
from types import SimpleNamespace

from azure_language import DEFAULT_POLLING_INTERVAL_SECONDS, AzureLanguageService
from conversation import Conversation, Turn


def _conversation():
    return Conversation(
        (
            Turn("Agent", "Who am I speaking with?"),
            Turn("Customer", "This is Alice."),
        )
    )


def _pii_job(items):
    return {
        "tasks": {
            "items": [
                {
                    "status": "succeeded",
                    "results": {"conversations": [{"conversationItems": items}]},
                }
            ]
        }
    }


def _summary_job(summaries):
    return {
        "tasks": {
            "items": [
                {
                    "status": "succeeded",
                    "results": {"conversations": [{"summaries": summaries}]},
                }
            ]
        }
    }


class FakeConversationClient:
    def __init__(self, job):
        self.job = job
        self.tasks = []
        self.kwargs = []
        self.closed = False

    def begin_conversation_analysis(self, task, **kwargs):
        self.tasks.append(task)
        self.kwargs.append(kwargs)
        return SimpleNamespace(result=lambda: self.job)

    def close(self):
        self.closed = True


class FakeCredential:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _default_pii_client():
    return FakeConversationClient(
        _pii_job(
            [
                {
                    "id": "1",
                    "redactedContent": {"text": "Who am I speaking with?"},
                    "entities": [],
                },
                {
                    "id": "2",
                    "redactedContent": {"text": "This is *****."},
                    "entities": [{"category": "Person"}, {"category": "Person"}],
                },
            ]
        )
    )


def _default_summary_client():
    return FakeConversationClient(
        _summary_job(
            [
                {"aspect": "issue", "text": "Customer identity was requested."},
                {"aspect": "resolution", "text": "The customer identified herself."},
            ]
        )
    )


class AzureLanguageServiceTests(unittest.TestCase):
    def test_detect_pii_redacts_turns_and_reports_unique_categories(self):
        client = _default_pii_client()
        service = AzureLanguageService(client, _default_summary_client())

        result = service.detect_pii(_conversation())

        self.assertEqual(
            result.redacted,
            Conversation(
                (
                    Turn("Agent", "Who am I speaking with?"),
                    Turn("Customer", "This is *****."),
                )
            ),
        )
        self.assertEqual(
            result.redacted_text,
            "Agent: Who am I speaking with?\nCustomer: This is *****.",
        )
        self.assertEqual(result.categories, ("Person",))

    def test_detect_pii_submits_a_conversational_pii_task(self):
        client = _default_pii_client()
        service = AzureLanguageService(client, _default_summary_client())

        service.detect_pii(_conversation())

        task = client.tasks[0]
        self.assertEqual(task["tasks"][0]["kind"], "ConversationalPIITask")
        items = task["analysisInput"]["conversations"][0]["conversationItems"]
        self.assertEqual([item["id"] for item in items], ["1", "2"])
        self.assertEqual([item["role"] for item in items], ["Agent", "Customer"])

    def test_detect_pii_rejects_a_mismatched_item_count(self):
        client = FakeConversationClient(
            _pii_job(
                [{"id": "1", "redactedContent": {"text": "Only one."}, "entities": []}]
            )
        )
        service = AzureLanguageService(client, _default_summary_client())

        with self.assertRaisesRegex(RuntimeError, "different number of items"):
            service.detect_pii(_conversation())

    def test_summarize_joins_the_issue_and_resolution_aspects(self):
        service = AzureLanguageService(
            _default_pii_client(), _default_summary_client()
        )

        result = service.summarize(_conversation())

        self.assertEqual(
            result,
            "Issue: Customer identity was requested. "
            "Resolution: The customer identified herself.",
        )

    def test_summarize_submits_a_conversational_summarization_task(self):
        client = _default_summary_client()
        service = AzureLanguageService(_default_pii_client(), client)

        service.summarize(_conversation())

        task = client.tasks[0]["tasks"][0]
        self.assertEqual(task["kind"], "ConversationalSummarizationTask")
        self.assertEqual(
            task["parameters"]["summaryAspects"], ["issue", "resolution"]
        )

    def test_both_tasks_override_the_five_second_sdk_polling_default(self):
        pii_client = _default_pii_client()
        summary_client = _default_summary_client()
        service = AzureLanguageService(pii_client, summary_client)

        service.detect_pii(_conversation())
        service.summarize(_conversation())

        for client in (pii_client, summary_client):
            self.assertEqual(
                client.kwargs[0]["polling_interval"],
                DEFAULT_POLLING_INTERVAL_SECONDS,
            )
        self.assertLess(DEFAULT_POLLING_INTERVAL_SECONDS, 5)

    def test_a_failed_task_raises_with_its_errors(self):
        client = FakeConversationClient(
            {
                "tasks": {
                    "items": [
                        {
                            "status": "failed",
                            "errors": [{"code": "InvalidRequest"}],
                        }
                    ]
                }
            }
        )
        service = AzureLanguageService(client, _default_summary_client())

        with self.assertRaisesRegex(RuntimeError, "InvalidRequest"):
            service.detect_pii(_conversation())

    def test_close_releases_both_clients_and_the_credential(self):
        pii_client = _default_pii_client()
        summary_client = _default_summary_client()
        credential = FakeCredential()
        service = AzureLanguageService(pii_client, summary_client, credential)

        service.close()

        self.assertTrue(pii_client.closed)
        self.assertTrue(summary_client.closed)
        self.assertTrue(credential.closed)

    def test_close_tolerates_an_injected_service_without_a_credential(self):
        service = AzureLanguageService(
            _default_pii_client(), _default_summary_client()
        )

        service.close()


if __name__ == "__main__":
    unittest.main()
