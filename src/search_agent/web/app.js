const STAGES = [
  ["understand", "理解岗位"],
  ["plan", "规划路径"],
  ["execute", "执行检索"],
  ["judge", "能力判定"],
  ["evaluate", "评估收敛"],
  ["reflect", "反思缺口"],
  ["revise", "修订策略"],
  ["finalize", "交付"],
];

const STAGE_LABEL = Object.fromEntries(STAGES);
const STAGE_ORDER = new Map(STAGES.map(([key], i) => [key, i]));

const state = {
  running: false,
  controller: null,
  startedAt: 0,
  executeCount: 0,
  stageCount: 0,
};

const $ = (id) => document.getElementById(id);
const queryInput = $("query");
const topKInput = $("top-k");
const runBtn = $("run");
const stopBtn = $("stop");
const runHint = $("run-hint");
const timeline = $("timeline");
const timelineEmpty = $("timeline-empty");
const results = $("results");
const resultsEmpty = $("results-empty");
const resultsHint = $("results-hint");
const metrics = $("metrics");
const traceWrap = $("trace-wrap");
const traceJson = $("trace-json");
const timelineHint = $("timeline-hint");

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function chip(text, extra = "") {
  return el("span", `tag ${extra}`.trim(), text);
}

function kv(label, value) {
  const wrap = el("span", "kv");
  wrap.append(el("span", "", `${label}: `), el("b", "", value));
  return wrap;
}

function buildRail() {
  const rail = $("stage-rail");
  rail.replaceChildren();
  for (const [key, label] of STAGES) {
    const item = el("span", "rail-item");
    item.append(el("span", "", label), el("b", "", key));
    item.dataset.stage = key;
    rail.append(item);
  }
}

function markRail(stage) {
  const index = STAGE_ORDER.get(stage);
  if (index === undefined) return;
  for (const item of document.querySelectorAll(".rail-item")) {
    item.classList.toggle("active", item.dataset.stage === stage);
    if (STAGE_ORDER.get(item.dataset.stage) < index) item.classList.add("hit");
  }
}

function resetUI() {
  timeline.replaceChildren();
  results.replaceChildren();
  timelineEmpty.hidden = true;
  resultsEmpty.hidden = true;
  metrics.hidden = true;
  traceWrap.hidden = true;
  traceJson.textContent = "";
  resultsHint.textContent = "—";
  state.executeCount = 0;
  state.stageCount = 0;
  for (const item of document.querySelectorAll(".rail-item")) {
    item.classList.remove("hit", "active");
  }
}

function setRunning(active) {
  state.running = active;
  runBtn.disabled = active;
  stopBtn.hidden = !active;
  runHint.classList.remove("error");
  if (active) {
    runHint.textContent = "Agent 正在运行：阶段事件会实时出现在右侧时间线。";
  }
}

function addRoundDivider(round) {
  const divider = el("li", "round-divider", `ROUND ${round}`);
  timeline.append(divider);
}

function addWaiting() {
  const waiting = el("li", "waiting", "等待下一个阶段事件…");
  waiting.id = "waiting-row";
  timeline.append(waiting);
}

function removeWaiting() {
  document.getElementById("waiting-row")?.remove();
}

function stageDetail(stage, detail = {}) {
  const parts = [];
  const list = (values) => (values || []).join(" · ");

  if (stage === "understand") {
    if (detail.jobTitle) parts.push(kv("岗位", detail.jobTitle));
    if (detail.mustAbilities?.length) parts.push(kv("必备能力", list(detail.mustAbilities)));
    if (detail.hardConstraints !== undefined) parts.push(kv("硬条件", detail.hardConstraints));
  } else if (stage === "plan") {
    if (detail.paths !== undefined) parts.push(kv("路径", detail.paths));
    for (const rationale of detail.rationales || []) {
      const line = el("span", "line", `↳ ${rationale}`);
      parts.push(line);
    }
  } else if (stage === "execute") {
    if (detail.paths !== undefined) parts.push(kv("路径", detail.paths));
    if (detail.recalled !== undefined) parts.push(kv("召回人次", detail.recalled));
    if (detail.qualities?.length) parts.push(kv("质量", list(detail.qualities)));
  } else if (stage === "judge") {
    if (detail.qualified !== undefined) parts.push(kv("累计判定", detail.qualified));
  } else if (stage === "evaluate") {
    if (detail.next) parts.push(kv("下一阶段", detail.next));
    if (detail.stopReason) parts.push(kv("终止原因", detail.stopReason));
  } else if (stage === "reflect") {
    if (detail.commonTraits?.length) parts.push(kv("共性", list(detail.commonTraits)));
    if (detail.gaps?.length) parts.push(kv("缺口", list(detail.gaps)));
    if (detail.newSoftSignals !== undefined) parts.push(kv("新软信号", detail.newSoftSignals));
  } else if (stage === "revise") {
    if (detail.softSignals !== undefined) parts.push(kv("软信号", detail.softSignals));
    if (detail.converged !== undefined) parts.push(kv("画像收敛", detail.converged ? "是" : "否"));
    if (detail.iteration !== undefined) parts.push(kv("轮次", detail.iteration));
  } else if (stage === "finalize") {
    if (detail.total !== undefined) parts.push(kv("交付", detail.total));
    if (detail.searchTotal !== undefined) parts.push(kv("收敛检索", detail.searchTotal));
  } else if (stage.startsWith("degrade")) {
    if (detail.reason) {
      const line = el("span", "reason", `降级原因: ${detail.reason}`);
      parts.push(line);
    }
  }
  return parts;
}

