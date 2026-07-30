import threading
import unittest
from pathlib import Path

from benchmark import (
    BenchmarkService,
    PiiResult,
    billable_characters,
    percentile,
    records_for_length,
    text_records,
)


class TextRecordTests(unittest.TestCase):
    def test_rounds_each_started_thousand_characters_to_a_record(self):
        self.assertEqual(text_records("a"), 1)
        self.assertEqual(text_records("a" * 1000), 1)
        self.assertEqual(text_records("a" * 1001), 2)

    def test_empty_text_is_not_billable(self):
        self.assertEqual(text_records(""), 0)
        self.assertEqual(records_for_length(0), 0)

    def test_counts_astral_characters_as_utf16_code_units(self):
        # An emoji is one Python code point but two UTF-16 code units, which
        # is what Azure bills against.
        self.assertEqual(len("\U0001f600"), 1)
        self.assertEqual(billable_characters("\U0001f600"), 2)
        self.assertEqual(text_records("\U0001f600" * 500), 1)
        self.assertEqual(text_records("\U0001f600" * 501), 2)


class PercentileTests(unittest.TestCase):
    def test_nearest_rank_percentile(self):
        self.assertEqual(percentile([10, 20, 30, 40], 0.5), 20)
        self.assertEqual(percentile([10, 20, 30, 40], 0.95), 40)
        self.assertEqual(percentile([7], 0.95), 7)
        self.assertEqual(percentile([], 0.95), 0.0)


class SampleTests(unittest.TestCase):
    def test_bundled_samples_have_identical_character_counts(self):
        root = Path(__file__).parents[1]
        pii = (root / "samples" / "PII.txt").read_text().strip()
        no_pii = (root / "samples" / "No PII.txt").read_text().strip()

        self.assertEqual(len(pii), len(no_pii))
        self.assertGreater(len(pii), 400)

    def test_no_pii_sample_avoids_person_type_references(self):
        root = Path(__file__).parents[1]
        no_pii = (root / "samples" / "No PII.txt").read_text().lower()

        for person_reference in ("customer", "representative", "person", "user"):
            self.assertNotIn(person_reference, no_pii)


class BenchmarkServiceTests(unittest.TestCase):
    def test_sequential_redacts_before_summarizing(self):
        calls = []

        def detect(text):
            calls.append(("pii", text))
            return PiiResult("Customer ***** requested help.", ("Person",))

        def summarize(text):
            calls.append(("summary", text))
            return "A customer requested help."

        result = BenchmarkService(detect, summarize).run_sequential(
            "Customer Alice requested help."
        )

        self.assertEqual(
            calls,
            [
                ("pii", "Customer Alice requested help."),
                ("summary", "Customer ***** requested help."),
            ],
        )
        self.assertEqual(result.summary, "A customer requested help.")
        self.assertTrue(result.has_pii)
        self.assertFalse(result.discarded_speculative_summary)

    def test_parallel_starts_pii_and_summary_concurrently_without_pii(self):
        barrier = threading.Barrier(2)

        def detect(text):
            barrier.wait(timeout=1)
            return PiiResult(text, ())

        def summarize(text):
            barrier.wait(timeout=1)
            return "Safe summary"

        result = BenchmarkService(detect, summarize).run_parallel("General notes.")

        self.assertEqual(result.summary, "Safe summary")
        self.assertEqual([item.name for item in result.operations], ["pii", "summary"])
        self.assertFalse(result.has_pii)
        self.assertFalse(result.discarded_speculative_summary)

    def test_parallel_discards_speculative_summary_and_resummarizes_redacted_input(self):
        summaries = []

        def detect(text):
            return PiiResult("Contact ***** for help.", ("Person",))

        def summarize(text):
            summaries.append(text)
            if "*" not in text:
                return "Unsafe summary naming Alice"
            return "Safe redacted summary"

        result = BenchmarkService(detect, summarize).run_parallel(
            "Contact Alice for help."
        )

        self.assertEqual(
            summaries,
            ["Contact Alice for help.", "Contact ***** for help."],
        )
        self.assertEqual(result.summary, "Safe redacted summary")
        self.assertNotIn("Alice", result.summary)
        self.assertTrue(result.discarded_speculative_summary)
        self.assertEqual(
            [item.name for item in result.operations],
            ["pii", "speculative_summary", "redacted_summary"],
        )

    def test_parallel_reports_summary_failure_even_when_pii_succeeds(self):
        def detect(text):
            return PiiResult(text, ())

        def summarize(text):
            raise RuntimeError("summarization exploded")

        with self.assertRaisesRegex(RuntimeError, "summarization exploded"):
            BenchmarkService(detect, summarize).run_parallel("Notes.")

    def test_parallel_does_not_swallow_summary_failure_when_pii_fails_first(self):
        """Both futures must be inspected so neither exception is dropped."""

        def detect(text):
            raise RuntimeError("pii exploded")

        def summarize(text):
            raise RuntimeError("summary exploded")

        with self.assertRaises(RuntimeError) as caught:
            BenchmarkService(detect, summarize).run_parallel("Notes.")
        self.assertIn("exploded", str(caught.exception))


