from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Literal

from azure.core.exceptions import AzureError
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
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


class BenchmarkRequest(BaseModel):
    conversation: list[TurnModel]
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


def _validated_conversation(request: BenchmarkRequest) -> Conversation:
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
        version="3.0.0",
        lifespan=lifespan,
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

    @application.post("/api/benchmark")
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
            "projection": tradeoff.project(sequential, parallel, rates).to_dict(),
        }

    return application


app = create_app()
