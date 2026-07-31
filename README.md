# Azure Conversation PII and Summarization Benchmark

A local FastAPI dashboard for comparing two Azure AI Language pipelines over
call-center style transcripts, using the **conversation** endpoints:
`ConversationalPIITask` for PII redaction and `ConversationalSummarizationTask`
(issue + resolution aspects) for summarization.

1. **Sequential:** detect conversation PII, redact every turn, then summarize
   the redacted transcript.
2. **Speculative parallel:** detect PII and summarize concurrently. If PII is
   detected, discard the speculative summary and summarize the redacted
   transcript again. If no PII is detected, use the speculative summary.

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

**Both conversation tasks are asynchronous long-running jobs** — conversation
PII has no synchronous variant. The SDK's default poll interval is 5 seconds,
which would quantise every measurement to the poll cadence rather than the
service's actual latency — and the speculative-parallel pipeline would pay that
artefact on every operation on its critical path. This app polls at 1 second.
Reported latency is therefore submit plus poll wall time, not pure inference
time, and every operation carries up to one poll interval of granularity.

Because PII detection is itself a polled job, its latency floor is far higher
than the synchronous text PII endpoint's. That pushes sequential latency up and
moves the break-even prevalence relative to a text-PII benchmark — which is
exactly why measuring the conversation endpoints you actually plan to use
matters.

The warm-up costs two extra Azure calls per benchmark run, which are not
included in the reported per-transcript cost.

## The corpus projection

A single transcript is one point. What actually decides the trade-off is `p`,
the fraction of your transcripts that contain **any** PII, because that is how
often the speculative summary gets thrown away:

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

- **`p` is not a free axis.** Longer transcripts are more likely to contain an
  entity, so `p` and transcript length move together. The projection describes
  a corpus whose transcripts resemble the one you measured — it is not a
  length-independent model.
- **Within-transcript PII density does not enter the model.** Billing is per
  1,000 characters of input per operation, and the conversation PII task's
  `redactedContent` masks each entity with characters of equal length, so a
  transcript with one entity and one with fifty cost and time the same. Only
  presence matters, because the pipeline discards the speculative summary on
  *any* detection.

  Note that conversation summarization is **abstractive** — the issue and
  resolution summaries are generated text, not verbatim input sentences — so
  span-overlap tricks that are sound for extractive summarization (only
  re-summarizing when a selected sentence overlaps a PII span) do **not**
  transfer here. On any detection, the speculative summary must be discarded.

## Privacy posture

The speculative call sends the **un-redacted** transcript to Azure, and
service-side input logging is left at the Azure default (inputs retained 48
hours for troubleshooting). That is an accepted trade-off for this benchmark.
If you need to change it, add `"loggingOptOut": true` to the task parameters in
`azure_language.py`.

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

Optional settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LANGUAGE_API_VERSION` | `2024-11-01` | Conversation API version |
| `PII_CATEGORIES` | unset | Comma-separated categories to request explicitly |
| `CORS_ALLOW_ORIGINS` | unset | Comma-separated browser origins allowed to call the API |

**Dates of birth are not redacted on the GA API.** `DateOfBirth` is a preview
category and is rejected outright on `2024-11-01`. Opting in means accepting
Microsoft's preview terms — see [evaluation/README.md](evaluation/README.md)
for the measurement and the exact settings.

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

The UI provides a structured turn editor (Agent/Customer roles) and two
synthetic transcript fixtures:

- `samples/PII.json` — includes a name, phone number, email address, and a
  credit card number, since PCI redaction is the motivating scenario
- `samples/No PII.json`

The fixtures have the same turn count and exactly the same total character
count for an equivalent payload comparison. Note that both are under 1,000
characters in total, so both round up to a single text record — the equal
length matters for latency comparability, not for cost.

Input limits: the conversation API enforces **1,000 characters per turn**
(counted in UTF-16 code units); this app additionally caps a transcript at
5,000 characters in total.

## API

The backend is usable on its own — nothing here needs the dashboard. Browsable
docs are at `/api-docs`, interactive Swagger at `/docs`, and the schema at
`/openapi.json`.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/redact` | Redact PII from a transcript, and report every detected span |
| `POST /api/summarize` | Summarize a transcript (issue + resolution aspects) |
| `POST /api/pipeline` | Redact and summarize together, `sequential` or `parallel` |
| `POST /api/benchmark` | Run both pipelines N times and compare latency and cost |
| `GET /api/config` | Input limits, allowed roles, and iteration defaults |
| `GET /api/samples` | The two bundled fixtures |
| `GET /api/pricing` | Current regional retail meters |
| `GET /healthz` | Health probe, does not call Azure |

Every `POST` takes the same `conversation` array:

```bash
curl -X POST http://localhost:8000/api/redact \
  -H "Content-Type: application/json" \
  -d '{"conversation": [{"role": "Customer", "text": "Maya Chen, card 4111 1111 1111 1111."}]}'
```

Detected spans carry category, offset, length, and confidence — never the
matched text, which is the PII value itself. Cross-origin browser callers need
`CORS_ALLOW_ORIGINS` set; it is off by default. There is no authentication in
front of these endpoints, so keep the container behind your own network
controls.

`POST /api/benchmark` accepts

```json
{
  "conversation": [
    {"role": "Agent", "text": "Who am I speaking with?"},
    {"role": "Customer", "text": "This is Maya Chen."}
  ],
  "iterations": 3,
  "warm_up": true
}
```

and returns aggregated timings, per-transcript cost, and the prevalence
projection. The `redacted_text` field renders the redacted transcript one
`Role: text` line per turn.

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
operation, summed across all turns of the transcript. Characters are counted
in UTF-16 code units, matching how Azure bills and how the conversation API
enforces its per-turn limit.

The app loads current regional USD retail rates from Microsoft's
[Azure Retail Prices API](https://prices.azure.com/api/retail/prices) using
these Standard-tier meters (the feed has no conversation-specific meters; the
conversation skills bill under the same records):

- `Standard Text Records` for conversation PII detection
- `Standard Summarization Text Records` for conversation summarization

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

## Evaluating redaction quality

`evaluation/` holds ten hand-labelled transcripts and a scorer that reports
recall, over-detection, and whether any labelled value survived redaction:

```bash
uv run python evaluation/evaluate.py
```

It exits non-zero if anything leaked. See
[evaluation/README.md](evaluation/README.md) for measured results, including
which patterns the service handles well and where it over-redacts.
