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
const PAGE_SIZE = 5;
const CONVERSATION_KEY = "search-agent:conversation";

const state = {
  running: false,
  controller: null,
  startedAt: 0,
  executeCount: 0,
  stageCount: 0,
  conversationId: null,
  conversationTurns: 0,
  conversationHistory: [],
  queryToken: null,
  pageOffset: 0,
  pageLimit: PAGE_SIZE,
  pageTotal: 0,
  pageTotalPages: 1,
  pageCurrent: 1,
  pageHasMore: false,
  pageLoading: false,
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
const pager = $("pager");
const pageState = $("page-state");
const prevPageBtn = $("prev-page");
const nextPageBtn = $("next-page");
const conversationLabel = $("conversation-label");
const conversationHistory = $("conversation-history");
const newConversationBtn = $("new-conversation");

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

function loadConversationState() {
  try {
    const raw = sessionStorage.getItem(CONVERSATION_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    state.conversationId = saved.conversationId || null;
    state.conversationTurns = Number(saved.conversationTurns || 0);
    state.conversationHistory = Array.isArray(saved.history) ? saved.history.slice(-6) : [];
  } catch (err) {
    state.conversationId = null;
  }
}

function saveConversationState() {
  try {
    sessionStorage.setItem(
      CONVERSATION_KEY,
      JSON.stringify({
        conversationId: state.conversationId,
        conversationTurns: state.conversationTurns,
        history: state.conversationHistory.slice(-6),
      }),
    );
  } catch (err) {
    // The in-memory conversation still works when storage is unavailable.
  }
}

function renderConversation() {
  const round = Math.max(1, state.conversationTurns || 1);
  conversationLabel.textContent = state.conversationId
    ? `第 ${round} 轮 · 续接当前会话`
    : "新会话 · 第 1 轮";
  conversationHistory.replaceChildren();
  state.conversationHistory.forEach((turn, index) => {
    const li = el("li");
    li.append(
      el("b", "", `${index + 1}`),
      el("span", "", turn.query || ""),
      el("small", "", `${turn.total ?? 0} 人`),
    );
    li.title = turn.query || "";
    li.addEventListener("click", () => {
      queryInput.value = turn.query || "";
      queryInput.focus();
    });
    conversationHistory.append(li);
  });
  conversationHistory.hidden = state.conversationHistory.length === 0;
}

function rememberConversationTurn(query, total) {
  const last = state.conversationHistory[state.conversationHistory.length - 1];
  if (!last || last.query !== query) {
    state.conversationHistory.push({ query, total });
    state.conversationHistory = state.conversationHistory.slice(-6);
  } else {
    last.total = total;
  }
  saveConversationState();
  renderConversation();
}

function resetConversation() {
  state.conversationId = null;
  state.conversationTurns = 0;
  state.conversationHistory = [];
  state.queryToken = null;
  try {
    sessionStorage.removeItem(CONVERSATION_KEY);
  } catch (err) {
    // Ignore storage failures; clearing in-memory state is enough.
  }
  hidePager();
  renderConversation();
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
  hidePager();
  state.queryToken = null;
  state.pageOffset = 0;
  state.pageTotal = 0;
  state.pageTotalPages = 1;
  state.pageCurrent = 1;
  state.pageHasMore = false;
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
  stopBtn.disabled = false;
  newConversationBtn.disabled = active;
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
    if (detail.historyTurns) parts.push(kv("历史上下文", `${detail.historyTurns} 轮`));
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

function hidePager() {
  pager.hidden = true;
  pageState.textContent = "—";
}

function updatePager(page) {
  if (!page) {
    hidePager();
    return;
  }
  state.pageOffset = Number(page.offset || 0);
  state.pageLimit = Number(page.limit || PAGE_SIZE);
  state.pageTotal = Number(page.total || 0);
  state.pageTotalPages = Math.max(1, Number(page.totalPages || 1));
  state.pageCurrent = Math.max(1, Number(page.currentPage || 1));
  state.pageHasMore = Boolean(page.hasMore);
  const visible = state.pageTotalPages > 1;
  pager.hidden = !visible;
  if (!visible) return;
  pageState.textContent = `第 ${state.pageCurrent} / ${state.pageTotalPages} 页 · 共 ${state.pageTotal} 人`;
  prevPageBtn.disabled = state.pageLoading || state.pageCurrent <= 1;
  nextPageBtn.disabled = state.pageLoading || !state.pageHasMore;
}

function renderCandidateList(items, offset = 0) {
  results.replaceChildren();
  items.forEach((item, index) => results.append(renderCandidate(item, offset + index + 1)));
}

function renderResults(payload) {
  const items = payload.items || [];
  const clarification = payload.clarification;

  if (clarification?.needClarification) {
    results.replaceChildren();
    hidePager();
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
  const page = payload.page || null;
  const offset = Number(page?.offset || 0);
  const total = Number(page?.total ?? payload.total ?? items.length);
  renderCandidateList(items, offset);
  updatePager(page);
  resultsHint.textContent = page
    ? `${items.length} 人本页${total !== items.length ? ` · 共 ${total} 人` : ""}`
    : `${items.length} 人交付`;
  $("results").scrollTop = 0;
}

async function loadSessionPage(offset) {
  if (!state.queryToken || state.pageLoading) return;
  state.pageLoading = true;
  updatePager({
    offset: state.pageOffset,
    limit: state.pageLimit,
    total: state.pageTotal,
    currentPage: state.pageCurrent,
    totalPages: state.pageTotalPages,
    hasMore: state.pageHasMore,
  });
  try {
    const resp = await fetch(
      `/sessions/${encodeURIComponent(state.queryToken)}?offset=${offset}&limit=${state.pageLimit}`,
    );
    if (resp.status === 404) throw new Error("该分页会话已过期，请重新运行搜索。");
    if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
    const payload = await resp.json();
    renderCandidateList(payload.items || [], Number(payload.offset || 0));
    resultsEmpty.hidden = (payload.items || []).length > 0;
    updatePager(payload);
    resultsHint.textContent = `${(payload.items || []).length} 人本页${payload.total ? ` · 共 ${payload.total} 人` : ""}`;
    $("results").scrollTop = 0;
  } catch (err) {
    runHint.classList.add("error");
    runHint.textContent = err.message;
  } finally {
    state.pageLoading = false;
    updatePager({
      offset: state.pageOffset,
      limit: state.pageLimit,
      total: state.pageTotal,
      currentPage: state.pageCurrent,
      totalPages: state.pageTotalPages,
      hasMore: state.pageHasMore,
    });
  }
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

const INTENT_LABEL = {
  GREETING: "问候",
  HELP: "能力说明",
  OUT_OF_SCOPE: "范围说明",
};

function addRoutingNote() {
  const li = el("li", "routing-note");
  const body = el("div", "routing-copy");
  body.append(
    el("strong", "", "意图路由已完成"),
    el("span", "", "本次输入未启动八阶段 Agent，也未调用 LLM。"),
  );
  li.append(el("span", "routing-dot"), body);
  timeline.append(li);
  timeline.scrollTop = timeline.scrollHeight;
}

function renderAssistantReply(payload) {
  results.replaceChildren();
  resultsEmpty.hidden = true;
  metrics.hidden = true;
  traceWrap.hidden = true;
  resultsHint.textContent = "未启动 Agent";

  const card = el("li", "assistant-card");
  const head = el("div", "assistant-head");
  const title = el("div", "assistant-title");
  title.append(
    el("strong", "", "Search Agent"),
    el("span", "assistant-intent", INTENT_LABEL[payload.intent] || payload.intent || "提示"),
  );
  head.append(el("span", "assistant-mark", "SA"), title);
  card.append(head, el("p", "assistant-message", payload.message || ""));

  const suggestions = payload.suggestions || [];
  if (suggestions.length) {
    const actions = el("div", "reply-suggestions");
    for (const item of suggestions) {
      const button = el("button", "reply-suggestion", item.label || item.query || "示例需求");
      button.type = "button";
      button.title = item.query || "";
      button.addEventListener("click", () => {
        queryInput.value = item.query || "";
        queryInput.focus();
        runHint.classList.remove("error");
        runHint.textContent = "已填入示例需求，点击“运行 Agent”开始搜索。";
      });
      actions.append(button);
    }
    card.append(el("p", "suggestion-label", "可以试试"), actions);
  }
  results.append(card);
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
  let sawReply = false;
  let sawError = false;
  let sawCancelled = false;

  try {
    const resp = await fetch("/search/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        topK,
        details: true,
        pageSize: PAGE_SIZE,
        conversationId: state.conversationId,
      }),
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
        } else if (event === "reply") {
          sawReply = true;
          removeWaiting();
          addRoutingNote();
          renderAssistantReply(payload);
          timelineHint.textContent = "已回复 · Agent 未启动";
        } else if (event === "result") {
          sawResult = true;
          sawCancelled = Boolean(payload.debug && payload.debug.cancelled);
          if (payload.conversationId) {
            state.conversationId = payload.conversationId;
            state.conversationTurns = Number(payload.conversationTurns || state.conversationTurns || 1);
            saveConversationState();
          }
          state.queryToken = payload.queryToken || null;
          removeWaiting();
          renderResults(payload);
          renderMetrics(payload);
          renderTrace(payload);
          if (!sawCancelled) {
            rememberConversationTurn(query, Number(payload.total || 0));
          }
          timelineHint.textContent = sawCancelled
            ? "已取消 · 未产出部分结果"
            : `完成 · ${state.stageCount} 个阶段事件 · ${Math.max(1, state.executeCount)} 轮`;
        } else if (event === "error") {
          sawError = true;
          showError(`Agent 执行失败：${payload.message || "unknown error"}`);
        }
      }
    }
    if (!sawResult && !sawReply && !sawError && state.running) {
      showError("连接已结束，但没有收到最终结果。");
    }
    if (sawResult && sawCancelled) {
      runHint.textContent = "已取消。后端会在当前阶段结束后停止后续工作。";
    } else if (sawReply) {
      runHint.textContent = "已回复。此请求不需要启动搜索 Agent。";
    } else if (sawResult) {
      runHint.textContent = "完成。可修改需求后重新运行。";
    }
  } catch (err) {
    if (err.name === "AbortError") {
      runHint.textContent = "已请求停止：当前阶段结束后不会再发起新的检索或模型调用。";
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
    const topK = Math.max(1, Math.min(50, Number(topKInput.value) || 20));
    topKInput.value = topK;
    runSearch(query, topK);
  });

  stopBtn.addEventListener("click", () => {
    if (!state.controller) return;
    stopBtn.disabled = true;
    runHint.classList.remove("error");
    runHint.textContent = "正在停止：等待当前阶段结束…";
    state.controller.abort();
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

  newConversationBtn.addEventListener("click", () => {
    if (state.running) return;
    resetConversation();
    resetUI();
    queryInput.value = "";
    timelineEmpty.hidden = false;
    resultsEmpty.hidden = false;
    runHint.classList.remove("error");
    runHint.textContent = "已开启新会话。下一条需求不会引用之前的上下文。";
    queryInput.focus();
  });

  prevPageBtn.addEventListener("click", () => {
    loadSessionPage(Math.max(0, state.pageOffset - state.pageLimit));
  });

  nextPageBtn.addEventListener("click", () => {
    if (state.pageHasMore) loadSessionPage(state.pageOffset + state.pageLimit);
  });
}

function initFromUrl() {
  const params = new URLSearchParams(location.search);
  const query = params.get("q");
  const topK = params.get("topK");
  if (query) queryInput.value = query;
  if (topK) topKInput.value = topK;
  if (params.get("auto") === "1" && query) {
    const k = Math.max(1, Math.min(50, Number(topK) || 20));
    runSearch(query, k);
  }
}

loadConversationState();
buildRail();
renderConversation();
bindEvents();
initFromUrl();
loadHealth();
