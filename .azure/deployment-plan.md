# Azure Deployment Plan

> **Status:** Deployed

Generated: 2026-07-30T22:26:20Z

---

## 1. Project Overview

**Goal:** Deploy the four-way Azure AI Language PII and summarization benchmark as a public FastAPI web application on Azure Container Apps.

**Path:** Modernize Existing

The application is already implemented, tested, and containerized. This deployment adds Azure hosting while reusing existing shared Azure resources.

---

## 2. Requirements

| Attribute | Value |
|-----------|-------|
| Classification | POC / demo |
| Scale | Small |
| Budget | Cost-Optimized |
| Subscription | ME-MngEnvMCAP461858-nikhilgopal-1 (`fd918039-a89e-49a7-8e32-af614b3765f9`) |
| Location | `eastus2` |
| Resource group | Existing `finance-app-ng` |
| Compliance | PII is redacted before safe summaries are returned; no application secret is deployed |

The user explicitly selected Azure Container Apps, authorized deployment, requested autonomous execution, and previously selected the existing subscription, resource group, and East US 2 resources.

### Policy Constraints

The subscription has three Defender-related assignments for data protection, open-source relational databases, and SQL Server on machines. None restricts this Container Apps deployment, its SKUs, region, network access, naming, or tags.

---

## 3. Components Detected

| Component | Type | Technology | Path |
|-----------|------|------------|------|
| `language-pii-web` | Web + REST API | Python 3.12, FastAPI, Uvicorn, vanilla HTML/CSS/JS | repository root |
| Azure AI adapter | Service integration | Azure AI Text Analytics and `DefaultAzureCredential` | `azure_language.py` |
| Benchmark engine | Application service | Async Python sequential/speculative-parallel pipelines | `benchmark.py` |
| Static dashboard | Frontend | HTML, CSS, browser JavaScript | `static/` |

### Dependencies

| Component | Depends On | Type |
|-----------|------------|------|
| Web API | Azure AI Language `finance-app-resource` | Managed-identity data-plane API |
| Container App | Azure Container Registry `regngasdf` | Managed-identity image pull |
| Container App | Container Apps environment `phia-ca-env` | Existing compute environment |
| Application Insights | Log Analytics `workspace-financeappng0vTV` | Existing monitoring workspace |

### Existing Infrastructure

| Item | Status |
|------|--------|
| Bicep | `infra/main.bicep`, `infra/modules/resources.bicep` |
| Parameters | `infra/main.parameters.json` |
| Container build | `Dockerfile.azure` |
| Existing ACR | `regngasdf` (Standard), provisioning confirmed |
| Existing Container Apps environment | `phia-ca-env`, provisioning state `Succeeded` |
| Existing Azure AI Language | `finance-app-resource` (AIServices S0) |
| Existing resource group | `finance-app-ng` in `eastus2` |

---

## 4. Recipe Selection

**Selected:** Bicep

**Recipe type:** `bicep`

**Rationale:**
- The prepared repository already contains direct Bicep infrastructure with subscription scope.
- The deployment intentionally reuses shared resources with exact names.
- A direct two-phase Bicep deployment provides an explicit RBAC propagation gate before switching from the public placeholder to the private ACR image.
- No AZD environment or `azure.yaml` is required.

**Phase 1 command:**

```bash
az deployment sub create \
  --name language-pii-infra-27a7 \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters deployerObjectId=<signed-in-user-object-id>
```

**Phase 2 commands:**

```bash
az acr build \
  --registry regngasdf \
  --image azure-language-pii:f4e49f9 \
  --file Dockerfile.azure .

az deployment sub create \
  --name language-pii-app-27a7 \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters deployerObjectId=<signed-in-user-object-id> \
               containerImage=regngasdf.azurecr.io/azure-language-pii:f4e49f9
```

---

## 5. Architecture

**Stack:** Containers

### Service Mapping

| Component | Azure Service | SKU / configuration |
|-----------|---------------|---------------------|
| FastAPI web application | Azure Container Apps | Existing Consumption environment; 0.5 vCPU, 1 GiB, min 0, max 3 |
| Container image | Azure Container Registry | Existing Standard registry |
| PII and summarization | Azure AI Language | Existing AIServices S0 account |
| Telemetry | Application Insights | Workspace-based |
| Secret-ready configuration | Azure Key Vault | Standard, RBAC authorization |