function addEvent(stage, detail) {
  removeWaiting();
  state.stageCount += 1;

  if (stage === "execute") {
    if (state.executeCount >= 1) addRoundDivider(state.executeCount + 1);
    state.executeCount += 1;
  }

  const li = el("li", "event");
  if (stage.startsWith("degrade")) li.classList.add("degrade");
  if (stage === "finalize") li.classList.add("final");

  const dot = el("span", "event-dot");
  const body = el("div", "event-body");
  const head = el("div", "event-head");
  head.append(
    el("span", "stage-name", STAGE_LABEL[stage] || stage),
    el("code", "", stage),
    el("span", "event-time", `+${Math.round(performance.now() - state.startedAt)} ms`),
  );

  const detailWrap = el("div", "event-detail");
  const parts = stageDetail(stage, detail);
  if (parts.length) detailWrap.append(...parts);

  const raw = el("details", "raw");
  raw.append(el("summary", "", "raw event"), el("pre", "", JSON.stringify(detail, null, 2)));
  detailWrap.append(raw);

  body.append(head, detailWrap);
  li.append(dot, body);
  timeline.append(li);
  timeline.scrollTop = timeline.scrollHeight;

  markRail(stage);
  timelineHint.textContent = `${state.stageCount} 个阶段事件 · 第 ${Math.max(1, state.executeCount)} 轮检索`;
}

function renderSkills(item) {
  const names = (item.skills || []).map((s) => s?.name).filter(Boolean).slice(0, 6);
  return names.map((name) => chip(name, "skill"));
}

function renderCandidate(item, rank) {
  const li = el("li", "candidate");
  const top = el("div", "cand-top");
  const meta = [item.docId, item.workYears != null ? `${item.workYears} 年` : null, item.currentResidence]
    .filter(Boolean)
    .join(" · ");
  const who = el("div", "cand-id");
  who.append(el("h3", "", item.name || item.docId), el("p", "", meta));
  top.append(el("span", "rank", rank), who, el("span", "score", Number(item.score || 0).toFixed(2)));

  const tags = el("div", "tag-row");
  const explanation = item.matchExplanation || {};
  for (const ability of explanation.matchedAbilities || []) tags.append(chip(ability));
  for (const skill of renderSkills(item)) tags.append(skill);

  const details = el("details");
  const summary = el("summary", "", "匹配依据 / 来源路径");
  const explain = el("div", "explain");

  if (item.summary) explain.append(el("p", "", item.summary));
  if (explanation.pathRationale?.length) {
    const ul = el("ul");
    for (const rationale of explanation.pathRationale) {
      if (rationale) ul.append(el("li", "", rationale));
    }
    if (ul.childElementCount) explain.append(el("p", "", "来源路径"), ul);
  }
  if (explanation.hardMatches?.length) {
    const ul = el("ul");
    for (const hard of explanation.hardMatches) {
      const values = Array.isArray(hard.values) ? hard.values.join("/") : hard.values;
      ul.append(el("li", "", `${hard.field || "?"}: ${values ?? "—"} ${hard.matched ? "✓" : "✗"}`));
    }
    explain.append(el("p", "", "硬条件"), ul);
  }
  const raw = el("details");
  raw.append(el("summary", "", "raw matchExplanation"), el("pre", "", JSON.stringify(explanation, null, 2)));
  explain.append(raw);

  details.append(summary, explain);
  li.append(top, tags, details);
  return li;
}

function renderResults(payload) {
  results.replaceChildren();
  const items = payload.items || [];
  const clarification = payload.clarification;

  if (clarification?.needClarification) {
    resultsEmpty.hidden = true;
    resultsHint.textContent = "需要澄清";
    const card = el("li", "err-card");
    card.append(el("p", "warn-line", "岗位描述需要澄清："));
    const ul = el("ul");
    for (const question of clarification.questions || []) ul.append(el("li", "", question));
    card.append(ul);
    results.append(card);
    return;
  }

  resultsEmpty.hidden = items.length > 0;
  const total = payload.total ?? items.length;
  resultsHint.textContent = `${items.length} 人交付${total !== items.length ? ` · total ${total}` : ""}`;
  items.forEach((item, index) => results.append(renderCandidate(item, index + 1)));
  $("results").scrollTop = 0;
}

function setMetric(id, value) {
  $(id).textContent = value ?? "—";
}

