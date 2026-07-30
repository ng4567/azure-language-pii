import unittest
from types import SimpleNamespace

from azure_language import DEFAULT_POLLING_INTERVAL_SECONDS, AzureLanguageService


class FakePiiClient:
    def __init__(self):
        self.closed = False

    def recognize_pii_entities(self, documents):
        return [
            SimpleNamespace(
                is_error=False,
                redacted_text="Contact *****.",
                entities=[
                    SimpleNamespace(category="Person"),
                    SimpleNamespace(category="Person"),
                ],
            )
        ]

    def close(self):
        self.closed = True


class FakeSummaryClient:
    def __init__(self, result=None):
        self.calls = []
        self.closed = False
        self._result = result or SimpleNamespace(
            is_error=False,
            sentences=[
                SimpleNamespace(text="First sentence."),
                SimpleNamespace(text="Second sentence."),
            ],
        )

    def begin_extract_summary(self, documents, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(result=lambda: [self._result])

    def close(self):
        self.closed = True


class FakeCredential:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class AzureLanguageServiceTests(unittest.TestCase):
    def test_detect_pii_returns_redaction_and_unique_categories_without_values(self):
        service = AzureLanguageService(FakePiiClient(), FakeSummaryClient())

        result = service.detect_pii("Contact Alice.")

        self.assertEqual(result.redacted_text, "Contact *****.")
        self.assertEqual(result.categories, ("Person",))
        self.assertFalse(hasattr(result, "entities"))

    def test_summarize_combines_extractive_sentences(self):
        service = AzureLanguageService(FakePiiClient(), FakeSummaryClient())

        result = service.summarize("Long text")

        self.assertEqual(result, "First sentence. Second sentence.")

    def test_summarize_overrides_the_five_second_sdk_polling_default(self):
        summary_client = FakeSummaryClient()
        service = AzureLanguageService(FakePiiClient(), summary_client)

        service.summarize("Long text")

        self.assertEqual(
            summary_client.calls[0]["polling_interval"],
            DEFAULT_POLLING_INTERVAL_SECONDS,
        )
        self.assertLess(DEFAULT_POLLING_INTERVAL_SECONDS, 5)

    def test_summarize_raises_on_a_document_error(self):
        summary_client = FakeSummaryClient(
            SimpleNamespace(is_error=True, message="document too long")
        )
        service = AzureLanguageService(FakePiiClient(), summary_client)

        with self.assertRaisesRegex(RuntimeError, "document too long"):
            service.summarize("Long text")

    def test_close_releases_both_clients_and_the_credential(self):
        pii_client = FakePiiClient()
        summary_client = FakeSummaryClient()
        credential = FakeCredential()
        service = AzureLanguageService(pii_client, summary_client, credential)

        service.close()

        self.assertTrue(pii_client.closed)
        self.assertTrue(summary_client.closed)
        self.assertTrue(credential.closed)

    def test_close_tolerates_an_injected_service_without_a_credential(self):
        service = AzureLanguageService(FakePiiClient(), FakeSummaryClient())

        service.close()


if __name__ == "__main__":
    unittest.main()
