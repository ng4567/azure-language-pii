const state = {
  samples: null,
  source: "pii",
  turns: [],
  config: {
    max_text_length: 5000,
    max_turn_length: 1000,
    roles: ["Agent", "Customer"],
    default_iterations: 3,
    max_iterations: 10,
  },
  projection: null,
};

const turnEditor = document.querySelector("#turn-editor");
const addTurnButton = document.querySelector("#add-turn");
const runButton = document.querySelector("#run-button");
const statusBox = document.querySelector("#status");
const iterationsInput = document.querySelector("#iterations");
const prevalenceInput = document.querySelector("#prevalence");

function setStatus(message, isError = false) {
  statusBox.textContent = message;
  statusBox.classList.toggle("error", isError);
}

// String.length counts UTF-16 code units, which is exactly how Azure bills.
function totalCharacters() {
  return state.turns.reduce((sum, turn) => sum + turn.text.length, 0);
}

function updateCount() {
  const limit = state.config.max_text_length;
  const total = totalCharacters();
  const counter = document.querySelector("#character-count");
  counter.textContent =
    `${total.toLocaleString()} / ${limit.toLocaleString()} characters`;
  counter.classList.toggle("over-limit", total > limit);
}

function updateTurnCounter(index) {
  const row = turnEditor.children[index];
  if (!row) return;
  const counter = row.querySelector(".turn-count");
  const length = state.turns[index].text.length;
  counter.textContent = `${length.toLocaleString()} / ${state.config.max_turn_length.toLocaleString()}`;
  counter.classList.toggle("over-limit", length > state.config.max_turn_length);
}

function markActiveTab(source) {
  document.querySelectorAll(".source-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.source === source);
  });
}

function becomeCustom() {
  if (state.source !== "custom") {
    state.source = "custom";
    markActiveTab("custom");
  }
}

