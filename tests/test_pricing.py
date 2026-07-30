import unittest

from benchmark import OperationTiming, PipelineResult
from pricing import estimate_pipeline_cost, parse_retail_rates


ITEMS = [
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
]


class PricingTests(unittest.TestCase):
    def test_parses_exact_standard_meters_as_per_record_rates(self):
        rates = parse_retail_rates(ITEMS, "eastus2")

        self.assertEqual(rates.pii_per_record_usd, 0.001)
        self.assertEqual(rates.summary_per_record_usd, 0.002)
        self.assertEqual(rates.region, "eastus2")
        self.assertEqual(len(rates.meters), 2)

    def test_estimates_cost_from_actual_operation_character_counts(self):
        rates = parse_retail_rates(ITEMS, "eastus2")
        result = PipelineResult(
            mode="parallel",
            total_ms=30,
            has_pii=True,
            pii_categories=("Person",),
            redacted_text="*",
            summary="Safe",
            operations=(
                OperationTiming("pii", 10, 1001),
                OperationTiming("speculative_summary", 10, 1001),
                OperationTiming("redacted_summary", 10, 1001),
            ),
            discarded_speculative_summary=True,
        )

        estimate = estimate_pipeline_cost(result, rates)

        self.assertEqual(estimate.pii_records, 2)
        self.assertEqual(estimate.summary_records, 4)
        self.assertAlmostEqual(estimate.total_usd, 0.01)

    def test_rejects_missing_official_meter(self):
        with self.assertRaisesRegex(RuntimeError, "Summarization"):
            parse_retail_rates(ITEMS[:1], "eastus2")


if __name__ == "__main__":
    unittest.main()
