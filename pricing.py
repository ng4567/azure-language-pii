from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from benchmark import PipelineResult, text_records

RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
PRICING_PAGE_URL = "https://azure.microsoft.com/pricing/details/language/"
DATA_LIMITS_URL = (
    "https://learn.microsoft.com/azure/ai-services/language-service/concepts/data-limits"
)


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


def parse_retail_rates(items: list[dict[str, Any]], region: str) -> RetailRates:
    by_name = {
        item["meterName"]: item
        for item in items
        if item.get("unitOfMeasure") == "1K"
        and float(item.get("tierMinimumUnits", 0)) == 0
    }
    pii_item = by_name.get("Standard Text Records")
    summary_item = by_name.get("Standard Summarization Text Records")
    if pii_item is None:
        raise RuntimeError("Microsoft retail pricing did not return Standard Text Records.")
    if summary_item is None:
        raise RuntimeError(
            "Microsoft retail pricing did not return Standard Summarization Text Records."
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


def estimate_pipeline_cost(
    result: PipelineResult, rates: RetailRates
) -> CostEstimate:
    pii_records = 0
    summary_records = 0
    for operation in result.operations:
        records = text_records("x" * operation.characters)
        if operation.name == "pii":
            pii_records += records
        else:
            summary_records += records
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
    def __init__(self, region: str) -> None:
        self._region = region

    @lru_cache(maxsize=1)
    def get_rates(self) -> RetailRates:
        query = urlencode(
            {
                "$filter": (
                    f"armRegionName eq '{self._region}' and "
                    "productName eq 'Azure Language' and skuName eq 'Standard'"
                )
            }
        )
        request = Request(
            f"{RETAIL_PRICES_URL}?{query}",
            headers={"User-Agent": "azure-language-pipeline-benchmark/1.0"},
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.load(response)
        except (OSError, ValueError) as error:
            raise RuntimeError(
                f"Unable to load official Microsoft retail pricing: {error}"
            ) from error
        return parse_retail_rates(payload["Items"], self._region)
