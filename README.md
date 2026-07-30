# Azure PII and Summarization Benchmark

A local FastAPI dashboard for comparing two Azure AI Language pipelines:

1. **Sequential:** detect PII, redact the input, then summarize.
2. **Speculative parallel:** detect PII and summarize concurrently. If PII is
   detected, discard the speculative summary and summarize the redacted input
   again. If no PII is detected, use the speculative summary.

The dashboard reports measured latency and retail cost for both, then projects
them across a range of corpus PII prevalence so you can find the break-even
point.

## What the numbers mean

**Latency is a median of N iterations, not a single sample.** Both pipelines are
network bound, so one draw is not a usable basis for a decision. Each run:

- **warms up first.** Each Azure client caches its bearer token on its own
  pipeline policy, so the first call through each client pays token acquisition
  (a subprocess spawn under `az login`) plus a TLS handshake. An untimed warm-up
  pair absorbs that, instead of charging it to whichever pipeline happened to
  run first.
- **alternates the order** of the two pipelines across iterations, so neither
  gets a systematic advantage from running second against warm connections.
- **reports min, median, and p95.** With small iteration counts the high
  percentile is coarse by construction — read it as spread, not as a
  distribution tail.

Summarization is a long-running operation. The SDK's default poll interval is
5 seconds, which would quantise every measurement to the poll cadence rather
than the service's actual latency — and the speculative-parallel pipeline would
pay that artefact twice, since it puts two summarizations on its critical path.
This app polls at 1 second. Reported summarization latency is therefore submit
plus poll wall time, not pure inference time, and it still carries up to one
poll interval of granularity.

The warm-up costs two extra Azure calls per benchmark run, which are not
included in the reported per-document cost.

## The corpus projection

A single document is one point. What actually decides the trade-off is `p`, the
fraction of your documents that contain **any** PII, because that is how often
the speculative summary gets thrown away:

```
E[latency_sequential] = t_pii + t_summary                     (flat in p)
E[latency_parallel]   = t_overlapped + p × t_summary
E[cost_parallel]      = cost_sequential + p × cost_summary
```

Parallel is never cheaper — it buys latency with a second summarization. The
break-even prevalence, where parallel stops being faster, is
`(t_sequential − t_overlapped) / t_summary`. The dashboard shows it, plus the
price of a second of latency saved at your chosen `p`.

Two caveats on reading the sweep:

- **`p` is not a free axis.** Longer documents are more likely to contain an
  entity, so `p` and document length move together. The projection describes a
  corpus whose documents resemble the one you measured — it is not a
  length-independent model.
- **Within-document PII density does not enter the model.** Billing is per
  1,000 characters of input per operation, and Azure's `redacted_text` replaces
  each entity with asterisks of equal length, so a document with one entity and
  one with fifty cost and time the same. Only presence matters, because the
  pipeline discards the speculative summary on *any* detection.

  That last point is a property of this implementation, not a law. Extractive
  summarization returns verbatim input sentences with offsets, so a smarter
  pipeline could compare the selected sentences against the PII spans and only
  re-summarize on actual overlap — which would make density matter and lower
  the retry rate. This app does not do that. (The same trick would **not** be
  sound for abstractive summarization.)

## Privacy posture

The speculative call sends **un-redacted** text to Azure, and service-side input
logging is left at the Azure default (inputs retained 48 hours for
troubleshooting). That is an accepted trade-off for this benchmark. If you need
to change it, pass `disable_service_logs=True` in `azure_language.py`.

What the backend itself guarantees is narrower: an unsafe speculative summary is
never returned to the browser or logged locally — when PII is found it is
discarded and replaced by a summary of the redacted text.

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Azure CLI authenticated with `az login`
- An Azure AI Language resource using the Standard pricing tier
- A data-plane role on that resource for your signed-in identity

## Configure

Find the root resource endpoint without retrieving API keys:

```bash
az cognitiveservices account list \
  --query "[].{name:name,region:location,endpoint:properties.endpoint}" \
  --output table
```

Copy `.env.example` to `.env` and set:

```dotenv
LANGUAGE_ENDPOINT=https://<resource-name>.cognitiveservices.azure.com
AZURE_REGION=eastus2
```

Use the root `cognitiveservices.azure.com` endpoint, not a Foundry project
endpoint containing `/api/projects/`. Authentication uses
`DefaultAzureCredential`, which picks up your Azure CLI login locally.

## Run

```bash
uv sync
az login
uv run uvicorn main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The UI provides custom input and two synthetic fixtures:

- `samples/PII.txt`
- `samples/No PII.txt`

The fixtures have exactly the same character count for an equivalent payload
comparison. Note that both are under 1,000 characters, so both round up to a
single text record — the equal length matters for latency comparability, not
for cost.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/config` | Input limits and iteration defaults used by the UI |
| `GET /api/samples` | The two bundled fixtures |
| `GET /api/pricing` | Current regional retail meters |
| `POST /api/benchmark` | Run the comparison |
| `GET /healthz` | Health probe, does not call Azure |

`POST /api/benchmark` accepts `{"text": ..., "iterations": 3, "warm_up": true}`
and returns aggregated timings, per-document cost, and the prevalence
projection.

## Run in Docker

Build the production image:

```bash
docker build -t azure-language-pii .
```

`DefaultAzureCredential` cannot automatically reuse a host Azure CLI login
from inside a container. For local Docker, use a service principal with access
to the Azure AI Language resource. Export the values from `.env` into the
current shell before forwarding only the required variables:

```bash
set -a
. ./.env
set +a

docker run --rm -p 8000:8000 \
  -e LANGUAGE_ENDPOINT \
  -e AZURE_REGION \
  -e AZURE_TENANT_ID \
  -e AZURE_CLIENT_ID \
  -e AZURE_CLIENT_SECRET \
  azure-language-pii
```

The same variables can be supplied to Compose:

```bash
docker compose up --build
```

Do not bake credentials into the image or pass `.env` as a build secret. When
deploying to Azure Container Apps, App Service, or AKS, enable managed identity
and grant it the appropriate Azure AI Language data-plane role. In that case,
only `LANGUAGE_ENDPOINT` and `AZURE_REGION` are required application settings.

The container runs as non-root UID `10001`, listens on port `8000`, and exposes
`GET /healthz` for health probes.

## Pricing methodology

Microsoft defines one Azure AI Language text record as 1,000 characters. Each
started 1,000 characters is counted separately for every PII or summarization
operation. Characters are counted in UTF-16 code units, matching how Azure
bills and how the default `string_index_type` measures offsets.

The app loads current regional USD retail rates from Microsoft's
[Azure Retail Prices API](https://prices.azure.com/api/retail/prices) using
these Standard-tier meters:

- `Standard Text Records` for PII detection
- `Standard Summarization Text Records` for extractive summarization

The feed is paginated and mixes `Consumption` with `DevTestConsumption` rows and
one row per commitment tier, so the loader follows `NextPageLink` and keeps only
pay-as-you-go, first-tier, per-1K rows for the configured region. Rates are
cached for an hour.

The displayed amount is a retail estimate, not a bill. Negotiated rates,
commitment tiers, free grants, taxes, and currency conversion can change the
actual cost.

Official references:

- [Azure AI Language pricing](https://azure.microsoft.com/pricing/details/language/)
- [Azure Language data limits and 1,000-character billing unit](https://learn.microsoft.com/azure/ai-services/language-service/concepts/data-limits)
- [Azure Retail Prices API](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices)

## Test

```bash
uv run python -m unittest discover -v
```