class WarmUpAndIterationTests(unittest.TestCase):
    def _service(self, calls, iterations=3):
        def detect(text):
            calls.append(("pii", text))
            return PiiResult(text, ())

        def summarize(text):
            calls.append(("summary", text))
            return "Safe summary"

        return BenchmarkService(detect, summarize, iterations=iterations)

    def test_warm_up_runs_before_any_timed_call(self):
        calls = []
        self._service(calls).compare("General notes.")

        # The first two calls are the warm-up pair, on neither pipeline's text.
        self.assertEqual({name for name, _ in calls[:2]}, {"pii", "summary"})
        self.assertNotIn("General notes.", {text for _, text in calls[:2]})

    def test_warm_up_can_be_skipped(self):
        calls = []
        self._service(calls).compare("General notes.", warm_up=False)

        self.assertTrue(all(text == "General notes." for _, text in calls))

    def test_compare_alternates_which_pipeline_runs_first(self):
        order = []

        def detect(text):
            return PiiResult(text, ())

        def summarize(text):
            return "Safe summary"

        service = BenchmarkService(detect, summarize, iterations=4)
        original_sequential = service.run_sequential
        original_parallel = service.run_parallel

        def tracked_sequential(text):
            order.append("sequential")
            return original_sequential(text)

        def tracked_parallel(text):
            order.append("parallel")
            return original_parallel(text)

        service.run_sequential = tracked_sequential
        service.run_parallel = tracked_parallel
        service.compare("General notes.", warm_up=False)

        self.assertEqual(
            order,
            ["sequential", "parallel", "parallel", "sequential"] * 2,
        )

    def test_compare_reports_median_and_spread_across_iterations(self):
        import itertools
        import time

        durations = itertools.cycle([0.03, 0.01, 0.02])

        def detect(text):
            time.sleep(next(durations))
            return PiiResult(text, ())

        def summarize(text):
            return "Safe summary"

        comparison = BenchmarkService(detect, summarize, iterations=3).compare(
            "General notes.", warm_up=False
        )

        sequential = comparison.sequential
        self.assertEqual(comparison.iterations, 3)
        self.assertEqual(sequential.iterations, 3)
        self.assertEqual(len(sequential.samples_ms), 3)
        self.assertLessEqual(sequential.min_ms, sequential.total_ms)
        self.assertLessEqual(sequential.total_ms, sequential.p95_ms)
        self.assertLessEqual(sequential.p95_ms, sequential.max_ms)

    def test_aggregated_operations_have_one_entry_per_name(self):
        def detect(text):
            return PiiResult("Contact ***** for help.", ("Person",))

        def summarize(text):
            return "Safe summary"

        comparison = BenchmarkService(detect, summarize, iterations=3).compare(
            "Contact Alice for help.", warm_up=False
        )

        self.assertEqual(
            [item.name for item in comparison.parallel.operations],
            ["pii", "speculative_summary", "redacted_summary"],
        )
        self.assertEqual(
            [item.name for item in comparison.sequential.operations],
            ["pii", "summary"],
        )

    def test_rejects_non_positive_iterations(self):
        with self.assertRaises(ValueError):
            BenchmarkService(lambda text: PiiResult(text, ()), lambda text: "", 0)


if __name__ == "__main__":
    unittest.main()
