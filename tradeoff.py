from __future__ import annotations

from dataclasses import asdict, dataclass

from benchmark import PipelineResult, records_for_length
from pricing import RetailRates

# 0%, 5%, ... 100%
CURVE_STEP = 0.05
CURVE_POINTS = int(round(1 / CURVE_STEP)) + 1


@dataclass(frozen=True)
class PipelinePrimitives:
    """Measured building blocks the projection is derived from.

    ``overlapped_ms`` is the measured wall time of the concurrent PII +
    speculative-summary phase, so it already includes real thread and
    connection contention rather than an idealised ``max()``.
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


def _median_summary_ms(*results: PipelineResult) -> float:
    durations = [
        operation.duration_ms
        for result in results
        for operation in result.operations
        if operation.name != "pii"
    ]
    if not durations:
        return 0.0
    return sum(durations) / len(durations)


def derive_primitives(
    sequential: PipelineResult, parallel: PipelineResult
) -> PipelinePrimitives:
    pii_timings = [
        operation
        for result in (sequential, parallel)
        for operation in result.operations
        if operation.name == "pii"
    ]
    pii_ms = (
        sum(timing.duration_ms for timing in pii_timings) / len(pii_timings)
        if pii_timings
        else 0.0
    )
    pii_characters = pii_timings[-1].characters if pii_timings else 0

    summary_ms = _median_summary_ms(sequential, parallel)
    summary_timing = sequential.operation("summary")
    summary_characters = summary_timing.characters if summary_timing else pii_characters

    # Strip the conditional second summarization to recover the cost of the
    # overlapped phase alone.
    redacted = parallel.operation("redacted_summary")
    overlapped_ms = parallel.total_ms - (redacted.duration_ms if redacted else 0.0)

    return PipelinePrimitives(
        pii_ms=pii_ms,
        summary_ms=summary_ms,
        overlapped_ms=max(overlapped_ms, 0.0),
        sequential_ms=sequential.total_ms,
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
    sequential: PipelineResult,
    parallel: PipelineResult,
    rates: RetailRates,
) -> TradeoffProjection:
    primitives = derive_primitives(sequential, parallel)

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
