const state = { samples: null, mode: "suite" };
const runButton = document.querySelector("#run-button");
const textArea = document.querySelector("#input-text");
const statusBox = document.querySelector("#status");

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setStatus(message, isError = false) {
  statusBox.textContent = message;
  statusBox.classList.toggle("error", isError);
}

function formatMs(value) {
  return `${Math.round(value).toLocaleString()} ms`;
}

function formatCost(value) {
  return `$${value.toFixed(6)}`;
}

function updateCount() {
  document.querySelector("#character-count").textContent =
    `${textArea.value.length.toLocaleString()} / 5,000 characters`;
}

function selectMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  document.querySelector("#suite-config").classList.toggle("hidden", mode !== "suite");
  document.querySelector("#custom-config").classList.toggle("hidden", mode !== "custom");
  document.querySelector("#run-label").textContent =
    mode === "suite" ? "Run all four comparisons" : "Compare custom text";
  document.querySelector("#run-note").textContent = mode === "suite"
    ? "One click runs 2 datasets × 2 pipeline strategies."
    : "Your text is sent to the local backend and evaluated by both strategies.";
  if (mode === "custom") textArea.focus();
}

function appendHighlightedText(container, text, entities) {
  const codePoints = Array.from(text);
  const ranges = [...entities]
    .filter((entity) =>
      Number.isInteger(entity.offset) &&
      Number.isInteger(entity.length) &&
      entity.offset >= 0 &&
      entity.length > 0 &&
      entity.offset + entity.length <= codePoints.length
    )
    .sort((left, right) => left.offset - right.offset || right.length - left.length);

  let cursor = 0;
  ranges.forEach((entity) => {
    if (entity.offset < cursor) return;
    if (entity.offset > cursor) {
      container.append(document.createTextNode(
        codePoints.slice(cursor, entity.offset).join("")
      ));
    }
    const mark = element(
      "mark",
      "pii-mark",
      codePoints.slice(entity.offset, entity.offset + entity.length).join("")
    );
    mark.dataset.category = entity.category;
    mark.title = `${entity.category} · ${(entity.confidence_score * 100).toFixed(1)}% confidence`;
    container.append(mark);
    cursor = entity.offset + entity.length;
  });
  if (cursor < codePoints.length) {
    container.append(document.createTextNode(codePoints.slice(cursor).join("")));
  }
}

function metric(label, value, detail) {
  const node = element("article", "aggregate-card");
  node.append(
    element("span", null, label),
    element("strong", null, value),
    element("small", null, detail)
  );
  return node;
}

function renderAggregate(payload, isSuite) {
  const container = document.querySelector("#aggregate-metrics");
  container.replaceChildren();
  if (isSuite) {
    const aggregate = payload.aggregate;
    const saved = aggregate.sequential_latency_ms - aggregate.parallel_latency_ms;
    container.append(
      metric(
        "Combined latency delta",
        saved >= 0 ? `${formatMs(saved)} saved` : `${formatMs(Math.abs(saved))} slower`,
        "Parallel total compared with sequential total"
      ),
      metric(
        "Sequential retail estimate",
        formatCost(aggregate.sequential_cost_usd),
        "Both matched datasets"
      ),
      metric(
        "Parallel retail estimate",
        formatCost(aggregate.parallel_cost_usd),
        "Both matched datasets"
      ),
      metric(
        "Measured pipeline runs",
        String(aggregate.pipeline_runs),
        `${payload.characters_per_sample.toLocaleString()} characters each`
      )
    );
    return;
  }

  const saved = payload.latency_saved_ms;
  container.append(
    metric(
      "Latency delta",
      saved >= 0 ? `${formatMs(saved)} saved` : `${formatMs(Math.abs(saved))} slower`,
      "Parallel compared with sequential"
    ),
    metric("Sequential cost", formatCost(payload.sequential.cost.total_usd), "Estimated retail"),
    metric("Parallel cost", formatCost(payload.parallel.cost.total_usd), "Estimated retail"),
    metric("Input size", payload.characters.toLocaleString(), "Unicode characters")
  );
}

function operationTimeline(operations) {
  const list = element("div", "operation-list");
  operations.forEach((operation) => {
    const row = element("div", "operation-row");
    row.append(
      element("span", null, operation.name.replaceAll("_", " ")),
      element("strong", null, formatMs(operation.duration_ms))
    );
    list.append(row);
  });
  return list;
}

function pipelineCard(mode, result) {
  const card = element("article", `pipeline-card ${mode}`);
  const title = element("div", "pipeline-title");
  title.append(
    element("span", "pipeline-icon", mode === "sequential" ? "S" : "P"),
    element("div", null)
  );
  title.lastChild.append(
    element("strong", null, mode === "sequential" ? "Sequential" : "Speculative parallel"),
    element(
      "small",
      null,
      mode === "sequential"
        ? "PII → redact → summarize"
        : "PII ∥ summarize → conditional retry"
    )
  );

  const primary = element("div", "primary-metric");
  primary.append(
    element("strong", null, formatMs(result.total_ms)),
    element("span", null, "total latency")
  );

  const stats = element("div", "pipeline-stats");
  [
    ["Retail estimate", formatCost(result.cost.total_usd)],
    ["Azure calls", String(result.operations.length)],
    ["Text records", String(result.cost.total_records)],
  ].forEach(([label, value]) => {
    const item = element("div");
    item.append(element("span", null, label), element("strong", null, value));
    stats.append(item);
  });

  const summary = element("div", "summary-block");
  summary.append(
    element("span", null, "Returned summary"),
    element("p", null, result.summary)
  );

  if (result.discarded_speculative_summary) {
    card.append(element("span", "discard-badge", "Speculative summary discarded"));
  }
  card.append(title, primary, stats, operationTimeline(result.operations), summary);
  return card;
}

