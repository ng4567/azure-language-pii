import unittest

from benchmark import OperationTiming, PipelineResult
from pricing import (
    PricingUnavailableError,
    estimate_pipeline_cost,
    parse_retail_rates,
)


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


def _result(mode, operations, **overrides):
    defaults = dict(
        mode=mode,
        iterations=1,
        total_ms=30,
        p95_ms=30,
        min_ms=30,
        max_ms=30,
        samples_ms=(30,),
        has_pii=True,
        pii_categories=("Person",),
        redacted_text="*",
        summary="Safe",
        operations=operations,
        discarded_speculative_summary=True,
    )
    defaults.update(overrides)
    return PipelineResult(**defaults)


class PricingTests(unittest.TestCase):
    def test_parses_exact_standard_meters_as_per_record_rates(self):
        rates = parse_retail_rates(ITEMS, "eastus2")

        self.assertEqual(rates.pii_per_record_usd, 0.001)
        self.assertEqual(rates.summary_per_record_usd, 0.002)
        self.assertEqual(rates.region, "eastus2")
        self.assertEqual(len(rates.meters), 2)

    def test_estimates_cost_from_actual_operation_character_counts(self):
        rates = parse_retail_rates(ITEMS, "eastus2")
        result = _result(
            "parallel",
            (
                OperationTiming("pii", 10, 1001),
                OperationTiming("speculative_summary", 10, 1001),
                OperationTiming("redacted_summary", 10, 1001),
            ),
        )

        estimate = estimate_pipeline_cost(result, rates)

        self.assertEqual(estimate.pii_records, 2)
        self.assertEqual(estimate.summary_records, 4)
        self.assertAlmostEqual(estimate.total_usd, 0.01)

    def test_rejects_missing_official_meter(self):
        with self.assertRaisesRegex(PricingUnavailableError, "Summarization"):
            parse_retail_rates(ITEMS[:1], "eastus2")

    def test_rejects_unknown_billable_operation(self):
        rates = parse_retail_rates(ITEMS, "eastus2")
        result = _result("parallel", (OperationTiming("translation", 10, 100),))

        with self.assertRaisesRegex(ValueError, "translation"):
            estimate_pipeline_cost(result, rates)

    def test_ignores_dev_test_rows_for_the_same_meter(self):
        items = [
            {
                "meterName": "Standard Text Records",
                "retailPrice": 1.0,
                "unitOfMeasure": "1K",
                "type": "Consumption",
                "armRegionName": "eastus2",
                "effectiveStartDate": "2020-12-01T00:00:00Z",
            },
            {
                "meterName": "Standard Text Records",
                "retailPrice": 99.0,
                "unitOfMeasure": "1K",
                "type": "DevTestConsumption",
                "armRegionName": "eastus2",
                "effectiveStartDate": "2020-12-01T00:00:00Z",
            },
            {
                "meterName": "Standard Summarization Text Records",
                "retailPrice": 2.0,
                "unitOfMeasure": "1K",
                "type": "Consumption",
                "armRegionName": "eastus2",
                "effectiveStartDate": "2023-05-01T00:00:00Z",
            },
        ]

        rates = parse_retail_rates(items, "eastus2")

        self.assertEqual(rates.pii_per_record_usd, 0.001)

    def test_ignores_rows_from_another_region(self):
        items = [
            {**ITEMS[0], "armRegionName": "westus", "retailPrice": 99.0},
            {**ITEMS[0], "armRegionName": "eastus2"},
            {**ITEMS[1], "armRegionName": "eastus2"},
        ]

        rates = parse_retail_rates(items, "eastus2")

        self.assertEqual(rates.pii_per_record_usd, 0.001)

    def test_ignores_commitment_tier_rows_above_the_first_tier(self):
        items = [
            {**ITEMS[0], "tierMinimumUnits": 1000.0, "retailPrice": 0.5},
            {**ITEMS[0], "tierMinimumUnits": 0.0},
            {**ITEMS[1], "tierMinimumUnits": 0.0},
        ]

        rates = parse_retail_rates(items, "eastus2")

        self.assertEqual(rates.pii_per_record_usd, 0.001)

    def test_tolerates_null_tier_minimum_units(self):
        items = [
            {**ITEMS[0], "tierMinimumUnits": None},
            {**ITEMS[1], "tierMinimumUnits": None},
        ]

        rates = parse_retail_rates(items, "eastus2")

        self.assertEqual(rates.pii_per_record_usd, 0.001)


class RetailPricingCacheTests(unittest.TestCase):
    def _pricing(self, pages):
        from pricing import RetailPricing

        pricing = RetailPricing("eastus2", ttl_seconds=3600)
        pricing.requests = []

        def fake_get_json(url):
            pricing.requests.append(url)
            return pages[len(pricing.requests) - 1]

        pricing._get_json = fake_get_json
        return pricing

    def test_follows_next_page_link(self):
        pricing = self._pricing(
            [
                {"Items": [ITEMS[0]], "NextPageLink": "https://example.test/page2"},
                {"Items": [ITEMS[1]], "NextPageLink": None},
            ]
        )

        rates = pricing.get_rates()

        self.assertEqual(len(pricing.requests), 2)
        self.assertEqual(pricing.requests[1], "https://example.test/page2")
        self.assertEqual(rates.summary_per_record_usd, 0.002)

    def test_caches_within_the_ttl(self):
        pricing = self._pricing([{"Items": ITEMS, "NextPageLink": None}])

        pricing.get_rates()
        pricing.get_rates()

        self.assertEqual(len(pricing.requests), 1)

    def test_refetches_after_the_ttl_expires(self):
        pricing = self._pricing(
            [
                {"Items": ITEMS, "NextPageLink": None},
                {"Items": ITEMS, "NextPageLink": None},
            ]
        )
        pricing._ttl_seconds = 0

        pricing.get_rates()
        pricing.get_rates()

        self.assertEqual(len(pricing.requests), 2)


if __name__ == "__main__":
    unittest.main()
