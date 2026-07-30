import unittest
import warnings

from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=StarletteDeprecationWarning,
)
from fastapi.testclient import TestClient

from benchmark import PiiEntity, PiiResult
from main import create_app
from pricing import parse_retail_rates


class FakeLanguageService:
    def detect_pii(self, text):
        if "Alice" in text:
            return PiiResult(
                text.replace("Alice", "*****"),
                ("Person",),
                (PiiEntity("Person", text.index("Alice"), 5, 0.99),),
            )
        if "Maya Chen" in text:
            return PiiResult(
                text.replace("Maya Chen", "*********"),
                ("Person",),
                (PiiEntity("Person", text.index("Maya Chen"), 9, 0.99),),
            )
        return PiiResult(text, (), ())

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
        self.assertEqual(
            payload["parallel"]["original_text"],
            "Contact Alice for support.",
        )
        self.assertEqual(payload["parallel"]["pii_entities"][0]["offset"], 8)
        self.assertTrue(payload["parallel"]["discarded_speculative_summary"])
        self.assertEqual(payload["parallel"]["cost"]["summary_records"], 2)
        self.assertGreater(payload["sequential"]["total_ms"], 0)
        self.assertEqual(payload["pricing"]["currency"], "USD")

    def test_pricing_exposes_official_sources(self):
        response = self.client.get("/api/pricing")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["region"], "eastus2")
        self.assertIn("prices.azure.com", response.json()["retail_api_url"])

    def test_sample_suite_returns_four_results_for_equal_size_inputs(self):
        response = self.client.post("/api/benchmark/samples")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["characters_per_sample"], 581)
        self.assertEqual(
            set(payload["datasets"]),
            {"pii", "no_pii"},
        )
        self.assertEqual(
            set(payload["datasets"]["pii"]["pipelines"]),
            {"sequential", "parallel"},
        )
        self.assertEqual(
            set(payload["datasets"]["no_pii"]["pipelines"]),
            {"sequential", "parallel"},
        )
        self.assertTrue(payload["datasets"]["pii"]["has_pii"])
        self.assertFalse(payload["datasets"]["no_pii"]["has_pii"])
        self.assertIn("Maya Chen", payload["datasets"]["pii"]["original_text"])
        self.assertEqual(
            payload["datasets"]["pii"]["pii_entities"][0]["category"],
            "Person",
        )
        self.assertEqual(payload["aggregate"]["pipeline_runs"], 4)
        self.assertGreater(payload["aggregate"]["total_cost_usd"], 0)


if __name__ == "__main__":
    unittest.main()
