import unittest
from types import SimpleNamespace

from azure_language import AzureLanguageService


class FakePiiClient:
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


class FakeSummaryClient:
    def begin_analyze_actions(self, documents, actions):
        result = SimpleNamespace(
            is_error=False,
            sentences=[
                SimpleNamespace(text="First sentence."),
                SimpleNamespace(text="Second sentence."),
            ],
        )
        return SimpleNamespace(result=lambda: iter([[result]]))


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


if __name__ == "__main__":
    unittest.main()
