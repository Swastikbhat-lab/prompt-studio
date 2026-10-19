const $ = (id) => document.getElementById(id);
const state = { request: "", context: "", model: "", questions: [], assumptions: [], prompt: "", answers: [], rounds: 0 };
const ROUTE_LABELS = {
  package: "PACKAGE · NO MODEL CALL",
  clarify: "CLARIFY · BOUNDED QUESTIONS",
  draft: "DRAFT · STATE ASSUMPTIONS",
};

function showError(message = "") {
  const error = $("error");
  error.textContent = message;
  error.hidden = !message;
}

function setBusy(busy, label = "Thinking locally…") {
  for (const id of ["generate", "rewrite", "refine", "draft-now", "clear", "request", "model", "context", "strategy"]) {
    $(id).disabled = busy;
  }
  if (busy) $("generate").textContent = label;
  else $("generate").innerHTML = "Build prompt <span aria-hidden=\"true\">→</span>";
}

function renderQuestions(questions) {
  const container = $("questions");
  container.replaceChildren();
  state.questions = questions;
  questions.forEach((item, index) => {
    const card = document.createElement("div");
    card.className = "question";
    const title = document.createElement("p");
    title.textContent = `${index + 1}. ${item.question}`;
    card.append(title);
    if (item.reason) {
      const reason = document.createElement("p");
      reason.className = "reason";
      reason.textContent = item.reason;
      card.append(reason);
    }
    const input = document.createElement("input");
    input.type = "text";
    input.maxLength = 2000;
    input.placeholder = "Your answer (optional if you continue with assumptions)";
    input.setAttribute("aria-label", `Answer: ${item.question}`);
    card.append(input);
    container.append(card);
  });
  $("refine-actions").hidden = questions.length === 0;
  if (questions.length) {
    $("refine").textContent = state.rounds >= 2 ? "Finish prompt with these answers" : "Regenerate with answers";
    $("refine").innerHTML += " <span aria-hidden=\"true\">→</span>";
  }
}

function renderAssumptions(items) {
  const box = $("assumptions");
  box.replaceChildren();
  box.hidden = items.length === 0;
  if (!items.length) return;
  const heading = document.createElement("strong");
  heading.textContent = "Visible assumptions — review and edit the prompt below";
  const list = document.createElement("ul");
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    list.append(li);
  });
  box.append(heading, list);
}

function renderDecision(decision, engine) {
  const box = $("decision");
  if (!decision) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const forced = decision.route === "package" && engine !== "deterministic";
  $("decision-route").textContent = forced
    ? "REWRITE · GATE OVERRIDDEN"
    : (ROUTE_LABELS[decision.route] || decision.route.toUpperCase());
  $("decision-route").dataset.route = forced ? "draft" : decision.route;
  $("decision-latency").textContent = `${decision.latency_ms.toFixed(2)} ms · ${decision.backend} backend`;
  const metrics = [
    ["spec", decision.specificity],
    ["amb", decision.ambiguity],
    ["bloat", decision.context_bloat],
  ];
  for (const [key, value] of metrics) {
    $(`m-${key}`).textContent = value.toFixed(2);
    $(`b-${key}`).style.width = `${Math.min(100, Math.round(value * 100))}%`;
  }
  const constraints = $("decision-constraints");
  constraints.replaceChildren();
  for (const item of decision.constraints || []) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = item.value ? `${item.field}: ${item.value}` : item.field;
    chip.title = `${item.field} · presence ${item.probability}`;
    constraints.append(chip);
  }
  const flags = $("decision-flags");
  flags.replaceChildren();
  for (const flag of decision.context_flags || []) {
    const chip = document.createElement("span");
    chip.className = "chip warn";
    chip.textContent = flag;
    flags.append(chip);
  }
  const lines = [...(decision.rationale || [])];
  if (forced) lines.push("local writer forced by the user; the gate would have packaged this request");
  $("decision-rationale").textContent = lines.join(" · ");
}