### Supporting Services

| Service | Purpose |
|---------|---------|
| Existing Log Analytics | Centralized Container Apps and Application Insights logs |
| Application Insights | Monitoring and APM |
| Key Vault | Secret-ready deployment; no local service-principal secret is uploaded |
| System-assigned managed identity | ACR image pull and Azure AI Language authentication |

### New Resources

| Resource | Exact name |
|----------|------------|
| Container App | `ca-language-pii-dev-27a7` |
| Application Insights | `appi-language-pii-dev-27a7` |
| Key Vault | `kv-language-pii-dev-27a7` |

### Security

- HTTPS-only external ingress (`allowInsecure: false`).
- Container runs as non-root UID/GID `10001`.
- System-assigned identity receives `AcrPull` scoped to `regngasdf`.
- System-assigned identity receives `Cognitive Services User` scoped to `finance-app-resource`.
- No `.env`, client secret, registry password, or Azure credential is included in the image or infrastructure.
- `LANGUAGE_ENDPOINT` is non-secret configuration.
- Key Vault uses Azure RBAC and soft delete.
- Liveness and readiness probes call `/healthz`.

---

## 6. Provisioning Limit Checklist

| Resource Type | Number to Deploy | Current Usage | Total After Deployment | Limit / Quota | Notes |
|---------------|------------------|---------------|------------------------|---------------|-------|
| `Microsoft.App/managedEnvironments` | 0 | 3 in `eastus2` | 3 | 50 | `az quota` resource `ManagedEnvironmentCount`; existing environment reused |
| Managed Environment Consumption Cores | 0.5 maximum for one active app replica | 1.5 | 2.0 | 500 | `az containerapp env list-usages`; 498 cores remain before deployment |
| `Microsoft.App/containerApps` | 1 | 2 in `phia-ca-env` | 3 | Governed by environment quotas | Existing environment has ample consumption-core capacity |
| `Microsoft.KeyVault/vaults` | 1 | 0 in `eastus2` | 1 | No Microsoft.Quota creation quota exposed | Azure resource inventory plus official service limits; name availability is validated pre-deploy |
| `Microsoft.Insights/components` | 1 | 1 in `eastus2` | 2 | No Microsoft.Quota creation quota exposed | Azure resource inventory plus Azure Monitor service limits |

**Status:** All resources are within available limits.

Quota evidence:
- `check-quota.sh Microsoft.App eastus2`: managed environments limit 50, usage 3, available 47.
- `az containerapp env list-usages`: consumption cores limit 500, usage 1.5.

---

## 7. Execution Checklist

### Phase 1: Planning
- [x] Analyze workspace
- [x] Gather requirements from the user's explicit POC/demo request
- [x] Confirm subscription and location from the existing approved Azure context
- [x] Query subscription policy assignments
- [x] Prepare resource inventory
- [x] Invoke `azure-quotas` and validate capacity
- [x] Scan codebase
- [x] Select Bicep recipe
- [x] Plan architecture
- [x] User approved autonomous ACA deployment in the original request

### Phase 2: Execution
- [x] Research components
- [x] Generate infrastructure files
- [x] Generate application configuration
- [x] Generate `Dockerfile.azure`
- [x] Apply managed-identity and HTTPS hardening
- [x] Verify application locally with tests, Docker, and browser checks
- [x] Update plan status to `Ready for Validation`

### Phase 3: Validation
- [x] Invoke `azure-validate`
- [x] All validation checks pass
  - [x] Core validation: CLI, authentication, Bicep build, subscription validation, and what-if
  - [x] Bicep lint
  - [x] Azure Policy validation
  - [x] Application tests, Python compilation, and JavaScript syntax
  - [x] Local container image build
  - [x] Static managed-identity role verification
- [x] Run deployment what-if
- [x] Confirm resource names and managed-identity roles
- [x] Update plan status to `Validated`
- [x] Record validation proof below

### Phase 4: Deployment
- [x] Invoke `azure-deploy`
- [x] Provision placeholder revision and RBAC
- [x] Confirm `AcrPull` propagation
- [x] Build and push the application image
- [x] Deploy the application revision
- [x] Verify public endpoints and live Azure AI benchmark
- [x] Verify live role assignments
- [x] Update plan status to `Deployed`

---

## 7. Validation Proof

> The `azure-validate` skill must populate this section before setting the plan status to `Validated`.

