from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Literal

from azure.core.exceptions import AzureError
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import tradeoff
from azure_language import AzureLanguageService
from benchmark import (
    DEFAULT_ITERATIONS,
    MAX_ITERATIONS,
    BenchmarkService,
    PipelineResult,
    billable_characters,
    text_records_for_conversation,
)
from conversation import MAX_TURN_LENGTH, ROLES, Conversation, Turn
from pricing import PricingUnavailableError, RetailPricing, estimate_pipeline_cost

ROOT = Path(__file__).parent
MAX_TEXT_LENGTH = 5_000

WARM_UP_CONVERSATION = Conversation(
    (
        Turn("Agent", "Warm up the client pipeline before any timed measurement."),
        Turn("Customer", "Understood, nothing here needs to be timed."),
    )
)


class TurnModel(BaseModel):
    role: Literal["Agent", "Customer"]
    text: str


class ConversationRequest(BaseModel):
    """A transcript on its own — the input to the single-task endpoints."""

    conversation: list[TurnModel]


class PipelineRequest(ConversationRequest):
    mode: Literal["sequential", "parallel"] = "sequential"


class BenchmarkRequest(ConversationRequest):
    iterations: int = Field(default=DEFAULT_ITERATIONS, ge=1, le=MAX_ITERATIONS)
    warm_up: bool = True


@lru_cache(maxsize=1)
def _load_samples() -> dict[str, dict[str, object]]:
    samples = {}
    for key, filename, label in (
        ("pii", "PII.json", "PII transcript"),
        ("no_pii", "No PII.json", "No-PII transcript"),
    ):
        turns = json.loads((ROOT / "samples" / filename).read_text(encoding="utf-8"))
        conv = Conversation.from_dicts(turns)
        samples[key] = {
            "label": label,
            "filename": filename,
            "conversation": conv.to_dict(),
            "characters": conv.billable_characters(),
        }
    return samples


def _serialize_result(result: PipelineResult, rates) -> dict[str, object]:
    return {
        **result.to_dict(),
        "cost": estimate_pipeline_cost(result, rates).to_dict(),
    }


def _validated_conversation(request: ConversationRequest) -> Conversation:
    turns = []
    for index, turn in enumerate(request.conversation, start=1):
        text = turn.text.strip()
        if not text:
            raise HTTPException(
                status_code=400, detail=f"Turn {index} has no text."
            )
        # Measured the way Azure measures it, so astral characters cannot slip
        # a turn past this guard and into a service-side job rejection.
        if billable_characters(text) > MAX_TURN_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Turn {index} exceeds {MAX_TURN_LENGTH:,} characters; the "
                    "conversation API enforces this per turn."
                ),
            )
        turns.append(Turn(role=turn.role, text=text))
    if not turns:
        raise HTTPException(status_code=400, detail="At least one turn is required.")

    conv = Conversation(tuple(turns))
    if conv.billable_characters() > MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"The transcript must be {MAX_TEXT_LENGTH:,} characters or fewer.",
        )
    return conv


def _azure_guard(operation, label: str = "Azure request failed"):
    """Run an Azure-backed call, mapping service failures to 502.

    AzureError also covers the transport failures (ServiceRequestError,
    ServiceResponseError) that HttpResponseError does not.
    """
    try:
        return operation()
    except (AzureError, RuntimeError) as error:
        raise HTTPException(status_code=502, detail=f"{label}: {error}") from error


