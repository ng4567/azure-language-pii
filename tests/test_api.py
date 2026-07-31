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
from main import MAX_TEXT_LENGTH, create_app
from pricing import PricingUnavailableError, parse_retail_rates


class FakeLanguageService:
    def __init__(self):
        self.pii_calls = []
        self.summary_calls = []

    def detect_pii(self, text):
        self.pii_calls.append(text)
        if "Alice" in text:
            return PiiResult(text.replace("Alice", "*****"), ("Person",))
        return PiiResult(text, ())

    def summarize(self, text):
        self.summary_calls.append(text)
        return f"Summary of {text}"

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

    def test_health_check_does_not_call_azure(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_config_exposes_the_limits_the_ui_needs(self):
        response = self.client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["max_text_length"], MAX_TEXT_LENGTH)
        self.assertGreaterEqual(response.json()["max_iterations"], 1)

    def test_rejects_empty_and_oversized_text(self):
        empty = self.client.post("/api/benchmark", json={"text": "   "})
        oversized = self.client.post(
            "/api/benchmark", json={"text": "x" * (MAX_TEXT_LENGTH + 1)}
        )

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(oversized.status_code, 400)

    def test_length_guard_counts_utf16_code_units_like_azure_does(self):
        # 2,501 emoji are 2,501 code points but 5,002 UTF-16 code units.
        response = self.client.post(
            "/api/benchmark", json={"text": "\U0001f600" * 2501}
        )

        self.assertEqual(response.status_code, 400)

    def test_rejects_out_of_range_iteration_counts(self):
        response = self.client.post(
            "/api/benchmark", json={"text": "Hello.", "iterations": 0}
        )

        self.assertEqual(response.status_code, 422)

    def test_benchmark_returns_safe_results_and_costs(self):
        response = self.client.post(
            "/api/benchmark",
            json={"text": "Contact Alice for support.", "iterations": 1},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("Alice", payload["parallel"]["summary"])
        self.assertTrue(payload["parallel"]["discarded_speculative_summary"])
        self.assertEqual(payload["parallel"]["cost"]["summary_records"], 2)
        self.assertGreater(payload["sequential"]["total_ms"], 0)
        self.assertEqual(payload["pricing"]["currency"], "USD")

    def test_benchmark_warms_up_before_measuring(self):
        self.client.post(
            "/api/benchmark",
            json={"text": "Contact Alice for support.", "iterations": 1},
        )

        self.assertNotEqual(self.language.pii_calls[0], "Contact Alice for support.")
        self.assertNotEqual(self.language.summary_calls[0], "Contact Alice for support.")

    def test_benchmark_reports_the_spread_across_iterations(self):
        response = self.client.post(
            "/api/benchmark", json={"text": "Contact Alice.", "iterations": 3}
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
            "/api/benchmark", json={"text": "Contact Alice.", "iterations": 1}
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

        response = client.post("/api/benchmark", json={"text": "Hello."})

        self.assertEqual(response.status_code, 502)
        self.assertIn("retail feed is down", response.json()["detail"])
        self.assertNotIn("Azure benchmark failed", response.json()["detail"])

    def test_azure_failure_is_surfaced_as_a_bad_gateway(self):
        class BrokenLanguageService(FakeLanguageService):
            def summarize(self, text):
                raise RuntimeError("summarization exploded")

        client = TestClient(create_app(BrokenLanguageService(), FakePricing()))

        response = client.post("/api/benchmark", json={"text": "Hello."})

        self.assertEqual(response.status_code, 502)
        self.assertIn("Azure benchmark failed", response.json()["detail"])

    def test_azure_transport_failure_is_surfaced_as_a_bad_gateway(self):
        """ServiceRequestError is an AzureError but not an HttpResponseError."""

        class UnreachableLanguageService(FakeLanguageService):
            def detect_pii(self, text):
                raise ServiceRequestError("connection refused")

        client = TestClient(create_app(UnreachableLanguageService(), FakePricing()))

        response = client.post("/api/benchmark", json={"text": "Hello."})

        self.assertEqual(response.status_code, 502)
        self.assertIn("Azure benchmark failed", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