function renderTurnEditor() {
  turnEditor.replaceChildren();
  state.turns.forEach((turn, index) => {
    const row = document.createElement("div");
    row.className = "turn-row";

    const role = document.createElement("select");
    role.className = "turn-role";
    role.setAttribute("aria-label", `Turn ${index + 1} speaker`);
    state.config.roles.forEach((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      option.selected = name === turn.role;
      role.append(option);
    });
    role.addEventListener("change", () => {
      state.turns[index].role = role.value;
      becomeCustom();
    });

    const text = document.createElement("input");
    text.className = "turn-text";
    text.type = "text";
    text.value = turn.text;
    text.placeholder = "What was said…";
    text.setAttribute("aria-label", `Turn ${index + 1} text`);
    text.addEventListener("input", () => {
      state.turns[index].text = text.value;
      becomeCustom();
      updateTurnCounter(index);
      updateCount();
    });

    const counter = document.createElement("span");
    counter.className = "turn-count";

    const remove = document.createElement("button");
    remove.className = "turn-remove";
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove turn ${index + 1}`);
    remove.disabled = state.turns.length === 1;
    remove.addEventListener("click", () => {
      state.turns.splice(index, 1);
      becomeCustom();
      renderTurnEditor();
      updateCount();
    });

    row.append(role, text, counter, remove);
    turnEditor.append(row);
    updateTurnCounter(index);
  });
}

function loadTurns(turns, source) {
  state.turns = turns.map((turn) => ({ role: turn.role, text: turn.text }));
  state.source = source;
  markActiveTab(source);
  renderTurnEditor();
  updateCount();
}

function selectSource(source) {
  if (source !== "custom" && state.samples) {
    const sample = state.samples[source];
    loadTurns(sample.conversation, source);
    document.querySelector("#sample-note").textContent =
      `${sample.filename} · ${sample.conversation.length} turns · ${sample.characters} characters · matched transcript length`;
  } else {
    // Never clear what the user typed; switching to Custom keeps the turns.
    state.source = "custom";
    markActiveTab("custom");
    document.querySelector("#sample-note").textContent =
      "Your transcript remains in this browser and is sent directly to the local backend.";
    updateCount();
  }
}

function formatMs(value) {
  return `${Math.round(value).toLocaleString()} ms`;
}

function formatCost(value) {
  return `$${value.toFixed(6)}`;
}

function formatPercent(value) {
  return `${Math.round(value * 100)}%`;
}

function errorMessage(payload, fallback) {
  const detail = payload && payload.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors arrive as a list of objects.
    return detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  }
  return fallback;
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

function curvePointFor(rate) {
  const curve = state.projection.curve;
  let closest = curve[0];
  curve.forEach((point) => {
    if (Math.abs(point.pii_rate - rate) < Math.abs(closest.pii_rate - rate)) {
      closest = point;
    }
  });
  return closest;
}

function renderProjectionAt(rate) {
  if (!state.projection) return;
  const point = curvePointFor(rate);
  document.querySelector("#prevalence-value").textContent = formatPercent(point.pii_rate);
  document.querySelector("#projected-sequential-ms").textContent = formatMs(point.sequential_ms);
  document.querySelector("#projected-parallel-ms").textContent = formatMs(point.parallel_ms);
  document.querySelector("#projected-sequential-usd").textContent = formatCost(point.sequential_usd);
  document.querySelector("#projected-parallel-usd").textContent = formatCost(point.parallel_usd);

  const saved = document.querySelector("#projected-saved-ms");
  saved.textContent = point.latency_saved_ms >= 0
    ? formatMs(point.latency_saved_ms)
    : `−${formatMs(Math.abs(point.latency_saved_ms))}`;
  saved.classList.toggle("negative", point.latency_saved_ms < 0);

  document.querySelector("#projected-rate").textContent =
    point.usd_per_second_saved === null
      ? "parallel is slower here"
      : `$${point.usd_per_second_saved.toFixed(4)} / s`;
}

function renderProjectionChart() {
  const chart = document.querySelector("#projection-chart");
  chart.replaceChildren();
  const curve = state.projection.curve;
  const maxMs = Math.max(...curve.map((p) => Math.max(p.sequential_ms, p.parallel_ms)), 1);

  curve.forEach((point) => {
    const column = document.createElement("div");
    column.className = "chart-column";
    column.title =
      `${formatPercent(point.pii_rate)} PII · sequential ${formatMs(point.sequential_ms)} · parallel ${formatMs(point.parallel_ms)}`;

    const stack = document.createElement("div");
    stack.className = "chart-stack";
    [["sequential", point.sequential_ms], ["parallel", point.parallel_ms]].forEach(([kind, ms]) => {
      const bar = document.createElement("div");
      bar.className = `chart-bar ${kind}`;
      bar.style.height = `${(ms / maxMs) * 100}%`;
      stack.append(bar);
    });

    column.append(stack);
    if (point.pii_rate * 100 % 25 === 0) {
      const label = document.createElement("small");
      label.textContent = formatPercent(point.pii_rate);
      column.append(label);
    }
    chart.append(column);
  });
}

function renderResults(payload) {
  const sequential = payload.sequential;
  const parallel = payload.parallel;
  document.querySelector("#results").classList.remove("hidden");
  document.querySelector("#sequential-latency").textContent = formatMs(sequential.total_ms);
  document.querySelector("#parallel-latency").textContent = formatMs(parallel.total_ms);
  document.querySelector("#sequential-spread").textContent =
    `${Math.round(sequential.min_ms)}–${Math.round(sequential.p95_ms)} ms`;
  document.querySelector("#parallel-spread").textContent =
    `${Math.round(parallel.min_ms)}–${Math.round(parallel.p95_ms)} ms`;
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
  const widthFor = (value) => (maxLatency > 0 ? (value / maxLatency) * 100 : 0);
  document.querySelector("#sequential-bar").style.width = `${widthFor(sequential.total_ms)}%`;
  document.querySelector("#parallel-bar").style.width = `${widthFor(parallel.total_ms)}%`;

  const saved = payload.latency_saved_ms;
  const badge = document.querySelector("#speedup-badge");
  badge.textContent = saved >= 0
    ? `${payload.speedup.toFixed(2)}× parallel speedup · ${formatMs(saved)} saved · median of ${payload.iterations}`
    : `${formatMs(Math.abs(saved))} sequential advantage · median of ${payload.iterations}`;

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

  state.projection = payload.projection;
  const breakEven = state.projection.break_even_pii_rate;
  document.querySelector("#break-even-badge").textContent =
    breakEven === null
      ? "break-even unavailable"
      : `break-even at ${formatPercent(breakEven)} PII prevalence`;
  renderProjectionChart();
  renderProjectionAt(Number(prevalenceInput.value) / 100);

  document.querySelector("#results").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function loadInitialData() {
  // allSettled so a pricing outage cannot stop the samples from rendering.
  const [configResult, sampleResult, pricingResult] = await Promise.allSettled([
    fetch("/api/config"),
    fetch("/api/samples"),
    fetch("/api/pricing"),
  ]);

  if (configResult.status === "fulfilled" && configResult.value.ok) {
    state.config = await configResult.value.json();
    iterationsInput.max = state.config.max_iterations;
    iterationsInput.value = state.config.default_iterations;
  }

  if (sampleResult.status === "fulfilled" && sampleResult.value.ok) {
    state.samples = (await sampleResult.value.json()).samples;
    selectSource("pii");
  } else {
    setStatus("Could not load samples.", true);
    loadTurns([{ role: state.config.roles[0], text: "" }], "custom");
  }

  const meterNote = document.querySelector("#meter-note");
  if (pricingResult.status === "fulfilled" && pricingResult.value.ok) {
    const pricing = await pricingResult.value.json();
    document.querySelector("#pricing-link").href = pricing.pricing_page_url;
    document.querySelector("#limits-link").href = pricing.data_limits_url;
    const meters = pricing.meters
      .map((meter) => `${meter.meter_name}: $${meter.retail_price_per_1000}/1K`)
      .join(" · ");
    meterNote.textContent =
      `Microsoft Retail Prices API · ${pricing.region} · ${pricing.currency} · ${meters}`;
  } else {
    meterNote.textContent = "Official pricing is temporarily unavailable.";
  }
}

document.querySelectorAll(".source-tab").forEach((button) => {
  button.addEventListener("click", () => selectSource(button.dataset.source));
});

addTurnButton.addEventListener("click", () => {
  const last = state.turns[state.turns.length - 1];
  const next =
    last && last.role === state.config.roles[0]
      ? state.config.roles[1]
      : state.config.roles[0];
  state.turns.push({ role: next, text: "" });
  becomeCustom();
  renderTurnEditor();
  updateCount();
  const rows = turnEditor.querySelectorAll(".turn-text");
  rows[rows.length - 1].focus();
});

prevalenceInput.addEventListener("input", () => {
  document.querySelector("#prevalence-value").textContent =
    `${prevalenceInput.value}%`;
  renderProjectionAt(Number(prevalenceInput.value) / 100);
});

runButton.addEventListener("click", async () => {
  runButton.disabled = true;
  const iterations = Number(iterationsInput.value) || state.config.default_iterations;
  setStatus(
    `Warming up, then running both pipelines ${iterations}× against Azure AI Language…`,
  );
  try {
    const response = await fetch("/api/benchmark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation: state.turns, iterations }),
    });
    const payload = await readJson(response);
    if (!response.ok) throw new Error(errorMessage(payload, "Benchmark failed."));
    renderResults(payload);
    setStatus(`Benchmark complete · median of ${payload.iterations} iterations.`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    runButton.disabled = false;
  }
});

loadInitialData();
