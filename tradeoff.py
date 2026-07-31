from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median

from benchmark import (
    PII_OPERATION,
    ComparisonResult,
    PipelineOutcome,
    records_for_length,
)
from pricing import RetailRates

# 0%, 5%, ... 100%
CURVE_STEP = 0.05
CURVE_POINTS = int(round(1 / CURVE_STEP)) + 1


@dataclass(frozen=True)
class PipelinePrimitives:
    """Measured building blocks the projection is derived from.

    Each value is derived per iteration and then reduced with a median, so
    every one describes runs that actually happened.

    ``overlapped_ms`` is the measured wall time of the concurrent PII +
    speculative-summary phase, so it already includes real thread and
    connection contention rather than an idealised ``max()``.

    ``pii_ms`` averages both pipelines' PII calls, since both measure the same
    operation and two samples per iteration beat one. That makes it an
    estimate of the service, not a component of the parallel run, so it is not
    bounded by ``overlapped_ms`` and can exceed it slightly when the
    sequential pipeline happens to draw slower PII calls.
    """

    pii_ms: float
    summary_ms: float
    overlapped_ms: float
    sequential_ms: float
    pii_records: int
    summary_records: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PrevalencePoint:
    pii_rate: float
    sequential_ms: float
    parallel_ms: float
    sequential_usd: float
    parallel_usd: float
    latency_saved_ms: float
    extra_cost_usd: float
    usd_per_second_saved: float | None


@dataclass(frozen=True)
class TradeoffProjection:
    """Expected latency and cost as a function of corpus PII prevalence.

    The sweep parameter is P(a document contains at least one PII entity) for
    the customer's corpus. It is *not* a free axis: longer documents are more
    likely to contain an entity, so this projection describes a corpus whose
    documents resemble the one that was measured.
    """

    primitives: PipelinePrimitives
    break_even_pii_rate: float | None
    curve: tuple[PrevalencePoint, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _iteration_primitives(
    sequential: PipelineOutcome, parallel: PipelineOutcome
) -> tuple[float, float, float, float]:
    """The four latency primitives for one paired iteration.

    Deriving these per iteration and taking medians afterwards keeps the
    physical relationships intact. Subtracting one aggregate from another
    could place the overlapped phase below the PII call it contains, which no
    single execution can do.
    """
    pii_durations = [
        operation.duration_ms
        for outcome in (sequential, parallel)
        for operation in outcome.operations
        if operation.name == PII_OPERATION
    ]
    summary_durations = [
        operation.duration_ms
        for outcome in (sequential, parallel)
        for operation in outcome.operations
        if operation.name != PII_OPERATION
    ]
    retry_ms = next(
        (
            operation.duration_ms
            for operation in parallel.operations
            if operation.name == "redacted_summary"
        ),
        0.0,
    )
    return (
        sum(pii_durations) / len(pii_durations) if pii_durations else 0.0,
        sum(summary_durations) / len(summary_durations) if summary_durations else 0.0,
        max(parallel.total_ms - retry_ms, 0.0),
        sequential.total_ms,
    )


def _characters(outcome: PipelineOutcome, name: str) -> int:
    for operation in outcome.operations:
        if operation.name == name:
            return operation.characters
    return 0


def derive_primitives(comparison: ComparisonResult) -> PipelinePrimitives:
    pairs = list(
        zip(comparison.sequential_outcomes, comparison.parallel_outcomes)
    )
    if not pairs:
        raise ValueError("The comparison carries no per-iteration outcomes.")

    per_iteration = [_iteration_primitives(s, p) for s, p in pairs]
    pii_ms, summary_ms, overlapped_ms, sequential_ms = (
        median(values[index] for values in per_iteration) for index in range(4)
    )

    latest_sequential = comparison.sequential_outcomes[-1]
    pii_characters = _characters(latest_sequential, PII_OPERATION)
    summary_characters = _characters(latest_sequential, "summary") or pii_characters

    return PipelinePrimitives(
        pii_ms=pii_ms,
        summary_ms=summary_ms,
        overlapped_ms=overlapped_ms,
        sequential_ms=sequential_ms,
        pii_records=records_for_length(pii_characters),
        summary_records=records_for_length(summary_characters),
    )


def break_even_pii_rate(primitives: PipelinePrimitives) -> float | None:
    """The prevalence at which parallel stops being faster than sequential.

    Parallel saves ``sequential_ms - overlapped_ms`` on every clean document
    and pays a whole extra summarization on every dirty one, so the two are
    equal at ``(sequential_ms - overlapped_ms) / summary_ms``.
    """
    if primitives.summary_ms <= 0:
        return None
    rate = (primitives.sequential_ms - primitives.overlapped_ms) / primitives.summary_ms
    return min(max(rate, 0.0), 1.0)


def project(
    comparison: ComparisonResult, rates: RetailRates
) -> TradeoffProjection:
    primitives = derive_primitives(comparison)

    sequential_usd = (
        primitives.pii_records * rates.pii_per_record_usd
        + primitives.summary_records * rates.summary_per_record_usd
    )
    retry_usd = primitives.summary_records * rates.summary_per_record_usd

    points = []
    for index in range(CURVE_POINTS):
        rate = min(index * CURVE_STEP, 1.0)
        parallel_ms = primitives.overlapped_ms + rate * primitives.summary_ms
        parallel_usd = sequential_usd + rate * retry_usd
        latency_saved_ms = primitives.sequential_ms - parallel_ms
        extra_cost_usd = parallel_usd - sequential_usd
        points.append(
            PrevalencePoint(
                pii_rate=rate,
                sequential_ms=primitives.sequential_ms,
                parallel_ms=parallel_ms,
                sequential_usd=sequential_usd,
                parallel_usd=parallel_usd,
                latency_saved_ms=latency_saved_ms,
                extra_cost_usd=extra_cost_usd,
                usd_per_second_saved=(
                    extra_cost_usd / (latency_saved_ms / 1000)
                    if latency_saved_ms > 0
                    else None
                ),
            )
        )

    return TradeoffProjection(
        primitives=primitives,
        break_even_pii_rate=break_even_pii_rate(primitives),
        curve=tuple(points),
    )
