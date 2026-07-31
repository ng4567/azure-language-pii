import unittest
import warnings

from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=StarletteDeprecationWarning,
)
from azure.core.exceptions import ServiceRequestError
from fastapi.testclient import TestClient

from benchmark import PiiResult
from conversation import MAX_TURN_LENGTH, Conversation, Turn
from main import MAX_TEXT_LENGTH, create_app
from pricing import PricingUnavailableError, parse_retail_rates


def _turns(*texts, role="Customer"):
    return [{"role": role, "text": text} for text in texts]


class FakeLanguageService:
    def __init__(self):
        self.pii_calls = []
        self.summary_calls = []

    def detect_pii(self, conv):
        self.pii_calls.append(conv)
        if "Alice" in conv.as_text():
            redacted = Conversation(
                tuple(
                    Turn(turn.role, turn.text.replace("Alice", "*****"))
                    for turn in conv.turns
                )
            )
            return PiiResult(redacted, ("Person",))
        return PiiResult(conv, ())

    def summarize(self, conv):
        self.summary_calls.append(conv)
        return f"Summary of {conv.as_text()}"

    def close(self):
        pass


class FakePricing:
    def __init__(self, error=None):
        self.error = error

    def get_rates(self):
        if self.error:
            raise self.error
        return parse_retail_rates(
            [
                {
                    "meterName": "Standard Text Records",
                    "retailPrice": 1.0,
                    "unitOfMeasure": "1K",
                    "effectiveStartDate": "2020-12-01T00:00:00Z",
                },
                {
                    "meterName": "Standard Summarization Text Records",
                    "retailPrice": 2.0,
                    "unitOfMeasure": "1K",
                    "effectiveStartDate": "2023-05-01T00:00:00Z",
                },
            ],
            "eastus2",
        )


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.language = FakeLanguageService()
        self.client = TestClient(create_app(self.language, FakePricing()))

    def test_samples_are_equal_length(self):
        response = self.client.get("/api/samples")

        self.assertEqual(response.status_code, 200)
        samples = response.json()["samples"]
        self.assertEqual(samples["pii"]["characters"], samples["no_pii"]["characters"])

    def test_samples_are_structured_transcripts(self):
        samples = self.client.get("/api/samples").json()["samples"]

        for sample in samples.values():
            self.assertGreater(len(sample["conversation"]), 1)
            for turn in sample["conversation"]:
                self.assertIn(turn["role"], ("Agent", "Customer"))
                self.assertTrue(turn["text"])

    def test_static_pages_are_served(self):
        for path in ("/", "/api-docs", "/analysis"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])

    def test_health_check_does_not_call_azure(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_config_exposes_the_limits_the_ui_needs(self):
        response = self.client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["max_text_length"], MAX_TEXT_LENGTH)
        self.assertEqual(response.json()["max_turn_length"], MAX_TURN_LENGTH)
        self.assertEqual(response.json()["roles"], ["Agent", "Customer"])
        self.assertGreaterEqual(response.json()["max_iterations"], 1)

    def test_rejects_an_empty_conversation_and_blank_turns(self):
        empty = self.client.post("/api/benchmark", json={"conversation": []})
        blank = self.client.post(
            "/api/benchmark", json={"conversation": _turns("   ")}
        )

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(blank.status_code, 400)
        self.assertIn("Turn 1", blank.json()["detail"])

    def test_rejects_an_unknown_role(self):
        response = self.client.post(
            "/api/benchmark",
            json={"conversation": [{"role": "Bot", "text": "Hello."}]},
        )

        self.assertEqual(response.status_code, 422)

    def test_rejects_a_turn_over_the_per_turn_limit(self):
        response = self.client.post(
            "/api/benchmark",
            json={"conversation": _turns("x" * (MAX_TURN_LENGTH + 1))},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("per turn", response.json()["detail"])

    def test_rejects_a_transcript_over_the_total_limit(self):
        turns = _turns(*["x" * MAX_TURN_LENGTH] * 6)

        response = self.client.post("/api/benchmark", json={"conversation": turns})

        self.assertEqual(response.status_code, 400)
        self.assertIn(f"{MAX_TEXT_LENGTH:,}", response.json()["detail"])

    def test_turn_guard_counts_utf16_code_units_like_azure_does(self):
        # 501 emoji are 501 code points but 1,002 UTF-16 code units.
        response = self.client.post(
            "/api/benchmark", json={"conversation": _turns("\U0001f600" * 501)}
        )

        self.assertEqual(response.status_code, 400)

    def test_rejects_out_of_range_iteration_counts(self):
        response = self.client.post(
            "/api/benchmark",
            json={"conversation": _turns("Hello."), "iterations": 0},
        )

        self.assertEqual(response.status_code, 422)

    def test_benchmark_returns_safe_results_and_costs(self):
        response = self.client.post(
            "/api/benchmark",
            json={"conversation": _turns("Contact Alice for support."), "iterations": 1},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("Alice", payload["parallel"]["summary"])
        self.assertNotIn("Alice", payload["parallel"]["redacted_text"])
        self.assertTrue(payload["parallel"]["discarded_speculative_summary"])
        self.assertEqual(payload["parallel"]["cost"]["summary_records"], 2)
        self.assertGreater(payload["sequential"]["total_ms"], 0)
        self.assertEqual(payload["turns"], 1)
        self.assertEqual(payload["pricing"]["currency"], "USD")

    def test_benchmark_warms_up_before_measuring(self):
        self.client.post(
            "/api/benchmark",
            json={"conversation": _turns("Contact Alice for support."), "iterations": 1},
        )

        self.assertNotIn("Alice", self.language.pii_calls[0].as_text())
        self.assertNotIn("Alice", self.language.summary_calls[0].as_text())

    def test_benchmark_reports_the_spread_across_iterations(self):
        response = self.client.post(
            "/api/benchmark",
            json={"conversation": _turns("Contact Alice."), "iterations": 3},
        )

        payload = response.json()
        self.assertEqual(payload["iterations"], 3)
        self.assertEqual(len(payload["sequential"]["samples_ms"]), 3)
        self.assertLessEqual(
            payload["sequential"]["min_ms"], payload["sequential"]["total_ms"]
        )
        self.assertLessEqual(
            payload["sequential"]["total_ms"], payload["sequential"]["max_ms"]
        )

    def test_benchmark_includes_a_prevalence_projection(self):
        response = self.client.post(
            "/api/benchmark",
            json={"conversation": _turns("Contact Alice."), "iterations": 1},
        )

        projection = response.json()["projection"]
        self.assertEqual(len(projection["curve"]), 21)
        self.assertEqual(projection["curve"][0]["pii_rate"], 0.0)
        self.assertEqual(projection["curve"][-1]["pii_rate"], 1.0)
        self.assertIn("break_even_pii_rate", projection)
        self.assertGreaterEqual(
            projection["curve"][-1]["parallel_usd"],
            projection["curve"][0]["parallel_usd"],
        )

    def test_pricing_exposes_official_sources(self):
        response = self.client.get("/api/pricing")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["region"], "eastus2")
        self.assertIn("prices.azure.com", response.json()["retail_api_url"])

    def test_pricing_outage_is_not_reported_as_an_azure_language_failure(self):
        client = TestClient(
            create_app(
                FakeLanguageService(),
                FakePricing(PricingUnavailableError("retail feed is down")),
            )
        )

        response = client.post(
            "/api/benchmark", json={"conversation": _turns("Hello.")}
        )

        self.assertEqual(response.status_code, 502)
        self.assertIn("retail feed is down", response.json()["detail"])
        self.assertNotIn("Azure benchmark failed", response.json()["detail"])

    def test_redact_returns_spans_without_echoing_the_values(self):
        response = self.client.post(
            "/api/redact",
            json={"conversation": _turns("Contact Alice for support.")},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["has_pii"])
        self.assertEqual(payload["categories"], ["Person"])
        self.assertNotIn("Alice", payload["redacted_text"])
        self.assertEqual(payload["text_records"], 1)
        for entity in payload["entities"]:
            self.assertNotIn("text", entity)

    def test_redact_reports_no_pii_without_changing_the_transcript(self):
        response = self.client.post(
            "/api/redact", json={"conversation": _turns("The modem is offline.")}
        )

        payload = response.json()
        self.assertFalse(payload["has_pii"])
        self.assertEqual(payload["categories"], [])
        self.assertEqual(
            payload["redacted_conversation"],
            [{"role": "Customer", "text": "The modem is offline."}],
        )

    def test_summarize_returns_a_summary_for_the_text_it_was_given(self):
        response = self.client.post(
            "/api/summarize", json={"conversation": _turns("Contact Alice.")}
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Summary of", response.json()["summary"])

    def test_pipeline_sequential_summarizes_only_redacted_text(self):
        response = self.client.post(
            "/api/pipeline",
            json={"conversation": _turns("Contact Alice."), "mode": "sequential"},
        )

        payload = response.json()
        self.assertEqual(payload["mode"], "sequential")
        self.assertNotIn("Alice", payload["summary"])
        self.assertFalse(payload["discarded_speculative_summary"])
        self.assertEqual(payload["cost"]["summary_records"], 1)

    def test_pipeline_parallel_discards_the_unsafe_speculative_summary(self):
        response = self.client.post(
            "/api/pipeline",
            json={"conversation": _turns("Contact Alice."), "mode": "parallel"},
        )

        payload = response.json()
        self.assertNotIn("Alice", payload["summary"])
        self.assertTrue(payload["discarded_speculative_summary"])
        self.assertEqual(payload["cost"]["summary_records"], 2)

    def test_pipeline_rejects_an_unknown_mode(self):
        response = self.client.post(
            "/api/pipeline",
            json={"conversation": _turns("Hello."), "mode": "diagonal"},
        )

        self.assertEqual(response.status_code, 422)

    def test_pipeline_still_works_when_pricing_is_unavailable(self):
        """Costing is a nice-to-have; a retail outage must not fail the work."""
        client = TestClient(
            create_app(
                FakeLanguageService(),
                FakePricing(PricingUnavailableError("retail feed is down")),
            )
        )

        response = client.post(
            "/api/pipeline", json={"conversation": _turns("Contact Alice.")}
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["cost"])
        self.assertNotIn("Alice", response.json()["summary"])

    def test_task_endpoints_enforce_the_same_transcript_guards(self):
        for path in ("/api/redact", "/api/summarize", "/api/pipeline"):
            with self.subTest(path=path):
                oversized = self.client.post(
                    path,
                    json={"conversation": _turns("x" * (MAX_TURN_LENGTH + 1))},
                )
                self.assertEqual(oversized.status_code, 400)

    def test_azure_failure_is_surfaced_as_a_bad_gateway(self):
        class BrokenLanguageService(FakeLanguageService):
            def summarize(self, conv):
                raise RuntimeError("summarization exploded")

        client = TestClient(create_app(BrokenLanguageService(), FakePricing()))

        response = client.post(
            "/api/benchmark", json={"conversation": _turns("Hello.")}
        )

        self.assertEqual(response.status_code, 502)
        self.assertIn("Azure benchmark failed", response.json()["detail"])

    def test_azure_transport_failure_is_surfaced_as_a_bad_gateway(self):
        """ServiceRequestError is an AzureError but not an HttpResponseError."""

        class UnreachableLanguageService(FakeLanguageService):
            def detect_pii(self, conv):
                raise ServiceRequestError("connection refused")

        client = TestClient(create_app(UnreachableLanguageService(), FakePricing()))

        response = client.post(
            "/api/benchmark", json={"conversation": _turns("Hello.")}
        )

        self.assertEqual(response.status_code, 502)
        self.assertIn("Azure benchmark failed", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
