# Azure PII and Summarization Benchmark

A FastAPI dashboard for comparing two Azure AI Language pipelines:

1. **Sequential:** detect PII, redact the input, then summarize.
2. **Speculative parallel:** detect PII and summarize concurrently. If PII is
   detected, discard the speculative summary and summarize the redacted input
   again. If no PII is detected, use the speculative summary.

The backend never returns or logs the unsafe speculative summary.

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

The UI provides custom input and a one-click matched sample suite:

- `samples/PII.txt`
- `samples/No PII.txt`

The fixtures have exactly the same character count for an equivalent payload
comparison. The suite runs both fixtures through both strategies and displays
all four latency and retail-cost results together.

Detected PII is highlighted over the original input using the character offsets
returned by Azure AI Language. Redacted text remains the internal input to safe
summarization, but the dashboard deliberately shows the annotated original so
the detection behavior is visible. The backend requests `UnicodeCodePoint`
offsets and returns no entity values beyond the original text supplied by the
user.

The API exposes:

- `POST /api/benchmark` for one custom input
- `POST /api/benchmark/samples` for the matched four-way sample suite

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

## Deploy to Azure Container Apps

The deployed demo is available at
[https://ca-language-pii-dev-27a7.yellowtree-1fae237e.eastus2.azurecontainerapps.io](https://ca-language-pii-dev-27a7.yellowtree-1fae237e.eastus2.azurecontainerapps.io).

The Bicep deployment reuses the named resource group, registry, Container Apps
environment, Log Analytics workspace, and Azure AI Language resource in
`infra/main.bicep`. Update those names for another Azure environment.

Deploy the placeholder revision and managed-identity roles first:

```bash
DEPLOYER_OBJECT_ID="$(az ad signed-in-user show --query id -o tsv)"

az deployment sub create \
  --name language-pii-infra-27a7 \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters deployerObjectId="$DEPLOYER_OBJECT_ID"
```

After the `AcrPull` role is visible for the Container App identity, build the
image and switch to it:

```bash
az acr build \
  --registry regngasdf \
  --image azure-language-pii:<tag> \
  --file Dockerfile.azure .

az deployment sub create \
  --name language-pii-app-27a7 \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters deployerObjectId="$DEPLOYER_OBJECT_ID" \
               containerImage=regngasdf.azurecr.io/azure-language-pii:<tag>
```

The Container App uses its system-assigned managed identity for both ACR image
pulls and Azure AI Language access. Do not deploy the local service-principal
values from `.env`.

## Pricing methodology

Microsoft defines one Azure AI Language text record as 1,000 characters. Each
started 1,000 characters is counted separately for every PII or summarization
operation. The app loads current regional USD retail rates from Microsoft's
[Azure Retail Prices API](https://prices.azure.com/api/retail/prices) using
these Standard-tier meters:

- `Standard Text Records` for PII detection
- `Standard Summarization Text Records` for extractive summarization

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
