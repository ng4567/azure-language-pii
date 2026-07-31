# Redaction evaluation

Ten hand-labelled call-centre transcripts and a scorer, for answering "does
conversation PII actually catch what we need it to catch?" before committing
to an architecture.

```bash
uv run uvicorn main:app            # in one shell
uv run python evaluation/evaluate.py
uv run python evaluation/evaluate.py --pipeline   # also scan summaries
```

The scorer exits non-zero if any labelled value survived redaction, so it can
gate a pipeline.

## What is measured

**Leak rate** is the headline: did a labelled PII value survive verbatim in
the redacted output? That is the business requirement, and it is checked
against the redacted text itself rather than inferred from the span list. A
span can be missed while the value is still masked (an adjacent detection
covered it), and a span can be reported while the value leaks (the mask was
too narrow), so span recall alone would mislead.

**Span recall and precision** explain *why* something leaked, and are reported
per category.

Labels live beside each transcript in `datasets/*.json`. Edit them — the
point is that a human decided what counts as PII here. Two files carry an
intentionally empty `expected` list: they are negative controls where any
detection is over-redaction.

## Results as measured

Against a Standard tier resource in `eastus2`, 41 labelled spans:

| Configuration | Span recall | Values leaked |
| --- | --- | --- |
| GA (`2024-11-01`), default categories | 98% | **1** |
| Preview (`2025-11-15-preview`), explicit categories | 100% | 0 |

### Dates of birth are not redacted on the GA API

The single GA leak is `"March 14, 1978"` in an identity-verification
transcript. This is not a tuning problem: on `2024-11-01`, `DateOfBirth` and
`Date` are rejected outright as `piiCategories` values —

```
Invalid value for parameter 'piiCategories': 'DateOfBirth'
```

— and the date passes through un-redacted. On `2025-11-15-preview` the same
transcript redacts cleanly at confidence 1.0. Those categories are in preview,
so using them means accepting Microsoft's preview terms. To opt in:

```dotenv
LANGUAGE_API_VERSION=2025-11-15-preview
PII_CATEGORIES=Person,Phone,Email,Address,CreditCard,USSocialSecurityNumber,DateOfBirth,Age,ABARoutingNumber,BankAccountNumber,CVV,NumericIdentifier
```

Naming `piiCategories` explicitly is required — the preview categories are not
included in the default set.

### What GA handles well

- **Spoken-digit card numbers.** `"four one one one, one one one one, …"` is
  detected as `CreditCard`, as is a spoken expiry (`"April twenty twenty
  eight"`). A speech pipeline that has not applied inverse text normalisation
  does not silently defeat detection, which was the main worry going in.
- **Card fragments split across turns.** Four-digit fragments interleaved with
  agent backchannels are each caught.
- **Third-party PII.** Names, phones and emails belonging to a spouse or child
  rather than the caller are detected.
- **Bank details.** Routing and account numbers are caught, though reported as
  `NumericIdentifier` rather than a specific financial category.

### Two over-redaction behaviours worth pricing in

**Last-four references are redacted.** `"the card ending in 4242"` is masked
as `CreditCard` at 0.99. PCI DSS permits displaying the last four, so this is
avoidable data loss. It matters more than it looks: the speculative-parallel
pipeline discards its summary on *any* detection, so transcripts whose only
"PII" is a permitted last-four reference still count as dirty. That inflates
the effective prevalence `p` and therefore the cost of the parallel design.

**Backchannels near card numbers get masked.** In the split-card transcript,
the agent's `"Got it."`, `"Mhm."` and `"And the last four?"` are each labelled
`CreditCard` (0.74–0.86) and fully masked. Six of nine turns disappear. Nothing
leaks, but the transcript is degraded and the summary has far less to work
with. Low-confidence detections on short turns are the pattern to watch; a
confidence floor applied to short turns would recover most of it.

### Category naming

Span recall is 100% on preview but category agreement is 93%. The gaps are
naming, not detection: SSNs come back as `NumericIdentifier`, and bank
routing/account numbers likewise. If downstream logic branches on category
rather than on presence, verify against these actual values rather than the
documented category list.

## Caveats

These are synthetic transcripts written to probe specific failure modes, not a
sample of production traffic, and the prevalence of each pattern here says
nothing about its prevalence in a real corpus. Every value is fabricated. Ten
transcripts is enough to find categorical gaps like the date-of-birth one; it
is not enough to estimate a leak rate with any precision.
