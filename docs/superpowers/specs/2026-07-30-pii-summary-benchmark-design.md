# PII and Summarization Pipeline Benchmark Design

## Goal

Build a local FastAPI website that compares two privacy-aware Azure AI
Language pipelines against identical input:

- **Sequential:** detect PII, then summarize the redacted input.
- **Speculative parallel:** detect PII and summarize the original input
  concurrently. If PII is found, discard the speculative summary and summarize
  the redacted input again; otherwise return the speculative summary.

The application must never return, render, or persist a speculative summary
created from text containing PII.

## Application shape

FastAPI serves a JSON API and a dependency-free HTML/CSS/JavaScript dashboard.
The dashboard supports custom text and two bundled text files, `PII.txt` and
`No PII.txt`. The bundled files have exactly the same character count so their
latency and price comparison uses equivalent payload sizes.

The benchmark uses Azure AI Language extractive summarization. The existing
root Language endpoint and `DefaultAzureCredential` provide authentication;
no service key is needed or exposed to the browser.

## Benchmark execution

Each pipeline records total wall-clock latency and individual operation
latencies with `time.perf_counter`. The sequential pipeline calls PII detection
and then one summarization. The speculative pipeline starts the first two calls
in a two-worker executor. It uses the first summary only when no PII exists.
When PII exists, it waits for both speculative operations, discards that
summary, and makes a second summarization call using redacted input.

The response includes detected PII categories, redacted input, safe summary,
operation timings, call counts, billable text records, and estimated retail
cost. It never includes detected entity text or the unsafe speculative
summary.

## Pricing

Microsoft documents one text record as 1,000 characters. The backend resolves
the current USD prices for `Standard Text Records` and
`Standard Summarization Text Records` from the official Azure Retail Prices
API for the configured region. It calculates each operation as
`ceil(characters / 1000)` records and applies the relevant per-1,000-record
retail rate.

The UI identifies costs as estimates, displays the retrieved meter names,
region, and effective dates, and links both the Microsoft pricing page and
Azure Language data-limit documentation. It explains that negotiated,
commitment-tier, free-tier, tax, and currency effects are excluded.

## API and UI

- `GET /api/samples` returns bundled sample names, contents, and character
  counts.
- `GET /api/pricing` returns current Microsoft retail meter data.
- `POST /api/benchmark` accepts text and returns sequential and parallel
  results.
- `GET /` serves the dashboard.

The dashboard shows side-by-side latency and estimated cost cards, relative
latency bars, speedup, call counts, PII outcome, operation-level timing,
redacted input, and safe summaries. A warning explains that speculative
parallel execution sends the original text to summarization before PII status
is known and can incur an extra summary charge when PII is found.

## Failure handling and validation

Configuration errors, Azure errors, and pricing lookup errors return explicit
non-success API responses without credentials or sensitive text. Input is
required and capped below Azure's document limit. Unit tests cover equal-sized
fixtures, record rounding, cost calculation, no-PII and PII branches for both
pipelines, safe speculative-result disposal, and API validation. A live smoke
test verifies the logged-in Azure identity and configured resource.