| Check | Command Run | Result | Timestamp |
|-------|-------------|--------|-----------|
| Core Bicep validation | `validate-deployment.sh --scope sub --location eastus2 --template ./infra/main.bicep --parameters ./infra/main.parameters.json --subscription fd918039-a89e-49a7-8e32-af614b3765f9` | Pass: CLI/auth/build/validate; what-if = 6 create, 0 modify, 0 delete | 2026-07-30T22:30:29Z |
| Bicep lint | `az bicep lint --file ./infra/main.bicep` | Pass: no diagnostics | 2026-07-30T22:30:29Z |
| Application verification | `uv run --with pytest pytest -q && node --check static/app.js && uv run python -m compileall -q main.py azure_language.py benchmark.py pricing.py` | Pass: 17 tests and all syntax checks | 2026-07-30T22:30:29Z |
| Container build | `docker build -f Dockerfile.azure -t azure-language-pii:aca-validation .` | Pass | 2026-07-30T22:30:29Z |
| Policy validation | `az policy assignment list --scope /subscriptions/fd918039-a89e-49a7-8e32-af614b3765f9` | Pass: no policy conflicts for planned resources | 2026-07-30T22:30:29Z |
| Role verification | Static Bicep review plus `az role definition list` for all three role IDs | Pass: scoped AcrPull, Cognitive Services User, and Key Vault Secrets Officer IDs verified | 2026-07-30T22:30:29Z |
| Name availability | `az keyvault check-name --name kv-language-pii-dev-27a7` and resource lookups | Pass: Key Vault name available; Container App and Application Insights names unused | 2026-07-30T22:30:29Z |
| Revision-routing update | Bicep lint/build, subscription validation, and what-if with the ACR image parameter | Pass: explicit single-revision mode validated | 2026-07-30T22:34:00Z |

**Validated by:** azure-validate skill

**Validation timestamp:** 2026-07-30T22:30:29Z

### Role Assignment Verification

- **Status:** Verified
- **Identity:** system-assigned identity for `ca-language-pii-dev-27a7`
- **Roles:** `AcrPull` on `regngasdf`; `Cognitive Services User` on `finance-app-resource`
- **Deploying user:** `Key Vault Secrets Officer` on `kv-language-pii-dev-27a7`
- **Scope:** every assignment is scoped to its specific target resource
- **Issues:** none

---

## 8. Files to Generate

| File | Purpose | Status |
|------|---------|--------|
| `.azure/deployment-plan.md` | Deployment source of truth | Complete |
| `infra/main.bicep` | Subscription-scope entry point | Complete |
| `infra/modules/resources.bicep` | Container App, monitoring, Key Vault, and RBAC | Complete |
| `infra/main.parameters.json` | Exact demo environment parameters | Complete |
| `infra/bicepconfig.json` | Bicep analyzer configuration | Complete |
| `Dockerfile.azure` | ACR-compatible non-root image | Complete |

---

## 9. Next Steps

> Current: Deployed

1. Open the public dashboard and run either the matched suite or custom-input benchmark.
2. Rotate or remove the local demo service principal independently; the deployed app does not use it.

---

## 10. Deployment Verification

- **Status:** Succeeded
- **Health:** Healthy
- **Endpoint:** https://ca-language-pii-dev-27a7.yellowtree-1fae237e.eastus2.azurecontainerapps.io
- **Image:** `regngasdf.azurecr.io/azure-language-pii:f4e49f9`
- **Image digest:** `sha256:b0eef5d57ec4382a7bea5dde3fa22f77cbab0e5dd9a2ec33b173aa799f9a0b30`
- **Revision:** `ca-language-pii-dev-27a7--0000001`, healthy, 100% traffic
- **Health probe:** `GET /healthz` returned HTTP 200 with `{"status":"ok"}`
- **Live sample benchmark:** HTTP 200, four pipeline runs, both inputs 581 characters, eight PII spans on the PII fixture and zero on the no-PII fixture
- **Live pricing:** sequential `$0.006`; speculative parallel `$0.008` for both fixtures
- **Browser:** dashboard and four-way result rendering passed; all observed API requests returned 200; no console errors
- **RBAC:** `AcrPull` on `regngasdf` and `Cognitive Services User` on `finance-app-resource` verified for principal `fe146f16-95eb-41dc-8f94-1665b006f16c`
