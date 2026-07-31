"""Score conversation PII redaction against hand-labelled transcripts.

Reports two different things, and the second one is the one that matters:

* **Span metrics** — recall and precision against labelled spans. Useful for
  understanding *why* something failed.
* **Leak rate** — whether a labelled PII value survives verbatim in the
  redacted output. This is the business requirement. A span can be missed
  while the value is still masked (an adjacent detection covered it), and a
  span can be detected while the value leaks (the mask was too narrow), so
  neither metric substitutes for the other.

Usage:
    uv run python evaluation/evaluate.py
    uv run python evaluation/evaluate.py --pipeline      # also scan summaries
    uv run python evaluation/evaluate.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DATASETS = Path(__file__).parent / "datasets"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# The conversation endpoint returns shorter category names than the text
# endpoint does. Labels are written in conversation-API terms; these aliases
# keep the scorer honest if a label uses the text-API spelling instead.
CATEGORY_ALIASES = {
    "CreditCard": {"CreditCard", "CreditCardNumber"},
    "Phone": {"Phone", "PhoneNumber"},
    "Email": {"Email", "EmailAddress"},
    "Address": {"Address", "StreetAddress", "USStreetAddress"},
    "DateOfBirth": {"DateOfBirth", "Date", "DateTime"},
    "USSocialSecurityNumber": {"USSocialSecurityNumber", "SSN"},
    "ABARoutingNumber": {"ABARoutingNumber", "USBankAccountNumber"},
    "USBankAccountNumber": {"USBankAccountNumber", "ABARoutingNumber"},
    "Age": {"Age", "Quantity"},
    "Person": {"Person", "PersonType"},
}


def aliases_for(category: str) -> set[str]:
    return CATEGORY_ALIASES.get(category, {category})


@dataclass
class SpanOutcome:
    dataset: str
    turn: int
    text: str
    category: str
    detected: bool
    detected_as: str | None
    leaked: bool


@dataclass
class Totals:
    expected: int = 0
    detected: int = 0
    category_matched: int = 0
    leaked: int = 0
    false_positives: int = 0
    misses: list[SpanOutcome] = field(default_factory=list)
    leaks: list[SpanOutcome] = field(default_factory=list)


def post(base_url: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode()[:300]
        raise SystemExit(f"{path} returned HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise SystemExit(
            f"Could not reach {base_url}. Is the app running? ({error.reason})"
        ) from error


def occurrences(haystack: str, needle: str) -> list[tuple[int, int]]:
    spans = []
    start = haystack.find(needle)
    while start != -1:
        spans.append((start, start + len(needle)))
        start = haystack.find(needle, start + 1)
    return spans


def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def score(dataset: dict, response: dict) -> tuple[list[SpanOutcome], int]:
    turns = dataset["conversation"]
    redacted = response["redacted_conversation"]
    detections = response["entities"]

    outcomes: list[SpanOutcome] = []
    matched_detections: set[int] = set()

    for label in dataset["expected"]:
        turn_index = label["turn"]
        source = turns[turn_index - 1]["text"]
        label_spans = occurrences(source, label["text"])
        if not label_spans:
            raise SystemExit(
                f"{dataset['id']}: label {label['text']!r} not found in turn "
                f"{turn_index}. The dataset is inconsistent."
            )

        detected_as = None
        for position, entity in enumerate(detections):
            if entity["turn"] != turn_index:
                continue
            entity_span = (entity["offset"], entity["offset"] + entity["length"])
            if any(overlaps(entity_span, span) for span in label_spans):
                matched_detections.add(position)
                if detected_as is None or entity["category"] in aliases_for(
                    label["category"]
                ):
                    detected_as = entity["category"]

        # The requirement is that the value is gone, not that a span was
        # reported, so this is checked against the redacted text itself.
        leaked = label["text"] in redacted[turn_index - 1]["text"]

        outcomes.append(
            SpanOutcome(
                dataset=dataset["id"],
                turn=turn_index,
                text=label["text"],
                category=label["category"],
                detected=detected_as is not None,
                detected_as=detected_as,
                leaked=leaked,
            )
        )

    false_positives = len(detections) - len(matched_detections)
    return outcomes, false_positives


def evaluate(base_url: str, run_pipeline: bool) -> tuple[Totals, list[dict]]:
    totals = Totals()
    reports = []

    for path in sorted(DATASETS.glob("*.json")):
        dataset = json.loads(path.read_text(encoding="utf-8"))
        response = post(
            base_url, "/api/redact", {"conversation": dataset["conversation"]}
        )
        outcomes, false_positives = score(dataset, response)

        totals.expected += len(outcomes)
        totals.detected += sum(1 for o in outcomes if o.detected)
        totals.category_matched += sum(
            1
            for o in outcomes
            if o.detected and o.detected_as in aliases_for(o.category)
        )
        totals.leaked += sum(1 for o in outcomes if o.leaked)
        totals.false_positives += false_positives
        totals.misses.extend(o for o in outcomes if not o.detected)
        totals.leaks.extend(o for o in outcomes if o.leaked)

        report = {
            "id": dataset["id"],
            "scenario": dataset["scenario"],
            "expected": len(outcomes),
            "detected": sum(1 for o in outcomes if o.detected),
            "leaked": sum(1 for o in outcomes if o.leaked),
            "false_positives": false_positives,
            "categories": response["categories"],
            "redacted_text": response["redacted_text"],
        }

        if run_pipeline:
            piped = post(
                base_url,
                "/api/pipeline",
                {"conversation": dataset["conversation"], "mode": "sequential"},
            )
            summary = piped["summary"]
            in_summary = [
                label["text"]
                for label in dataset["expected"]
                if label["text"] in summary
            ]
            report["summary"] = summary
            report["summary_leaks"] = in_summary

        reports.append(report)
        status = "LEAK" if report["leaked"] else "ok"
        print(
            f"  {dataset['id']:<32} {report['detected']}/{report['expected']} spans"
            f"  ·  {report['false_positives']} extra  ·  {status}"
        )

    return totals, reports


def percentage(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    return f"{numerator / denominator * 100:.0f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="also summarize each transcript and scan the summary for PII",
    )
    parser.add_argument("--json", help="write the full report to this path")
    args = parser.parse_args()

    print(f"Scoring {len(list(DATASETS.glob('*.json')))} transcripts "
          f"against {args.base_url}\n")
    totals, reports = evaluate(args.base_url, args.pipeline)

    print("\n" + "=" * 70)
    print(f"Labelled spans        {totals.expected}")
    print(
        f"Detected              {totals.detected}"
        f"  (recall {percentage(totals.detected, totals.expected)})"
    )
    print(
        f"Correct category      {totals.category_matched}"
        f"  ({percentage(totals.category_matched, totals.expected)})"
    )
    print(f"Unlabelled detections {totals.false_positives}")
    print(
        f"VALUES LEAKED         {totals.leaked}"
        f"  ({percentage(totals.leaked, totals.expected)} of labelled spans)"
    )

    if totals.misses:
        print("\nMissed spans:")
        for miss in totals.misses:
            marker = "leaked" if miss.leaked else "masked anyway"
            print(
                f"  {miss.dataset:<32} turn {miss.turn}  "
                f"{miss.category:<24} {miss.text[:44]!r}  [{marker}]"
            )

    if totals.leaks:
        print("\nValues still present after redaction:")
        for leak in totals.leaks:
            print(
                f"  {leak.dataset:<32} turn {leak.turn}  "
                f"{leak.category:<24} {leak.text[:44]!r}"
            )

    if args.pipeline:
        summary_leaks = [
            (report["id"], leak)
            for report in reports
            for leak in report.get("summary_leaks", [])
        ]
        print(f"\nPII values appearing in summaries: {len(summary_leaks)}")
        for dataset_id, leak in summary_leaks:
            print(f"  {dataset_id:<32} {leak!r}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "totals": {
                        "expected": totals.expected,
                        "detected": totals.detected,
                        "category_matched": totals.category_matched,
                        "leaked": totals.leaked,
                        "false_positives": totals.false_positives,
                    },
                    "datasets": reports,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nFull report written to {args.json}")

    return 1 if totals.leaked else 0


if __name__ == "__main__":
    sys.exit(main())
