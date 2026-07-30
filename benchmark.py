from __future__ import annotations

import math
import statistics
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Sequence

DEFAULT_ITERATIONS = 3
MAX_ITERATIONS = 10
WARM_UP_TEXT = "Warm up the client pipeline before any timed measurement."

PII_OPERATION = "pii"
SUMMARY_OPERATIONS = frozenset(
    {"summary", "speculative_summary", "redacted_summary"}
)


@dataclass(frozen=True)
class PiiResult:
    redacted_text: str
    categories: tuple[str, ...]

    @property
    def has_pii(self) -> bool:
        return bool(self.categories)


@dataclass(frozen=True)
class OperationTiming:
    name: str
    duration_ms: float
    characters: int


@dataclass(frozen=True)
class PipelineOutcome:
    """One un-aggregated execution of a pipeline."""

    has_pii: bool
    pii_categories: tuple[str, ...]
    redacted_text: str
    summary: str
    total_ms: float
    operations: tuple[OperationTiming, ...]
    discarded_speculative_summary: bool


@dataclass(frozen=True)
class PipelineResult:
    """Aggregated timings across every iteration of one pipeline.

    ``total_ms`` is the median rather than a single sample, because the
    underlying calls are network bound and a single draw is not a usable
    basis for a price/performance decision.
    """

    mode: str
    iterations: int
    total_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float
    samples_ms: tuple[float, ...]
    has_pii: bool
    pii_categories: tuple[str, ...]
    redacted_text: str
    summary: str
    operations: tuple[OperationTiming, ...]
    discarded_speculative_summary: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def operation(self, name: str) -> OperationTiming | None:
        for operation in self.operations:
            if operation.name == name:
                return operation
        return None


@dataclass(frozen=True)
class ComparisonResult:
    iterations: int
    sequential: PipelineResult
    parallel: PipelineResult


def billable_characters(text: str) -> int:
    """Count characters the way Azure bills them.

    Azure measures text records in UTF-16 code units, which is also what the
    default ``string_index_type`` uses. Python's ``len`` counts code points,
    so anything outside the Basic Multilingual Plane would be undercounted.
    """
    return len(text.encode("utf-16-le")) // 2


def records_for_length(characters: int) -> int:
    """Each started 1,000 characters is one billable text record."""
    return math.ceil(characters / 1000) if characters > 0 else 0


