import unittest
import warnings

from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=StarletteDeprecationWarning,
)
from fastapi.testclient import TestClient

from benchmark import PiiResult
from main import create_app
from pricing import parse_retail_rates


class FakeLanguageService:
    def detect_pii(self, text):
        if "Alice" in text:
            return PiiResult(text.replace("Alice", "*****"), ("Person",))
        return PiiResult(text, ())

    def summarize(self, text):
        return f"Summary of {text}"

    def close(self):
        pass


class FakePricing:
    def get_rates(self):
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
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app(FakeLanguageService(), FakePricing()))

    def test_samples_are_equal_length(self):
        response = self.client.get("/api/samples")

        self.assertEqual(response.status_code, 200)
        samples = response.json()["samples"]
        self.assertEqual(samples["pii"]["characters"], samples["no_pii"]["characters"])

    def test_health_check_does_not_call_azure(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_rejects_empty_and_oversized_text(self):
        empty = self.client.post("/api/benchmark", json={"text": "   "})
        oversized = self.client.post("/api/benchmark", json={"text": "x" * 5001})

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(oversized.status_code, 400)

    def test_benchmark_returns_safe_results_and_costs(self):
        response = self.client.post(
            "/api/benchmark", json={"text": "Contact Alice for support."}
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("Alice", payload["parallel"]["summary"])
        self.assertTrue(payload["parallel"]["discarded_speculative_summary"])
        self.assertEqual(payload["parallel"]["cost"]["summary_records"], 2)
        self.assertGreater(payload["sequential"]["total_ms"], 0)
        self.assertEqual(payload["pricing"]["currency"], "USD")

    def test_pricing_exposes_official_sources(self):
        response = self.client.get("/api/pricing")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["region"], "eastus2")
        self.assertIn("prices.azure.com", response.json()["retail_api_url"])


if __name__ == "__main__":
    unittest.main()