function renderMetrics(payload) {
  const m = payload.debug?.agentMetrics;
  metrics.hidden = false;
  if (!m) {
    setMetric("m-calls", "—");
    setMetric("m-tokens", "—");
    setMetric("m-iterations", Math.max(1, state.executeCount));
    setMetric("m-elapsed", `${Math.round(performance.now() - state.startedAt)} ms`);
    setMetric("m-judged", payload.total ?? "—");
    setMetric("m-degraded", "—");
    return;
  }
  const meta = m.meta || {};
  const rounds = m.rounds || {};
  const recall = m.recall || {};
  setMetric("m-calls", meta.llmCalls);
  setMetric(
    "m-tokens",
    `${meta.tokensEstimated ? "≈" : ""}${Number(meta.totalTokens || 0).toLocaleString()}`,
  );
  setMetric("m-iterations", rounds.iterations);
  setMetric("m-elapsed", `${meta.elapsedMs ?? "—"} ms`);
  setMetric("m-judged", `${recall.judged ?? "—"} / ${recall.target ?? "—"}`);
  setMetric("m-degraded", meta.degradedReason || "无");
}

function renderTrace(payload) {
  const debug = payload.debug;
  if (!debug) return;
  traceJson.textContent = JSON.stringify(
    { agentTrace: debug.agentTrace, agentMetrics: debug.agentMetrics, degradedReason: debug.degradedReason },
    null,
    2,
  );
  traceWrap.hidden = false;
}

function parseFrame(frame) {
  let event = "message";
  const data = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!data.length) return null;
  try {
    return { event, payload: JSON.parse(data.join("\n")) };
  } catch (err) {
    return null;
  }
}

function showError(message) {
  runHint.classList.add("error");
  runHint.textContent = message;
  const card = el("li", "err-card", message);
  results.append(card);
  resultsEmpty.hidden = true;
}

async function runSearch(query, topK) {
  if (state.running) return;
  resetUI();
  setRunning(true);
  state.startedAt = performance.now();
  timelineHint.textContent = "连接中…";
  addWaiting();

  const controller = new AbortController();
  state.controller = controller;
  let sawResult = false;
  let sawError = false;

  try {
    const resp = await fetch("/search/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, topK, details: true }),
      signal: controller.signal,
    });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        const parsed = parseFrame(frame);
        if (!parsed) continue;
        const { event, payload } = parsed;
        if (event === "stage") {
          addEvent(payload.stage, payload.detail);
        } else if (event === "result") {
          sawResult = true;
          removeWaiting();
          renderResults(payload);
          renderMetrics(payload);
          renderTrace(payload);
          timelineHint.textContent = `完成 · ${state.stageCount} 个阶段事件 · ${Math.max(1, state.executeCount)} 轮`;
        } else if (event === "error") {
          sawError = true;
          showError(`Agent 执行失败：${payload.message || "unknown error"}`);
        }
      }
    }
    if (!sawResult && !sawError && state.running) {
      showError("连接已结束，但没有收到最终结果。");
    }
    if (sawResult) runHint.textContent = "完成。可修改需求后重新运行。";
  } catch (err) {
    if (err.name === "AbortError") {
      runHint.textContent = "已停止本次运行。";
    } else {
      showError(`请求失败：${err.message}`);
    }
  } finally {
    state.controller = null;
    setRunning(false);
    removeWaiting();
  }
}

function currentQuery() {
  return queryInput.value.trim();
}

async function loadHealth() {
  try {
    const resp = await fetch("/healthz");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const health = await resp.json();
    const mode = health.mode === "offline" ? "offline demo" : "real LLM";
    const modeChip = $("mode-chip");
    modeChip.replaceChildren();
    modeChip.append(el("i"), document.createTextNode(` ${mode}`));
    modeChip.classList.add("ok");
    $("backend-chip").textContent = health.backend || "backend —";
    $("version-chip").textContent = health.version ? `v${health.version}` : "v—";
    if (!state.running) {
      runHint.textContent =
        health.mode === "offline"
          ? "离线模式：脚本化 LLM + 合成候选人，零 API key · Ctrl+Enter 运行。"
          : "真实 LLM 模式：每次运行都会调用模型 API · Ctrl+Enter 运行。";
    }
  } catch (err) {
    $("mode-chip").classList.add("err");
    $("mode-chip").textContent = "offline";
  }
}

function bindEvents() {
  document.getElementById("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const query = currentQuery();
    if (!query) {
      queryInput.focus();
      runHint.classList.add("error");
      runHint.textContent = "请先填写招聘需求。";
      return;
    }
    const topK = Math.max(1, Math.min(50, Number(topKInput.value) || 5));
    topKInput.value = topK;
    runSearch(query, topK);
  });

  stopBtn.addEventListener("click", () => {
    state.controller?.abort();
  });

  queryInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      document.getElementById("search-form").requestSubmit();
    }
  });

  for (const button of document.querySelectorAll(".example")) {
    button.addEventListener("click", () => {
      queryInput.value = button.dataset.q || "";
      queryInput.focus();
    });
  }
}

function initFromUrl() {
  const params = new URLSearchParams(location.search);
  const query = params.get("q");
  const topK = params.get("topK");
  if (query) queryInput.value = query;
  if (topK) topKInput.value = topK;
  if (params.get("auto") === "1" && query) {
    const k = Math.max(1, Math.min(50, Number(topK) || 5));
    runSearch(query, k);
  }
}

buildRail();
bindEvents();
initFromUrl();
loadHealth();
