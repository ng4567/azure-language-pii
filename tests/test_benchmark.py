import threading
import unittest
from pathlib import Path

from benchmark import BenchmarkService, PiiResult, text_records


class TextRecordTests(unittest.TestCase):
    def test_rounds_each_started_thousand_characters_to_a_record(self):
        self.assertEqual(text_records("a"), 1)
        self.assertEqual(text_records("a" * 1000), 1)
        self.assertEqual(text_records("a" * 1001), 2)


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


if __name__ == "__main__":
    unittest.main()