function renderDraft(data) {
  if (data.questions?.length) state.rounds += 1;
  state.prompt = data.prompt;
  state.assumptions = data.assumptions || [];
  $("summary").textContent = data.summary || "Prompt prepared from your request.";
  $("summary").hidden = !data.summary;
  renderQuestions(data.questions || []);
  renderAssumptions(state.assumptions);
  renderDecision(data.decision, data.engine);
  $("prompt").value = state.prompt;
  const gateMs = data.decision ? data.decision.latency_ms.toFixed(2) : null;
  const engine = data.engine === "deterministic"
    ? `deterministic · no model call${gateMs ? ` · gate ${gateMs} ms` : ""}`
    : `${data.model} · ${data.elapsed_seconds}s`;
  $("run-meta").textContent = engine;
  $("prompt-section").hidden = false;
  $("review").hidden = false;
  $("copy-status").textContent = "";
  $("review").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function generate({ useAnswers = false, draftNow = false, forceRewrite = false } = {}) {
  showError();
  const request = $("request").value.trim();
  const context = $("context").value;
  if (!request) {
    showError("Add a request first.");
    $("request").focus();
    return;
  }
  if ((useAnswers || draftNow) && (request !== state.request || context !== state.context)) {
    showError("Your request changed. Build a fresh prompt before reusing these clarifications.");
    return;
  }
  const model = $("model").value;
  if (!model) {
    showError("No local model is available. Start Ollama and install a model first.");
    return;
  }
  let answers = [];
  if (useAnswers || draftNow) {
    const current = [...$("questions").querySelectorAll("input")]
      .map((input, index) => ({ question: state.questions[index].question, answer: input.value.trim() }))
      .filter((item) => item.answer);
    answers = [...state.answers, ...current];
  }
  if (!useAnswers && !draftNow) {
    state.rounds = 0;
    state.answers = [];
  }
  state.request = request;
  state.context = context;
  state.model = model;
  setBusy(true, forceRewrite ? "Writing locally…" : "Gate → local…");
  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request,
        context,
        model,
        answers,
        draft_now: draftNow,
        rounds: state.rounds,
        strategy: $("strategy").value,
        force_rewrite: forceRewrite,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Local generation failed.");
    if (data.network_used !== false) throw new Error("Unexpected provider state; result not shown.");
    state.answers = answers;
    renderDraft(data);
    if (draftNow) renderQuestions([]);
  } catch (error) {
    showError(error.message || "Could not reach the local prompt service.");
  } finally {
    setBusy(false);
  }
}

async function loadModels() {
  const select = $("model");
  try {
    const response = await fetch("/api/models");
    const data = await response.json();
    select.replaceChildren();
    for (const item of data.models || []) {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = item.name;
      select.append(option);
    }
    if (select.options.length) {
      state.model = select.value;
      $("model-status").textContent = `${select.options.length} model${select.options.length === 1 ? "" : "s"} ready on this computer`;
    } else {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No installed model";
      select.append(option);
      $("model-status").textContent = "Ollama is running; install a local model to continue.";
    }
  } catch {
    select.replaceChildren();
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Ollama unavailable";
    select.append(option);
    $("model-status").textContent = "Start local Ollama to discover models.";
  }
}

async function copyPrompt() {
  const text = $("prompt").value;
  if (!text.trim()) return;
  try {
    await navigator.clipboard.writeText(text);
    $("copy-status").textContent = "Copied";
  } catch {
    $("prompt").focus();
    $("prompt").select();
    const copied = document.execCommand("copy");
    $("copy-status").textContent = copied ? "Copied" : "Select and copy the prompt";
  }
}

function downloadPrompt() {
  const text = $("prompt").value;
  if (!text.trim()) return;
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "prompt.txt";
  link.click();
  URL.revokeObjectURL(url);
  $("copy-status").textContent = "Downloaded";
}

$("request").addEventListener("input", () => {
  $("char-count").textContent = `${$("request").value.length.toLocaleString()} / 20,000`;
});
$("generate").addEventListener("click", () => generate());
$("rewrite").addEventListener("click", () => generate({ forceRewrite: true }));
$("refine").addEventListener("click", () => generate({ useAnswers: true }));
$("draft-now").addEventListener("click", () => generate({ draftNow: true }));
$("copy").addEventListener("click", copyPrompt);
$("download").addEventListener("click", downloadPrompt);
$("clear").addEventListener("click", () => {
  $("request").value = "";
  $("context").value = "";
  $("char-count").textContent = "0 / 20,000";
  state.context = "";
  $("review").hidden = true;
  $("questions").replaceChildren();
  $("assumptions").replaceChildren();
  $("decision").hidden = true;
  $("summary").textContent = "";
  $("prompt").value = "";
  $("copy-status").textContent = "";
  state.request = "";
  state.questions = [];
  state.assumptions = [];
  state.prompt = "";
  state.answers = [];
  state.rounds = 0;
  showError();
  $("request").focus();
});
$("model").addEventListener("change", () => { state.model = $("model").value; });
loadModels();
