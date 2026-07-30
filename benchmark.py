from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Callable


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
class PipelineResult:
    mode: str
    total_ms: float
    has_pii: bool
    pii_categories: tuple[str, ...]
    redacted_text: str
    summary: str
    operations: tuple[OperationTiming, ...]
    discarded_speculative_summary: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def text_records(text: str) -> int:
    return math.ceil(len(text) / 1000) if text else 0


class BenchmarkService:
    def __init__(
        self,
        detect_pii: Callable[[str], PiiResult],
        summarize: Callable[[str], str],
    ) -> None:
        self._detect_pii = detect_pii
        self._summarize = summarize

    @staticmethod
    def _timed_call(name: str, text: str, operation: Callable[[str], object]):
        started = time.perf_counter()
        result = operation(text)
        timing = OperationTiming(
            name=name,
            duration_ms=(time.perf_counter() - started) * 1000,
            characters=len(text),
        )
        return result, timing

    def run_sequential(self, text: str) -> PipelineResult:
        started = time.perf_counter()
        pii_result, pii_timing = self._timed_call("pii", text, self._detect_pii)
        summary_input = pii_result.redacted_text if pii_result.has_pii else text
        summary, summary_timing = self._timed_call(
            "summary", summary_input, self._summarize
        )
        return PipelineResult(
            mode="sequential",
            total_ms=(time.perf_counter() - started) * 1000,
            has_pii=pii_result.has_pii,
            pii_categories=pii_result.categories,
            redacted_text=pii_result.redacted_text,
            summary=summary,
            operations=(pii_timing, summary_timing),
            discarded_speculative_summary=False,
        )

    def run_parallel(self, text: str) -> PipelineResult:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=2) as executor:
            pii_future = executor.submit(
                self._timed_call, "pii", text, self._detect_pii
            )
            summary_future = executor.submit(
                self._timed_call,
                "speculative_summary",
                text,
                self._summarize,
            )
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

        return PipelineResult(
            mode="parallel",
            total_ms=(time.perf_counter() - started) * 1000,
            has_pii=pii_result.has_pii,
            pii_categories=pii_result.categories,
            redacted_text=pii_result.redacted_text,
            summary=summary,
            operations=tuple(timings),
            discarded_speculative_summary=discarded,
        )
