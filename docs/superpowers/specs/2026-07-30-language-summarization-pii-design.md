# Azure Language Summarization and PII Sample Design

## Purpose

Provide a minimal runnable Python sample that sends the hard-coded text
`"hello, world"` to Azure AI Language for Conversational Summarization,
Extractive Summarization, and conversation PII detection.

## Architecture

`main.py` will load the Azure AI Language resource endpoint from
`LANGUAGE_ENDPOINT` and create one shared `DefaultAzureCredential`. It will
pass that credential to `ConversationAnalysisClient` for Conversational
Summarization and to `TextAnalyticsClient` for Extractive Summarization and
PII detection. Two clients are required because Azure exposes conversational
and document analysis through distinct SDK clients. The application will not
load, store, print, or require API keys. Local execution relies on `az login`;
deployed execution can use a managed identity with the appropriate Azure AI
Language role.

## Request and result flow

1. Validate that `LANGUAGE_ENDPOINT` is configured as the root endpoint of an
   Azure AI Language resource.
2. Authenticate for the Cognitive Services scope through
   `DefaultAzureCredential`.
3. Submit a single hard-coded conversation item containing `"hello, world"` to
   `ConversationAnalysisClient` with a `ConversationalSummarizationTask`.
   Submit the same text to `TextAnalyticsClient` for extractive summarization
   and PII detection.
4. Await each long-running summarization operation, then print its result in a
   labeled, readable form. Print the PII entities and redacted text separately.
5. Surface authentication, configuration, service, and per-document errors
   with actionable messages and a nonzero process exit.

## Configuration and documentation

An `.env.example` will document the single endpoint variable without including
credentials. The README will explain how to discover the Language resource
endpoint using Azure CLI, configure the environment, run `az login`, and
execute the sample. The code will preserve the existing dependency-management
conventions and add packages only when required by the selected SDK API.

## Validation

Validation will cover syntax/import compatibility and an executable
configuration/authentication path when the logged-in Azure identity has access
to the discovered resource. No secret values will be added to tracked files or
command output.
