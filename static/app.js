const state = { samples: null, source: "pii" };
const textArea = document.querySelector("#input-text");
const runButton = document.querySelector("#run-button");
const statusBox = document.querySelector("#status");

function setStatus(message, isError = false) {
  statusBox.textContent = message;
  statusBox.classList.toggle("error", isError);
}

function updateCount() {
  document.querySelector("#character-count").textContent =
    `${textArea.value.length.toLocaleString()} / 5,000 characters`;
}

function selectSource(source) {
  state.source = source;
  document.querySelectorAll(".source-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.source === source);
  });
  if (source !== "custom" && state.samples) {
    textArea.value = state.samples[source].content;
    document.querySelector("#sample-note").textContent =
      `${state.samples[source].filename} · ${state.samples[source].characters} characters · matched sample length`;
  } else {
    textArea.value = "";
    document.querySelector("#sample-note").textContent = "Your text remains in this browser and is sent directly to the local backend.";
    textArea.focus();
  }
  updateCount();
}

function formatMs(value) {
  return `${Math.round(value).toLocaleString()} ms`;
}

function formatCost(value) {
  return `$${value.toFixed(6)}`;
}

function renderTimings(sequential, parallel) {
  const container = document.querySelector("#timing-table");
  container.replaceChildren();
  [["Sequential", sequential], ["Parallel", parallel]].forEach(([label, result]) => {
    result.operations.forEach((operation) => {
      const row = document.createElement("div");
      row.className = "timing-row";
      const name = document.createElement("strong");
      name.textContent = `${label} · ${operation.name.replaceAll("_", " ")}`;
      const duration = document.createElement("span");
      duration.textContent = formatMs(operation.duration_ms);
      row.append(name, duration);
      container.append(row);
    });
  });
}

function renderResults(payload) {
  const sequential = payload.sequential;
  const parallel = payload.parallel;
  document.querySelector("#results").classList.remove("hidden");
  document.querySelector("#sequential-latency").textContent = formatMs(sequential.total_ms);
  document.querySelector("#parallel-latency").textContent = formatMs(parallel.total_ms);
  document.querySelector("#sequential-cost").textContent = formatCost(sequential.cost.total_usd);
  document.querySelector("#parallel-cost").textContent = formatCost(parallel.cost.total_usd);
  document.querySelector("#sequential-calls").textContent = sequential.operations.length;
  document.querySelector("#parallel-calls").textContent = parallel.operations.length;
  document.querySelector("#sequential-records").textContent = sequential.cost.total_records;
  document.querySelector("#parallel-records").textContent = parallel.cost.total_records;
  document.querySelector("#sequential-summary").textContent = sequential.summary;
  document.querySelector("#parallel-summary").textContent = parallel.summary;
  document.querySelector("#redacted-text").textContent = parallel.redacted_text;

  const maxLatency = Math.max(sequential.total_ms, parallel.total_ms);
  document.querySelector("#sequential-bar").style.width = `${sequential.total_ms / maxLatency * 100}%`;
  document.querySelector("#parallel-bar").style.width = `${parallel.total_ms / maxLatency * 100}%`;

  const saved = payload.latency_saved_ms;
  const badge = document.querySelector("#speedup-badge");
  badge.textContent = saved >= 0
    ? `${payload.speedup.toFixed(2)}× parallel speedup · ${formatMs(saved)} saved`
    : `${formatMs(Math.abs(saved))} sequential advantage`;

  const outcome = document.querySelector("#privacy-outcome");
  outcome.replaceChildren();
  const pill = document.createElement("span");
  pill.className = `outcome-pill${parallel.has_pii ? "" : " safe"}`;
  pill.textContent = parallel.has_pii ? "PII detected" : "No PII detected";
  const detail = document.createElement("p");
  detail.textContent = parallel.has_pii
    ? `${parallel.pii_categories.join(", ")} · speculative summary discarded · redacted summary used`
    : "The speculative summary was safe to use; no second summary call was needed.";
  outcome.append(pill, detail);
  renderTimings(sequential, parallel);
  document.querySelector("#results").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadInitialData() {
  try {
    const [sampleResponse, pricingResponse] = await Promise.all([
      fetch("/api/samples"),
      fetch("/api/pricing"),
    ]);
    if (!sampleResponse.ok) throw new Error("Could not load samples.");
    state.samples = (await sampleResponse.json()).samples;
    selectSource("pii");

    if (pricingResponse.ok) {
      const pricing = await pricingResponse.json();
      document.querySelector("#pricing-link").href = pricing.pricing_page_url;
      document.querySelector("#limits-link").href = pricing.data_limits_url;
      const meters = pricing.meters
        .map((meter) => `${meter.meter_name}: $${meter.retail_price_per_1000}/1K`)
        .join(" · ");
      document.querySelector("#meter-note").textContent =
        `Microsoft Retail Prices API · ${pricing.region} · ${pricing.currency} · ${meters}`;
    } else {
      document.querySelector("#meter-note").textContent = "Official pricing is temporarily unavailable.";
    }
  } catch (error) {
    setStatus(error.message, true);
  }
}

document.querySelectorAll(".source-tab").forEach((button) => {
  button.addEventListener("click", () => selectSource(button.dataset.source));
});
textArea.addEventListener("input", () => {
  updateCount();
  if (state.source !== "custom") {
    state.source = "custom";
    document.querySelectorAll(".source-tab").forEach((button) => {
      button.classList.toggle("active", button.dataset.source === "custom");
    });
  }
});

runButton.addEventListener("click", async () => {
  runButton.disabled = true;
  setStatus("Running both pipelines against Azure AI Language…");
  try {
    const response = await fetch("/api/benchmark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: textArea.value }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Benchmark failed.");
    renderResults(payload);
    setStatus("Benchmark complete.");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    runButton.disabled = false;
  }
});

loadInitialData();
