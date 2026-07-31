from __future__ import annotations

import os
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

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
from pricing import PricingUnavailableError, RetailPricing, estimate_pipeline_cost

ROOT = Path(__file__).parent
MAX_TEXT_LENGTH = 5_000


class BenchmarkRequest(BaseModel):
    text: str
    iterations: int = Field(default=DEFAULT_ITERATIONS, ge=1, le=MAX_ITERATIONS)
    warm_up: bool = True


@lru_cache(maxsize=1)
def _load_samples() -> dict[str, dict[str, str | int]]:
    samples = {}
    for key, filename, label in (
        ("pii", "PII.txt", "PII sample"),
        ("no_pii", "No PII.txt", "No-PII sample"),
    ):
        content = (ROOT / "samples" / filename).read_text(encoding="utf-8").strip()
        samples[key] = {
            "label": label,
            "filename": filename,
            "content": content,
            "characters": billable_characters(content),
        }
    return samples


def _serialize_result(result: PipelineResult, rates) -> dict[str, object]:
    return {
        **result.to_dict(),
        "cost": estimate_pipeline_cost(result, rates).to_dict(),
    }


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
        title="Azure PII and Summarization Benchmark",
        version="2.0.0",
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
        text = request.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Text is required.")
        # Measured the way Azure measures it, so astral characters cannot slip
        # a document past this guard and into a service-side length rejection.
        characters = billable_characters(text)
        if characters > MAX_TEXT_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Text must be {MAX_TEXT_LENGTH:,} characters or fewer.",
            )

        try:
            rates = application.state.pricing.get_rates()
        except PricingUnavailableError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

        service = BenchmarkService(
            application.state.language.detect_pii,
            application.state.language.summarize,
            iterations=request.iterations,
        )
        # AzureError also covers the transport failures (ServiceRequestError,
        # ServiceResponseError) that HttpResponseError does not.
        try:
            comparison = service.compare(text, warm_up=request.warm_up)
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
            "characters": characters,
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