def text_records(text: str) -> int:
    return records_for_length(billable_characters(text))


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile.

    With the small iteration counts this tool uses, the high percentiles are
    coarse by construction; they indicate spread, not a distribution tail.
    """
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = math.ceil(fraction * len(ordered))
    return ordered[min(max(rank, 1), len(ordered)) - 1]


def _aggregate(mode: str, outcomes: Sequence[PipelineOutcome]) -> PipelineResult:
    if not outcomes:
        raise ValueError("Cannot aggregate an empty set of pipeline runs.")

    totals = [outcome.total_ms for outcome in outcomes]
    timings_by_name: dict[str, list[OperationTiming]] = {}
    for outcome in outcomes:
        for operation in outcome.operations:
            timings_by_name.setdefault(operation.name, []).append(operation)

    operations = tuple(
        OperationTiming(
            name=name,
            duration_ms=statistics.median(timing.duration_ms for timing in timings),
            characters=timings[-1].characters,
        )
        for name, timings in timings_by_name.items()
    )

    latest = outcomes[-1]
    return PipelineResult(
        mode=mode,
        iterations=len(outcomes),
        total_ms=statistics.median(totals),
        p95_ms=percentile(totals, 0.95),
        min_ms=min(totals),
        max_ms=max(totals),
        samples_ms=tuple(totals),
        has_pii=latest.has_pii,
        pii_categories=latest.pii_categories,
        redacted_text=latest.redacted_text,
        summary=latest.summary,
        operations=operations,
        discarded_speculative_summary=latest.discarded_speculative_summary,
    )


def _raise_first_failure(futures: Iterable[Future]) -> None:
    """Retrieve every exception so none is silently dropped, then re-raise."""
    failures = [future.exception() for future in futures]
    for failure in failures:
        if failure is not None:
            raise failure


class BenchmarkService:
    def __init__(
        self,
        detect_pii: Callable[[str], PiiResult],
        summarize: Callable[[str], str],
        iterations: int = DEFAULT_ITERATIONS,
    ) -> None:
        if iterations < 1:
            raise ValueError("iterations must be at least 1.")
        self._detect_pii = detect_pii
        self._summarize = summarize
        self._iterations = iterations

    @staticmethod
    def _timed_call(name: str, text: str, operation: Callable[[str], object]):
        started = time.perf_counter()
        result = operation(text)
        timing = OperationTiming(
            name=name,
            duration_ms=(time.perf_counter() - started) * 1000,
            characters=billable_characters(text),
        )
        return result, timing

    def warm_up(self, text: str = WARM_UP_TEXT) -> None:
        """Pay the one-time per-client costs before anything is timed.

        Each Azure client caches its bearer token on its own pipeline policy,
        so the first call through each client pays token acquisition (a
        subprocess spawn under ``az login``) plus a TLS handshake. Without
        this, whichever pipeline ran first would absorb all of it.
        """
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(self._detect_pii, text),
                executor.submit(self._summarize, text),
            )
            wait(futures)
            _raise_first_failure(futures)

    def compare(self, text: str, *, warm_up: bool = True) -> ComparisonResult:
        """Measure both pipelines, alternating which one runs first.

        Alternating removes the residual ordering advantage that would
        otherwise accrue to whichever pipeline consistently ran second.
        """
        if warm_up:
            self.warm_up()

        sequential: list[PipelineOutcome] = []
        parallel: list[PipelineOutcome] = []
        for index in range(self._iterations):
            if index % 2 == 0:
                sequential.append(self.run_sequential(text))
                parallel.append(self.run_parallel(text))
            else:
                parallel.append(self.run_parallel(text))
                sequential.append(self.run_sequential(text))

        return ComparisonResult(
            iterations=self._iterations,
            sequential=_aggregate("sequential", sequential),
            parallel=_aggregate("parallel", parallel),
        )

    def run_sequential(self, text: str) -> PipelineOutcome:
        started = time.perf_counter()
        pii_result, pii_timing = self._timed_call(PII_OPERATION, text, self._detect_pii)
        summary_input = pii_result.redacted_text if pii_result.has_pii else text
        summary, summary_timing = self._timed_call(
            "summary", summary_input, self._summarize
        )
        return PipelineOutcome(
            has_pii=pii_result.has_pii,
            pii_categories=pii_result.categories,
            redacted_text=pii_result.redacted_text,
            summary=summary,
            total_ms=(time.perf_counter() - started) * 1000,
            operations=(pii_timing, summary_timing),
            discarded_speculative_summary=False,
        )

    def run_parallel(self, text: str) -> PipelineOutcome:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=2) as executor:
            pii_future = executor.submit(
                self._timed_call, PII_OPERATION, text, self._detect_pii
            )
            summary_future = executor.submit(
                self._timed_call,
                "speculative_summary",
                text,
                self._summarize,
            )
            futures = (pii_future, summary_future)
            wait(futures)
            _raise_first_failure(futures)
            pii_result, pii_timing = pii_future.result()
            speculative_summary, speculative_timing = summary_future.result()

        timings = [pii_timing, speculative_timing]
        discarded = pii_result.has_pii
        if discarded:
            summary, redacted_timing = self._timed_call(
                "redacted_summary",
                pii_result.redacted_text,
                self._summarize,
            )
            timings.append(redacted_timing)
        else:
            summary = speculative_summary
            timings[1] = OperationTiming(
                name="summary",
                duration_ms=speculative_timing.duration_ms,
                characters=speculative_timing.characters,
            )

        return PipelineOutcome(
            has_pii=pii_result.has_pii,
            pii_categories=pii_result.categories,
            redacted_text=pii_result.redacted_text,
            summary=summary,
            total_ms=(time.perf_counter() - started) * 1000,
            operations=tuple(timings),
            discarded_speculative_summary=discarded,
        )
