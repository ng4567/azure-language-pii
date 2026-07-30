from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterator
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from benchmark import (
    PII_OPERATION,
    SUMMARY_OPERATIONS,
    PipelineResult,
    records_for_length,
)

RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
PRICING_PAGE_URL = "https://azure.microsoft.com/pricing/details/language/"
DATA_LIMITS_URL = (
    "https://learn.microsoft.com/azure/ai-services/language-service/concepts/data-limits"
)

PII_METER_NAME = "Standard Text Records"
SUMMARY_METER_NAME = "Standard Summarization Text Records"

# The retail feed pages at 100 items; this bounds a pathological crawl.
MAX_PRICING_PAGES = 20
DEFAULT_CACHE_TTL_SECONDS = 3600.0


class PricingUnavailableError(RuntimeError):
    """Raised when official Microsoft retail pricing cannot be resolved."""


@dataclass(frozen=True)
class MeterRate:
    meter_name: str
    retail_price_per_1000: float
    effective_start_date: str


@dataclass(frozen=True)
class RetailRates:
    region: str
    currency: str
    pii_per_record_usd: float
    summary_per_record_usd: float
    meters: tuple[MeterRate, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "pricing_page_url": PRICING_PAGE_URL,
            "data_limits_url": DATA_LIMITS_URL,
            "retail_api_url": RETAIL_PRICES_URL,
        }


@dataclass(frozen=True)
class CostEstimate:
    pii_records: int
    summary_records: int
    total_records: int
    total_usd: float

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


def _billable_items(items: list[dict[str, Any]], region: str) -> Iterator[dict[str, Any]]:
    """Yield only the pay-as-you-go, first-tier, per-1K meters for ``region``.

    The retail feed mixes ``Consumption`` with ``DevTestConsumption`` and
    reservation rows under the same ``meterName``, and returns one row per
    commitment tier. Without this filter a later row silently overwrites the
    rate we actually want.
    """
    for item in items:
        item_type = item.get("type")
        if item_type is not None and item_type != "Consumption":
            continue
        item_region = item.get("armRegionName")
        if region and item_region is not None and item_region != region:
            continue
        if item.get("unitOfMeasure") != "1K":
            continue
        if float(item.get("tierMinimumUnits") or 0) != 0:
            continue
        yield item


def parse_retail_rates(items: list[dict[str, Any]], region: str) -> RetailRates:
    by_name: dict[str, dict[str, Any]] = {}
    for item in _billable_items(items, region):
        # First match wins so the result is deterministic across feed ordering.
        by_name.setdefault(item["meterName"], item)

    pii_item = by_name.get(PII_METER_NAME)
    summary_item = by_name.get(SUMMARY_METER_NAME)
    if pii_item is None:
        raise PricingUnavailableError(
            f"Microsoft retail pricing did not return {PII_METER_NAME}."
        )
    if summary_item is None:
        raise PricingUnavailableError(
            f"Microsoft retail pricing did not return {SUMMARY_METER_NAME}."
        )

    meters = tuple(
        MeterRate(
            meter_name=item["meterName"],
            retail_price_per_1000=float(item["retailPrice"]),
            effective_start_date=item["effectiveStartDate"],
        )
        for item in (pii_item, summary_item)
    )
    return RetailRates(
        region=region,
        currency="USD",
        pii_per_record_usd=float(pii_item["retailPrice"]) / 1000,
        summary_per_record_usd=float(summary_item["retailPrice"]) / 1000,
        meters=meters,
    )


def estimate_pipeline_cost(result: PipelineResult, rates: RetailRates) -> CostEstimate:
    """Cost of a single execution of ``result``'s pipeline.

    ``result.operations`` carries one entry per distinct operation, so this is
    a per-document cost regardless of how many iterations were measured.
    """
    pii_records = 0
    summary_records = 0
    for operation in result.operations:
        records = records_for_length(operation.characters)
        if operation.name == PII_OPERATION:
            pii_records += records
        elif operation.name in SUMMARY_OPERATIONS:
            summary_records += records
        else:
            raise ValueError(f"Unknown billable operation: {operation.name!r}")
    return CostEstimate(
        pii_records=pii_records,
        summary_records=summary_records,
        total_records=pii_records + summary_records,
        total_usd=(
            pii_records * rates.pii_per_record_usd
            + summary_records * rates.summary_per_record_usd
        ),
    )


class RetailPricing:
    """Loads regional retail rates, cached with a TTL so prices can refresh."""

    def __init__(
        self,
        region: str,
        ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        timeout: float = 10.0,
    ) -> None:
        self._region = region
        self._ttl_seconds = ttl_seconds
        self._timeout = timeout
        self._lock = threading.Lock()
        self._cached: RetailRates | None = None
        self._cached_at = 0.0

    def get_rates(self) -> RetailRates:
        with self._lock:
            now = time.monotonic()
            if self._cached is not None and now - self._cached_at < self._ttl_seconds:
                return self._cached
            rates = parse_retail_rates(self._fetch_items(), self._region)
            self._cached = rates
            self._cached_at = now
            return rates

    def _fetch_items(self) -> list[dict[str, Any]]:
        query = urlencode(
            {
                "$filter": (
                    f"armRegionName eq '{self._region}' and "
                    "productName eq 'Azure Language' and skuName eq 'Standard'"
                )
            }
        )
        url = f"{RETAIL_PRICES_URL}?{query}"
        items: list[dict[str, Any]] = []
        for _ in range(MAX_PRICING_PAGES):
            payload = self._get_json(url)
            items.extend(payload.get("Items", []))
            url = payload.get("NextPageLink")
            if not url:
                break
        return items

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={"User-Agent": "azure-language-pipeline-benchmark/1.0"},
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                return json.load(response)
        except (OSError, ValueError) as error:
            raise PricingUnavailableError(
                f"Unable to load official Microsoft retail pricing: {error}"
            ) from error
