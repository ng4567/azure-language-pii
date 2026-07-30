# Azure Language PII Pipeline Benchmark Design

## Purpose

Give a customer the evidence to choose between running PII detection
sequentially with summarization or speculatively in parallel. The deliverable
is a local FastAPI dashboard that measures both pipelines against a real Azure
AI Language resource, prices them from official Microsoft retail meters, and
projects the trade-off across corpus PII prevalence.

## Pipelines under test

**Sequential.** Detect PII, redact, then summarize the redacted text. Two
billable operations. Latency is `t_pii + t_summary` regardless of whether the
document contains PII.

**Speculative parallel.** Start PII detection and summarization concurrently on
the raw text. If no PII is found, the speculative summary is used as-is and the
run costs the same as sequential while completing in
`max(t_pii, t_summary)`. If PII is found, the speculative summary is discarded
and the redacted text is summarized again — three billable operations and
`max(t_pii, t_summary) + t_summary` of latency.

Parallel therefore buys latency with a conditional extra summarization. It is
never cheaper.

## Measurement requirements

The comparison is only meaningful if the measurement is not biased toward one
pipeline, which imposes four constraints.

1. **Warm up before timing.** Each `TextAnalyticsClient` caches its bearer token
   on its own pipeline policy, so the first call through each client pays token
   acquisition plus a TLS handshake. Untimed warm-up calls on both clients
   absorb this. Without it, whichever pipeline ran first would carry the entire
   one-time cost.
2. **Alternate pipeline order** across iterations, so neither accrues a
   residual advantage from consistently running second.
3. **Repeat and aggregate.** Report median, min, and p95 across N iterations
   rather than a single network-bound sample.
4. **Override the LRO poll interval.** Extractive summarization is a
   long-running operation whose SDK default poll interval is 5 seconds. Left
   alone it quantises every summarization measurement to the poll cadence, and
   the parallel pipeline pays that artefact twice. The app polls at 1 second and
   documents that the metric is submit-plus-poll wall time.

## Cost model

One text record is 1,000 characters, counted in UTF-16 code units, charged per
operation with each started 1,000 characters rounded up. Rates come from the
Azure Retail Prices API for the configured region, restricted to pay-as-you-go,
first-tier, per-1K rows of the `Standard Text Records` and
`Standard Summarization Text Records` meters. The feed is paginated and mixes
row types, so the loader follows `NextPageLink` and filters on `type`,
`armRegionName`, `unitOfMeasure`, and `tierMinimumUnits`.

## Prevalence projection

The measured document is a single point; the decision turns on `p`, the fraction
of the corpus containing any PII.

```
E[latency_sequential] = t_sequential                       (flat in p)
E[latency_parallel]   = t_overlapped + p × t_summary
E[cost_parallel]      = cost_sequential + p × cost_summary
break-even p          = (t_sequential − t_overlapped) / t_summary
```

`t_overlapped` is measured rather than modelled as `max()`: it is the parallel
run's wall time minus the conditional retry, so it already includes real thread
and connection contention.

Two limits are stated in the UI rather than hidden. `p` is not independent of
document length, since longer documents are likelier to contain an entity — the
projection describes a corpus resembling the measured document. And
within-document PII density does not enter the model, because billing is per
input character, redaction preserves length, and the pipeline discards on any
detection.

## Out of scope

Span-aware speculation — keeping the speculative summary unless a selected
summary sentence overlaps a PII span — would lower the retry rate below `p` and
make density matter. It is sound for extractive summarization because output
sentences are verbatim input spans, but not for abstractive. It is deliberately
not implemented here.

Service-side input logging is left at the Azure default. The speculative path
sends un-redacted text to Azure; this is an accepted trade-off for the
benchmark, documented in the README rather than mitigated.

## Configuration

`LANGUAGE_ENDPOINT` must be the root Language resource endpoint, not a Foundry
project endpoint. `AZURE_REGION` selects the pricing region. Authentication is
`DefaultAzureCredential` throughout; no API keys are read, stored, or logged.