function renderLegend(categories) {
  const legend = element("div", "pii-legend");
  categories.forEach((category) => {
    const item = element("span");
    item.append(element("i", null), document.createTextNode(category));
    legend.append(item);
  });
  return legend;
}

function renderDataset(key, dataset) {
  const section = element("section", `dataset-section ${dataset.has_pii ? "has-pii" : "no-pii"}`);
  const heading = element("div", "dataset-heading");
  const title = element("div");
  title.append(
    element("span", "dataset-number", key === "pii" ? "A" : key === "no_pii" ? "B" : "C"),
    element("div", null)
  );
  title.lastChild.append(
    element("p", "step", dataset.filename || "CUSTOM INPUT"),
    element("h3", null, dataset.has_pii ? "PII detected" : "No PII detected")
  );

  const saved = dataset.comparison.latency_saved_ms;
  const comparison = element(
    "span",
    `comparison-pill ${saved >= 0 ? "positive" : "negative"}`,
    saved >= 0
      ? `${dataset.comparison.speedup.toFixed(2)}× · ${formatMs(saved)} saved`
      : `${formatMs(Math.abs(saved))} sequential advantage`
  );
  heading.append(title, comparison);

  const inputPanel = element("article", "original-input");
  const inputHeading = element("div", "original-heading");
  inputHeading.append(
    element("strong", null, "Original input"),
    element(
      "span",
      dataset.has_pii ? "risk-label" : "safe-label",
      dataset.has_pii
        ? `${dataset.pii_entities.length} PII span${dataset.pii_entities.length === 1 ? "" : "s"} highlighted`
        : "No detected spans"
    )
  );
  const originalText = element("p", "annotated-text");
  appendHighlightedText(originalText, dataset.original_text, dataset.pii_entities);
  inputPanel.append(inputHeading, originalText);
  if (dataset.pii_categories.length) {
    inputPanel.append(renderLegend(dataset.pii_categories));
  }

  const grid = element("div", "pipeline-grid");
  grid.append(
    pipelineCard("sequential", dataset.pipelines.sequential),
    pipelineCard("parallel", dataset.pipelines.parallel)
  );
  section.append(heading, inputPanel, grid);
  return section;
}

function renderResults(payload, isSuite) {
  const datasets = isSuite
    ? payload.datasets
    : {
        custom: {
          filename: "Custom input",
          original_text: payload.parallel.original_text,
          has_pii: payload.parallel.has_pii,
          pii_categories: payload.parallel.pii_categories,
          pii_entities: payload.parallel.pii_entities,
          comparison: {
            speedup: payload.speedup,
            latency_saved_ms: payload.latency_saved_ms,
          },
          pipelines: {
            sequential: payload.sequential,
            parallel: payload.parallel,
          },
        },
      };

  document.querySelector("#result-context").textContent = isSuite
    ? `Matched workload · ${payload.characters_per_sample.toLocaleString()} chars each`
    : "Custom workload";
  renderAggregate(payload, isSuite);
  const results = document.querySelector("#dataset-results");
  results.replaceChildren();
  Object.entries(datasets).forEach(([key, dataset]) => {
    results.append(renderDataset(key, dataset));
  });
  document.querySelector("#results").classList.remove("hidden");
  document.querySelector("#results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function populateSamples(samples) {
  state.samples = samples;
  document.querySelector("#pii-count").textContent =
    `${samples.pii.characters.toLocaleString()} characters`;
  document.querySelector("#no-pii-count").textContent =
    `${samples.no_pii.characters.toLocaleString()} characters`;
  document.querySelector("#pii-preview").textContent =
    `${samples.pii.content.slice(0, 180)}…`;
  document.querySelector("#no-pii-preview").textContent =
    `${samples.no_pii.content.slice(0, 180)}…`;
}

async function loadInitialData() {
  try {
    const [sampleResponse, pricingResponse] = await Promise.all([
      fetch("/api/samples"),
      fetch("/api/pricing"),
    ]);
    if (!sampleResponse.ok) throw new Error("Could not load samples.");
    populateSamples((await sampleResponse.json()).samples);

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
      document.querySelector("#meter-note").textContent =
        "Official pricing is temporarily unavailable.";
    }
  } catch (error) {
    setStatus(error.message, true);
  }
}

document.querySelectorAll(".mode-button").forEach((button) => {
  button.addEventListener("click", () => selectMode(button.dataset.mode));
});
textArea.addEventListener("input", updateCount);

runButton.addEventListener("click", async () => {
  runButton.disabled = true;
  setStatus(
    state.mode === "suite"
      ? "Running four measured pipelines against Azure AI Language…"
      : "Running both pipeline strategies against Azure AI Language…"
  );
  try {
    const isSuite = state.mode === "suite";
    const response = await fetch(
      isSuite ? "/api/benchmark/samples" : "/api/benchmark",
      isSuite
        ? { method: "POST" }
        : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: textArea.value }),
          }
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Benchmark failed.");
    renderResults(payload, isSuite);
    setStatus("Benchmark complete. Original input is shown with PII annotations.");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    runButton.disabled = false;
  }
});

loadInitialData();
