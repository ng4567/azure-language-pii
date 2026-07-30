import unittest

from benchmark import OperationTiming, PipelineResult
from pricing import parse_retail_rates
from tradeoff import break_even_pii_rate, derive_primitives, project

RATES = parse_retail_rates(
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


def _result(mode, total_ms, operations, **overrides):
    defaults = dict(
        mode=mode,
        iterations=3,
        total_ms=total_ms,
        p95_ms=total_ms,
        min_ms=total_ms,
        max_ms=total_ms,
        samples_ms=(total_ms,),
        has_pii=True,
        pii_categories=("Person",),
        redacted_text="*" * 500,
        summary="Safe",
        operations=operations,
        discarded_speculative_summary=True,
    )
    defaults.update(overrides)
    return PipelineResult(**defaults)


# PII takes 100 ms, each summarization 400 ms.
SEQUENTIAL = _result(
    "sequential",
    500,
    (
        OperationTiming("pii", 100, 500),
        OperationTiming("summary", 400, 500),
    ),
)
PARALLEL = _result(
    "parallel",
    810,
    (
        OperationTiming("pii", 100, 500),
        OperationTiming("speculative_summary", 400, 500),
        OperationTiming("redacted_summary", 400, 500),
    ),
)


class PrimitiveTests(unittest.TestCase):
    def test_strips_the_conditional_retry_from_the_overlapped_phase(self):
        primitives = derive_primitives(SEQUENTIAL, PARALLEL)

        self.assertAlmostEqual(primitives.overlapped_ms, 410)
        self.assertAlmostEqual(primitives.sequential_ms, 500)
        self.assertAlmostEqual(primitives.pii_ms, 100)
        self.assertAlmostEqual(primitives.summary_ms, 400)
        self.assertEqual(primitives.pii_records, 1)
        self.assertEqual(primitives.summary_records, 1)

    def test_overlapped_phase_is_the_whole_run_when_no_retry_happened(self):
        parallel = _result(
            "parallel",
            420,
            (
                OperationTiming("pii", 100, 500),
                OperationTiming("summary", 400, 500),
            ),
            has_pii=False,
            pii_categories=(),
            discarded_speculative_summary=False,
        )

        primitives = derive_primitives(SEQUENTIAL, parallel)

        self.assertAlmostEqual(primitives.overlapped_ms, 420)


class BreakEvenTests(unittest.TestCase):
    def test_break_even_is_where_the_saving_equals_the_retry_cost(self):
        primitives = derive_primitives(SEQUENTIAL, PARALLEL)

        # Saves 90 ms per clean doc, pays 400 ms per dirty one: 90/400.
        self.assertAlmostEqual(break_even_pii_rate(primitives), 0.225)

    def test_break_even_is_clamped_to_a_probability(self):
        parallel = _result(
            "parallel",
            5000,
            (
                OperationTiming("pii", 100, 500),
                OperationTiming("speculative_summary", 400, 500),
                OperationTiming("redacted_summary", 400, 500),
            ),
        )

        self.assertEqual(break_even_pii_rate(derive_primitives(SEQUENTIAL, parallel)), 0.0)

    def test_break_even_is_unavailable_without_a_summary_measurement(self):
        sequential = _result("sequential", 100, (OperationTiming("pii", 100, 500),))
        parallel = _result("parallel", 100, (OperationTiming("pii", 100, 500),))

        self.assertIsNone(break_even_pii_rate(derive_primitives(sequential, parallel)))


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.projection = project(SEQUENTIAL, PARALLEL, RATES)

    def test_curve_spans_zero_to_full_prevalence(self):
        rates = [point.pii_rate for point in self.projection.curve]

        self.assertEqual(rates[0], 0.0)
        self.assertEqual(rates[-1], 1.0)
        self.assertEqual(len(rates), 21)

    def test_sequential_latency_is_flat_across_prevalence(self):
        latencies = {point.sequential_ms for point in self.projection.curve}

        self.assertEqual(latencies, {500})

    def test_parallel_latency_grows_with_prevalence(self):
        first = self.projection.curve[0]
        last = self.projection.curve[-1]

        self.assertAlmostEqual(first.parallel_ms, 410)
        self.assertAlmostEqual(last.parallel_ms, 810)

    def test_parallel_never_costs_less_than_sequential(self):
        for point in self.projection.curve:
            self.assertGreaterEqual(point.parallel_usd, point.sequential_usd)

    def test_extra_cost_is_the_retry_probability_times_a_summarization(self):
        half = next(p for p in self.projection.curve if p.pii_rate == 0.5)

        # One summary record at $0.002 per record, half the time.
        self.assertAlmostEqual(half.extra_cost_usd, 0.001)
        self.assertAlmostEqual(half.parallel_usd, 0.004)

    def test_price_per_second_saved_is_reported_only_while_parallel_wins(self):
        winning = next(p for p in self.projection.curve if p.pii_rate == 0.1)
        losing = next(p for p in self.projection.curve if p.pii_rate == 0.5)

        self.assertGreater(winning.latency_saved_ms, 0)
        self.assertIsNotNone(winning.usd_per_second_saved)
        self.assertLess(losing.latency_saved_ms, 0)
        self.assertIsNone(losing.usd_per_second_saved)

    def test_latency_saved_changes_sign_at_the_break_even_rate(self):
        break_even = self.projection.break_even_pii_rate

        for point in self.projection.curve:
            if point.pii_rate < break_even:
                self.assertGreater(point.latency_saved_ms, 0)
            elif point.pii_rate > break_even:
                self.assertLess(point.latency_saved_ms, 0)


if __name__ == "__main__":
    unittest.main()