def create_app(language_service=None, pricing_service=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        load_dotenv()
        owns_language_service = language_service is None
        application.state.language = (
            language_service or AzureLanguageService.from_environment()
        )
        application.state.pricing = pricing_service or RetailPricing(
            os.getenv("AZURE_REGION", "eastus2")
        )
        try:
            yield
        finally:
            if owns_language_service:
                application.state.language.close()

    application = FastAPI(
        title="Azure Conversation PII and Summarization Benchmark",
        version="3.1.0",
        description=(
            "Conversation PII redaction and conversation summarization over "
            "call-transcript payloads. The `tasks` endpoints do one unit of "
            "work each and are what to call from your own code; `benchmark` "
            "is the measurement harness behind the dashboard. Human-readable "
            "examples live at /api-docs."
        ),
        lifespan=lifespan,
    )

    # Browsers enforce CORS; curl and server-side clients do not. Off unless
    # an origin list is configured, so the default stays same-origin.
    load_dotenv()
    origins = [
        origin.strip()
        for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )
    if language_service is not None:
        application.state.language = language_service
    if pricing_service is not None:
        application.state.pricing = pricing_service
    application.mount(
        "/static",
        StaticFiles(directory=ROOT / "static"),
        name="static",
    )

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    @application.get("/api-docs", include_in_schema=False)
    def api_docs():
        return FileResponse(ROOT / "static" / "api.html")

    @application.get("/analysis", include_in_schema=False)
    def analysis():
        return FileResponse(ROOT / "static" / "analysis.html")

    @application.get("/healthz", include_in_schema=False)
    def health():
        return {"status": "ok"}

    @application.get("/api/config")
    def config():
        return {
            "max_text_length": MAX_TEXT_LENGTH,
            "max_turn_length": MAX_TURN_LENGTH,
            "roles": list(ROLES),
            "default_iterations": DEFAULT_ITERATIONS,
            "max_iterations": MAX_ITERATIONS,
        }

    @application.get("/api/samples")
    def samples():
        return {"samples": _load_samples()}

    @application.get("/api/pricing")
    def pricing():
        try:
            return application.state.pricing.get_rates().to_dict()
        except PricingUnavailableError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @application.post("/api/redact", tags=["tasks"], summary="Redact PII from a transcript")
    def redact(request: ConversationRequest):
        """One `ConversationalPIITask` call.

        Returns the redacted transcript plus every detected span. Span text is
        omitted on purpose — it is the PII value, and the caller already holds
        the input it came from.
        """
        conv = _validated_conversation(request)
        started = time.perf_counter()
        result = _azure_guard(lambda: application.state.language.detect_pii(conv))
        duration_ms = (time.perf_counter() - started) * 1000
        return {
            "has_pii": result.has_pii,
            "categories": list(result.categories),
            "redacted_conversation": result.redacted.to_dict(),
            "redacted_text": result.redacted_text,
            "entities": [asdict(entity) for entity in result.entities],
            "characters": conv.billable_characters(),
            "text_records": text_records_for_conversation(conv),
            "duration_ms": duration_ms,
        }

    @application.post(
        "/api/summarize", tags=["tasks"], summary="Summarize a transcript"
    )
    def summarize(request: ConversationRequest):
        """One `ConversationalSummarizationTask` call, issue and resolution
        aspects. Summarizes exactly what you send: pass the output of
        `/api/redact` if the summary must be PII-free.
        """
        conv = _validated_conversation(request)
        started = time.perf_counter()
        summary = _azure_guard(lambda: application.state.language.summarize(conv))
        return {
            "summary": summary,
            "characters": conv.billable_characters(),
            "text_records": text_records_for_conversation(conv),
            "duration_ms": (time.perf_counter() - started) * 1000,
        }

    @application.post(
        "/api/pipeline", tags=["tasks"], summary="Redact and summarize in one call"
    )
    def pipeline(request: PipelineRequest):
        """Both skills, wired the way the dashboard compares them.

        `sequential` redacts first and summarizes the redacted transcript.
        `parallel` starts both at once and re-summarizes only if PII is found,
        which sends un-redacted text to Azure on every request.
        """
        conv = _validated_conversation(request)
        service = BenchmarkService(
            application.state.language.detect_pii,
            application.state.language.summarize,
        )
        runner = (
            service.run_sequential
            if request.mode == "sequential"
            else service.run_parallel
        )
        outcome = _azure_guard(lambda: runner(conv))

        # Costing is best effort: a retail-feed outage must not fail the work.
        try:
            cost = estimate_pipeline_cost(
                outcome, application.state.pricing.get_rates()
            ).to_dict()
        except PricingUnavailableError:
            cost = None

        return {
            "mode": request.mode,
            "has_pii": outcome.has_pii,
            "categories": list(outcome.pii_categories),
            "redacted_text": outcome.redacted_text,
            "summary": outcome.summary,
            "discarded_speculative_summary": outcome.discarded_speculative_summary,
            "total_ms": outcome.total_ms,
            "operations": [asdict(operation) for operation in outcome.operations],
            "cost": cost,
        }

    @application.post(
        "/api/benchmark", tags=["benchmark"], summary="Compare both pipelines"
    )
    def benchmark(request: BenchmarkRequest):
        conv = _validated_conversation(request)

        try:
            rates = application.state.pricing.get_rates()
        except PricingUnavailableError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

        service = BenchmarkService(
            application.state.language.detect_pii,
            application.state.language.summarize,
            iterations=request.iterations,
            warm_up_payload=WARM_UP_CONVERSATION,
        )
        # AzureError also covers the transport failures (ServiceRequestError,
        # ServiceResponseError) that HttpResponseError does not.
        try:
            comparison = service.compare(conv, warm_up=request.warm_up)
        except (AzureError, RuntimeError) as error:
            raise HTTPException(
                status_code=502,
                detail=f"Azure benchmark failed: {error}",
            ) from error

        sequential = comparison.sequential
        parallel = comparison.parallel
        speedup = (
            sequential.total_ms / parallel.total_ms if parallel.total_ms > 0 else 0
        )
        return {
            "characters": conv.billable_characters(),
            "turns": len(conv.turns),
            "iterations": comparison.iterations,
            "warm_up": request.warm_up,
            "speedup": speedup,
            "latency_saved_ms": sequential.total_ms - parallel.total_ms,
            "sequential": _serialize_result(sequential, rates),
            "parallel": _serialize_result(parallel, rates),
            "pricing": rates.to_dict(),
            "projection": tradeoff.project(comparison, rates).to_dict(),
        }

    return application


app = create_app()
