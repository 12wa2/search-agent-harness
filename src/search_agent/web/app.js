const STAGES = [
  ["understand", "读取岗位"],
  ["plan", "制定计划"],
  ["execute", "调用工具"],
  ["judge", "筛选候选人"],
  ["evaluate", "检查覆盖"],
  ["reflect", "定位缺口"],
  ["revise", "改写策略"],
  ["finalize", "交付名单"],
];

const STAGE_LABEL = Object.fromEntries(STAGES);
STAGE_LABEL.direct_search = "解析需求";
const STAGE_KEYS = STAGES.map(([key]) => key);
const ROUTE_STAGES = Object.freeze({
  direct_search: ["direct_search", "execute", "finalize"],
  single_pass: ["understand", "execute", "judge", "evaluate", "finalize"],
  reduced: STAGE_KEYS,
  standard: STAGE_KEYS,
});
const ROUTE_LABEL = Object.freeze({
  direct_search: "直连搜索",
  single_pass: "轻量搜索",
  reduced: "缩减反思",
  standard: "完整反思",
});
const STAGE_META = {
  direct_search: { kind: "PARSE", glyph: "⌁" },
  understand: { kind: "READ", glyph: "01" },
  plan: { kind: "PLAN", glyph: "02" },
  execute: { kind: "TOOL CALL", glyph: "↳" },
  judge: { kind: "CHECK", glyph: "✓" },
  evaluate: { kind: "DECIDE", glyph: "?" },
  reflect: { kind: "REFLECT", glyph: "!" },
  revise: { kind: "REVISE", glyph: "↻" },
  finalize: { kind: "DELIVER", glyph: "◆" },
};
const PAGE_SIZE = 5;
const COMPARE_LIMIT = 4;
const COMPARE_VIEW_DIFF = "diff";
const COMPARE_VIEW_ALL = "all";
const DEFAULT_CHAT_PLACEHOLDER = "描述目标岗位、必备能力、城市与排除条件…";
const CONVERSATION_KEY = "search-agent:conversation";
const VIEW_KEY = "search-agent:view";
const SIDEBAR_KEY = "search-agent:sidebar";
const RESUME_PAGE = "/resume.html";
const RESUME_CACHE_PREFIX = "search-agent:candidate:";
const FINALIZE_REASON_COPY = Object.freeze({
  target_reached: "达到目标人数，搜索提前收敛。",
  max_iterations: "达到策略调整次数上限，交付当前最优结果。",
  time_budget: "达到时间预算上限，交付当前最优结果。",
  llm_budget: "达到模型调用预算上限，交付当前最优结果。",
  token_budget: "达到 Token 预算上限，交付当前最优结果。",
  no_progress: "本轮没有新增合格候选人，停止继续搜索。",
  profile_converged: "候选人画像已收敛，进入最终验证并交付。",
  direct_search: "明确条件已由 Hybrid V2 parser 解析为 IR，本次只调用一次 SearchTool，不启动 Agent 反思链路。",
  difficulty_single_pass: "需求清晰且难度较低，本次使用单次检索完成。",
  paths_exhausted: "反思后没有新的可执行路径，已收敛现有结果。",
  no_path_actions: "反思没有产生可执行的策略动作，已收敛现有结果。",
  reflection_empty: "没有可用的反思结果，已收敛现有结果。",
});
const ROUND_ACTION_LABEL = Object.freeze({
  add_path: "补充路径",
  loosen: "放宽条件",
  tighten: "收紧条件",
  drop: "移除路径",
  clarify: "请求澄清",
});

const state = {
  viewMode: "agent",
  runtimeMode: null,
  onlineConfigured: false,
  modeSwitching: false,
  running: false,
  controller: null,
  startedAt: 0,
  activeRun: null,
  activeWorkbench: null,
  workbenchSequence: 0,
  routeMeta: null,
  lastResult: null,
  runEvents: [],
  executeCount: 0,
  stageCount: 0,
  conversationId: null,
  conversationTurns: 0,
  conversationHistory: [],
  conversations: [],
  conversationsLoading: false,
  excludedCandidateIds: new Set(),
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
const agentRunbar = $("agent-runbar");
const agentState = $("agent-state");
const agentStateDetail = $("agent-state-detail");
const agentStageCount = $("agent-stage-count");
const agentRound = $("agent-round");
const pager = $("pager");
const pageState = $("page-state");
const prevPageBtn = $("prev-page");
const nextPageBtn = $("next-page");
const conversationLabel = $("conversation-label");
const conversationHistory = $("conversation-history");
const newConversationBtn = $("new-conversation");
const agentSessionList = $("agent-session-list");
const agentSessionCount = $("agent-session-count");
const agentSessionEmpty = $("agent-session-empty");
const sidebarNewBtn = $("sidebar-new");
const agentSidebar = $("agent-sidebar");
const agentMain = document.querySelector(".agent-main");
const sidebarToggle = $("sidebar-toggle");
const sidebarToggleLabel = $("sidebar-toggle-label");
const sidebarCollapse = $("sidebar-collapse");
const sidebarRailToggle = $("sidebar-rail-toggle");
const sidebarContent = $("sidebar-content");
const sidebarScrim = $("agent-sidebar-scrim");
const sidebarMediaQuery = window.matchMedia("(max-width: 980px)");

function stageKeysForRoute(route, plannedStages) {
  if (Array.isArray(plannedStages) && plannedStages.length) {
    return plannedStages.filter((stage) => STAGE_LABEL[stage]);
  }
  return ROUTE_STAGES[route] || STAGE_KEYS;
}

function routeLabel(route) {
  return ROUTE_LABEL[route] || "自适应反思";
}

function routeFootnote(route) {
  if (route === "direct_search") return "直连搜索：Hybrid V2 parser LLM 将明确需求解析为 IR，再调用一次 SearchTool；不启动 understand / plan / judge / reflect，Agent LLM 调用为 0。";
  if (route === "single_pass") return "轻量搜索：跳过制定计划、反思与策略改写，只保留真实执行的检索阶段。";
  if (route === "reduced") return "缩减反思：按查询难度限制路径数与反思轮次，未执行的阶段会标记为跳过。";
  return "完整反思：允许规划多条路径，并根据覆盖检查决定是否继续反思。";
}

function routeStageTotal(route, plannedStages) {
  if (Array.isArray(plannedStages) && plannedStages.length) return plannedStages.length;
  if (route === "direct_search") return 3;
  return route === "single_pass" ? 5 : 8;
}

function resetRouteSummary() {
  const intent = $("route-intent");
  const strategy = $("route-strategy");
  const rule = $("route-rule");
  const stageCount = $("route-stage-count");
  const stageDetail = $("route-stage-detail");
  if (intent) intent.textContent = "—";
  if (strategy) strategy.textContent = "—";
  if (rule) rule.textContent = "等待判定";
  if (stageCount) stageCount.textContent = "阶段待判定";
  if (stageDetail) stageDetail.textContent = "等待 Agent 事件";
}

function updateRouteSummary({
  intent = "SEARCH",
  intentRule = "search",
  difficultyRoute,
  plannedStages,
  executedStages,
  executedCount,
} = {}) {
  const route = difficultyRoute || "";
  const intentNode = $("route-intent");
  const strategyNode = $("route-strategy");
  const ruleNode = $("route-rule");
  const stageCountNode = $("route-stage-count");
  const stageDetailNode = $("route-stage-detail");
  if (intentNode) intentNode.textContent = `${intent} · ${INTENT_LABEL[intent] || "自定义"}`;
  if (strategyNode) strategyNode.textContent = route ? routeLabel(route) : "待判定";
  if (ruleNode) ruleNode.textContent = `规则 ${intentRule || "—"}`;
  if (stageCountNode) {
    stageCountNode.textContent = route
      ? `计划 ${routeStageTotal(route, plannedStages)} 阶段`
      : "阶段待判定";
  }
  if (stageDetailNode) {
    const executed = Number.isFinite(executedCount)
      ? executedCount
      : Array.isArray(executedStages)
        ? executedStages.length
        : 0;
    stageDetailNode.textContent = executed ? `已执行 ${executed} 阶段` : "等待阶段事件";
  }
}
const agentView = $("agent-view");
const consoleView = $("console-view");
const viewAgentBtn = $("view-agent");
const viewConsoleBtn = $("view-console");
const chatScroll = $("chat-scroll");
const chatIntro = $("agent-intro");
const chatTranscript = $("chat-transcript");
const chatForm = $("chat-form");
const chatQueryInput = $("chat-query");
const chatTopKInput = $("chat-top-k");
const chatRunBtn = $("chat-run");
const chatStopBtn = $("chat-stop");
const chatNewBtn = $("chat-new");
const composerMode = $("composer-mode");
const composerNote = $("composer-note");
const compareDialog = $("candidate-compare-dialog");
const compareGrid = $("candidate-compare-grid");
const compareCopy = $("candidate-compare-copy");
const compareOverview = $("candidate-compare-overview");
const compareMatrix = $("candidate-compare-matrix");
const compareCloseBtn = $("candidate-compare-close");
const compareViewButtons = document.querySelectorAll("[data-compare-view]");
const agentLiveStatus = $("agent-live-status");
const modeChip = $("mode-chip");
const modeLabel = $("mode-label");
const onlineModeDialog = $("online-mode-dialog");
let agentLiveStatusTimer = 0;

function defaultComposerNote() {
  return state.runtimeMode === "offline" || !state.runtimeMode
    ? "当前快速演示：脚本化 LLM + 16 人内存库，无需 API Key · Enter 发送，Shift+Enter 换行"
    : "当前完整在线：在线 LLM + Hybrid V2 + Elasticsearch 候选人库 · Enter 发送，Shift+Enter 换行";
}

function setComposerNote(text, { error = false } = {}) {
  composerNote.textContent = text;
  composerNote.classList.toggle("is-error", error);
}

function setAgentResultMode(active) {
  const enabled = Boolean(active);
  if (agentMain) agentMain.classList.toggle("has-agent-result", enabled);
  if (chatQueryInput) {
    chatQueryInput.rows = enabled ? 1 : 2;
    chatQueryInput.placeholder = enabled
      ? "继续追问，或补充城市、经验与排除条件…"
      : DEFAULT_CHAT_PLACEHOLDER;
  }
  if (!enabled && compareDialog?.open) compareDialog.close();
}

function announceAgentStatus(message) {
  if (!agentLiveStatus || !message) return;
  window.clearTimeout(agentLiveStatusTimer);
  agentLiveStatus.textContent = "";
  agentLiveStatusTimer = window.setTimeout(() => {
    agentLiveStatus.textContent = String(message);
  }, 100);
}

function syncModeToggleState() {
  if (!modeChip) return;
  modeChip.disabled = !state.runtimeMode || state.running || state.modeSwitching;
}

function renderRuntimeMode({ healthy = true } = {}) {
  if (!modeChip) return;
  const mode = state.runtimeMode;
  modeChip.classList.toggle("ok", healthy && Boolean(mode));
  modeChip.classList.toggle("err", !healthy);
  if (!mode) {
    modeChip.dataset.mode = "";
    modeChip.setAttribute("aria-pressed", "false");
    modeChip.removeAttribute("title");
    modeChip.setAttribute("aria-label", healthy ? "运行模式读取中" : "运行模式状态未知");
    if (modeLabel) modeLabel.textContent = healthy ? "读取中…" : "状态未知";
    composerMode.textContent = healthy ? "读取中…" : "状态未知";
    syncModeToggleState();
    return;
  }

  const offline = mode === "offline";
  const label = offline ? "快速演示" : "完整在线";
  const targetLabel = offline ? "完整在线" : "快速演示";
  modeChip.dataset.mode = mode;
  modeChip.setAttribute("aria-pressed", String(!offline));
  if (modeLabel) modeLabel.textContent = label;
  composerMode.textContent = label;

  let ariaLabel = `当前${label}，点击切换到${targetLabel}`;
  if (state.running) {
    ariaLabel = `当前${label}，Agent 运行中，完成或停止后可切换`;
  } else if (offline && !state.onlineConfigured) {
    ariaLabel = "当前快速演示，完整在线尚未配置，点击查看说明";
  }
  modeChip.removeAttribute("title");
  modeChip.setAttribute("aria-label", ariaLabel);
  syncModeToggleState();
}

function applyRuntimeHealth(health) {
  state.runtimeMode = health.mode === "offline" ? "offline" : "online";
  state.onlineConfigured = Boolean(health.onlineConfigured);
  if (health.backend) $("backend-chip").textContent = health.backend;
  if (health.version) $("version-chip").textContent = `v${health.version}`;
  renderRuntimeMode();
  if (!state.running) {
    runHint.classList.remove("error");
    runHint.textContent =
      state.runtimeMode === "offline"
        ? "快速演示：脚本化 LLM + 16 人内存库，零 API key · Ctrl+Enter 运行。"
        : "完整在线：在线 LLM + Hybrid V2 + Elasticsearch 候选人库 · Ctrl+Enter 运行。";
    setComposerNote(defaultComposerNote());
  }
}

function setRuntimeNotice(text, { error = false } = {}) {
  runHint.classList.toggle("error", error);
  runHint.textContent = text;
  setComposerNote(text, { error });
}

function confirmOnlineMode() {
  if (!onlineModeDialog) return Promise.resolve(true);
  if (typeof onlineModeDialog.showModal !== "function") {
    return Promise.resolve(
      window.confirm(
        "完整在线会调用真实模型并连接 Elasticsearch，可能产生 API 费用。切换只影响后续搜索，是否继续？",
      ),
    );
  }
  return new Promise((resolve) => {
    const handleClose = () => {
      onlineModeDialog.removeEventListener("close", handleClose);
      resolve(onlineModeDialog.returnValue === "confirm");
    };
    onlineModeDialog.returnValue = "cancel";
    onlineModeDialog.addEventListener("close", handleClose);
    onlineModeDialog.showModal();
  });
}

async function toggleRuntimeMode() {
  if (!state.runtimeMode || state.running || state.modeSwitching) return;
  const target = state.runtimeMode === "offline" ? "online" : "offline";
  const targetLabel = target === "online" ? "完整在线" : "快速演示";
  if (target === "online" && !state.onlineConfigured) {
    setRuntimeNotice(
      "完整在线尚未配置：请检查模型 Key 与 Elasticsearch 索引配置后重启服务。",
      { error: true },
    );
    return;
  }
  if (target === "online") {
    const confirmed = await confirmOnlineMode();
    if (!confirmed || state.running || state.modeSwitching) {
      if (!confirmed) setRuntimeNotice("已保留快速演示模式，未发起在线请求。");
      return;
    }
  }
  state.modeSwitching = true;
  renderRuntimeMode();
  try {
    const resp = await fetch("/runtime/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: target }),
      cache: "no-store",
    });
    let payload = {};
    try {
      payload = await resp.json();
    } catch (err) {
      // Keep the HTTP status as the fallback error message.
    }
    if (!resp.ok) {
      const detail =
        typeof payload.detail === "string" ? payload.detail : `HTTP ${resp.status}`;
      throw new Error(detail);
    }
    applyRuntimeHealth(payload);
    setRuntimeNotice(
      `已切换到${targetLabel}。${target === "online" ? "下一次搜索使用在线 LLM + Hybrid V2 检索" : "下一次搜索使用脚本 LLM + 16 人内存库"}。`,
    );
  } catch (err) {
    setRuntimeNotice(`运行模式切换失败：${err.message}`, { error: true });
  } finally {
    state.modeSwitching = false;
    renderRuntimeMode();
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function chip(text, extra = "") {
  return el("span", `tag ${extra}`.trim(), text);
}

function summaryWithLabel(text, label) {
  const summary = el("summary", "", text);
  summary.setAttribute("aria-label", label);
  return summary;
}

function deliveryOverview(payload) {
  const target = Math.max(0, Number(payload.topK ?? state.activeRun?.topK ?? 0));
  const delivered = Math.max(0, Number(payload.total || 0));
  const gap = Math.max(0, target - delivered);
  const reached = target > 0 && delivered >= target;
  const reasonCode = payload.debug?.finalizeReason || "";
  const hardSummary = payload.hardConditionSummary || payload.debug?.hardConditionSummary || {};
  const hardViolated = Math.max(0, Number(hardSummary.violated || 0));
  const hardUnverified = Math.max(0, Number(hardSummary.unverified || 0));
  const hardAffected = Math.max(
    hardViolated + hardUnverified,
    Number(hardSummary.affected || 0),
  );
  return {
    target,
    delivered,
    gap,
    reached,
    hardStatus: hardSummary.status || "not_applicable",
    hardViolated,
    hardUnverified,
    hardAffected,
    hasHardGap: hardAffected > 0,
    reasonCode,
    reason: FINALIZE_REASON_COPY[reasonCode] || "",
  };
}

function resolveExecutionRounds(payload) {
  const summaries = payload?.debug?.roundSummaries;
  if (Array.isArray(summaries) && summaries.length > 0) {
    return summaries.length;
  }
  const totals = payload?.debug?.agentMetrics?.rounds?.qualifiedTotalPerRound;
  if (Array.isArray(totals) && totals.length > 0) {
    return totals.length;
  }
  if (payload?.debug?.directSearch) return 1;
  const executed = Number(state.executeCount || 0);
  return Number.isFinite(executed) && executed > 0 ? executed : 0;
}

function resolveReflectionIterations(payload) {
  const iterations = Number(payload?.debug?.agentMetrics?.rounds?.iterations || 0);
  return Number.isFinite(iterations) && iterations > 0 ? iterations : 0;
}

function hardGapBreakdown(overview) {
  const parts = [];
  if (overview.hardViolated > 0) parts.push(`违规 ${overview.hardViolated} 人`);
  if (overview.hardUnverified > 0) parts.push(`待核实 ${overview.hardUnverified} 人`);
  return parts.join("、");
}

function fillClarificationQuery(option) {
  const query = String(option?.query || "").trim();
  if (!query) return;
  queryInput.value = query;
  chatQueryInput.value = query;
  if (state.viewMode === "agent") {
    setComposerNote("已填入所选方向，确认或继续补充后发送。");
    chatQueryInput.focus();
  } else {
    runHint.classList.remove("error");
    runHint.textContent = "已填入所选方向，点击“运行 Agent”开始搜索。";
    queryInput.focus();
  }
  scrollChatToEnd("smooth");
}

function appendClarificationOptions(container, options = []) {
  const usable = options.filter((option) => option?.query);
  if (!usable.length) return;
  container.append(el("p", "clarification-options-label", "选择一个更具体的方向"));
  const grid = el("div", "clarification-options");
  for (const option of usable) {
    const button = el("button", "clarification-option");
    button.type = "button";
    button.title = option.query;
    button.append(
      el("strong", "", option.label || "选择这个方向"),
      el("span", "", option.description || ""),
      el("small", "", "填入需求 →"),
    );
    button.addEventListener("click", () => fillClarificationQuery(option));
    grid.append(button);
  }
  container.append(grid);
}

const CHAT_STATUS_TONES = [
  "is-running",
  "is-complete",
  "is-degraded",
  "is-stopped",
  "is-failed",
  "is-clarify",
];

function setChatStatus(statusNode, label, tone) {
  if (!statusNode) return;
  statusNode.textContent = label;
  statusNode.classList.remove(...CHAT_STATUS_TONES);
  if (tone) statusNode.classList.add(`is-${tone}`);
}

function setAgentState(stateName, title, detail) {
  if (!agentRunbar) return;
  agentRunbar.dataset.state = stateName;
  agentState.textContent = title;
  agentStateDetail.textContent = detail;
}

function setAgentProgress() {
  if (!agentStageCount || !agentRound) return;
  agentStageCount.textContent = String(state.stageCount);
  agentRound.textContent = String(Math.max(0, state.executeCount));
}

function kv(label, value) {
  const wrap = el("span", "kv");
  wrap.append(el("span", "", `${label}: `), el("b", "", value));
  return wrap;
}

function setViewMode(mode, { persist = true } = {}) {
  const next = mode === "console" ? "console" : "agent";
  state.viewMode = next;
  document.body.dataset.view = next;
  if (agentView) agentView.hidden = next !== "agent";
  if (consoleView) consoleView.hidden = next !== "console";
  if (viewAgentBtn) {
    const active = next === "agent";
    viewAgentBtn.classList.toggle("is-active", active);
    viewAgentBtn.setAttribute("aria-pressed", String(active));
  }
  if (viewConsoleBtn) {
    const active = next === "console";
    viewConsoleBtn.classList.toggle("is-active", active);
    viewConsoleBtn.setAttribute("aria-pressed", String(active));
  }
  const skipLink = document.querySelector(".skip-link");
  if (skipLink) skipLink.setAttribute("href", next === "agent" ? "#chat-query" : "#query");
  if (persist) {
    try {
      sessionStorage.setItem(VIEW_KEY, next);
      const url = new URL(location.href);
      url.searchParams.set("view", next);
      history.replaceState(null, "", url);
    } catch (err) {
      // View preference is optional.
    }
  }
  if (next === "agent" && chatTranscript.childElementCount) scrollChatToEnd("auto");
}

function scrollChatToEnd(behavior = "smooth") {
  requestAnimationFrame(() => {
    if (!chatScroll || chatScroll.hidden) return;
    chatScroll.scrollTo({ top: chatScroll.scrollHeight, behavior });
  });
}

function scrollChatToNode(node, behavior = "smooth") {
  requestAnimationFrame(() => {
    if (!chatScroll || !node || chatScroll.hidden) return;
    const offset = node.getBoundingClientRect().top - chatScroll.getBoundingClientRect().top;
    chatScroll.scrollTo({ top: Math.max(0, chatScroll.scrollTop + offset - 12), behavior });
  });
}

function createChatMessage(role, extraClass = "") {
  const article = el("article", `chat-message chat-message-${role} ${extraClass}`.trim());
  if (role === "agent") {
    const avatar = el("span", "chat-avatar", "SA");
    avatar.setAttribute("aria-hidden", "true");
    const body = el("div", "chat-message-body");
    const label = el("div", "chat-message-label");
    const status = el("span", "chat-message-status is-running", "执行中");
    label.append(el("strong", "", "Search Agent"), status);
    body.append(label);
    article.append(avatar, body);
    chatTranscript.append(article);
    return { article, body, status };
  }
  const body = el("div", "chat-message-body");
  article.append(body);
  chatTranscript.append(article);
  return { article, body, status: null };
}

function appendChatUserMessage(query) {
  const { body } = createChatMessage("user", "agent-task-message");
  const bubble = el("div", "chat-user-bubble");
  bubble.append(el("p", "", query));
  body.append(bubble);
  scrollChatToEnd();
}

function appendChatPending() {
  const { article, body } = createChatMessage("agent", "chat-pending-message");
  const pending = el("div", "chat-typing");
  pending.append(el("span"), el("span"), el("span"), el("small", "", "正在理解你的需求"));
  body.append(pending);
  scrollChatToEnd();
  return article;
}

const CONDITION_LABEL = {
  name: "姓名",
  skill: "技能",
  company: "公司",
  position: "岗位",
  city: "城市",
  intention_city: "意向城市",
  live_city: "现居城市",
  work_years: "工作年限",
  degree: "学历",
  school: "学校",
  major: "专业",
  expected_salary: "期望薪资",
  age: "年龄",
  language: "语言",
  certificate: "证书",
};

const HARD_CONDITION_LABELS = {
  satisfied: "满足",
  violated: "违规",
  unverified: "待核实",
  not_applicable: "未核验",
};

function formatCondition(condition = {}) {
  const label = CONDITION_LABEL[condition.field] || condition.field || "条件";
  const values = (condition.values || []).join(" / ");
  if (condition.operator === "not") return `排除 ${values}`;
  if (condition.field === "work_years") {
    if (condition.operator === "gte") return `${values} 年以上经验`;
    if (condition.operator === "lte") return `${values} 年以内经验`;
    if (condition.operator === "range") return `${values} 年经验`;
    return `${label} ${values}`;
  }
  if (condition.field === "city" || condition.field === "intention_city" || condition.field === "live_city") {
    return `${label}：${values}`;
  }
  return `${label}：${values}`;
}

function renderChatCriteria(run, detail = {}) {
  const abilities = detail.mustAbilities || [];
  const constraints = detail.hardConstraintDetails || [];
  const needsClarification = Boolean(detail.needsClarification);
  const chips = run.criteriaChips;
  chips.replaceChildren();

  if (detail.jobTitle && !needsClarification) chips.append(chip(detail.jobTitle, "criterion-title"));
  abilities.slice(0, 8).forEach((ability) => chips.append(chip(ability, "criterion-skill")));
  constraints.slice(0, 8).forEach((condition) => {
    const label = formatCondition(condition);
    chips.append(chip(label, condition.operator === "not" ? "criterion-exclude" : "criterion-condition"));
  });
  if (!constraints.length && detail.hardConstraints) {
    chips.append(chip(`硬条件 ${detail.hardConstraints} 项`, "criterion-condition"));
  }
  if (detail.historyTurns) chips.append(chip(`承接此前 ${detail.historyTurns} 轮会话`, "criterion-history"));
  if (!chips.childElementCount) chips.append(chip("从原始需求中提取检索条件", "criterion-condition"));

  const title = detail.jobTitle ? `「${detail.jobTitle}」` : "这个岗位";
  if (needsClarification) {
    run.criteriaCopy.textContent = "我先识别到这些技能与线索，但还缺少岗位方向。确认方向后再开始规划检索，避免把单一技能误当成完整招聘需求。";
    run.criteriaBadge.textContent = "待确认";
    run.criteriaBadge.classList.remove("is-ready");
    run.criteriaCard.classList.remove("is-ready");
  } else {
    run.criteriaCopy.textContent = `我会优先寻找${title}方向，并重点核对以下能力与硬性条件。`;
    run.criteriaBadge.textContent = "已完成";
    run.criteriaBadge.classList.add("is-ready");
    run.criteriaCard.classList.add("is-ready");
  }
}

function chatStageCopy(stage, detail = {}) {
  const list = (values) => (values || []).filter(Boolean).join("、");
  if (stage === "direct_search") {
    const skills = list(detail.skills);
    const city = list(detail.cities);
    const signals = [skills ? `技能 ${skills}` : "", city ? `城市 ${city}` : ""]
      .filter(Boolean)
      .join("，");
    return `Hybrid V2 parser 已解析明确条件${signals ? `（${signals}）` : ""}并生成 CandidateSearchIR；随后直接调用 SearchTool，不启动 Agent 规划与反思循环。`;
  }
  if (stage === "understand") return "已读取岗位描述，提取必备能力、硬条件和可迁移经验。";
  if (stage === "plan") return `已规划 ${detail.paths || 0} 条互补的搜索路径，避免只依赖一个关键词。`;
  if (stage === "execute") {
    return `已执行第 ${Math.max(1, state.executeCount)} 轮检索，召回 ${detail.recalled || 0} 人次，正在整理候选人证据。`;
  }
  if (stage === "judge") return `已逐个核对能力证据与硬条件，累计 ${detail.qualified || 0} 人通过当前门槛。`;
  if (stage === "evaluate") {
    if (detail.next === "reflect") return "覆盖检查发现仍有能力缺口，准备换一个搜索方向继续验证。";
    if (detail.next === "finalize") return "能力覆盖检查通过，开始收敛名单并整理匹配依据。";
    return `本轮覆盖检查触发终止条件${detail.stopReason ? `：${detail.stopReason}` : ""}。`;
  }
  if (stage === "reflect") {
    return detail.gaps?.length
      ? `已定位到尚未覆盖的能力：${list(detail.gaps)}。下一轮会调整搜索策略。`
      : "已比较命中候选人，梳理仍需补齐的能力缺口。";
  }
  if (stage === "revise") return "已把缺口转换为新的搜索信号，并重新排列下一轮路径。";
  if (stage === "finalize") return `已收敛结果，本次交付 ${detail.total || 0} 位候选人。`;
  if (stage.startsWith("degrade")) return `执行触发降级${detail.reason ? `：${detail.reason}` : ""}，已完成的结果会保留。`;
  return "Agent 已记录本阶段结果，并继续判断下一步。";
}

function beginChatRun() {
  if (state.activeRun?.chatRun) return state.activeRun.chatRun;
  state.activeRun?.pending?.remove();

  const message = createChatMessage("agent", "chat-run-message agent-task-message");
  message.body.append(el("p", "chat-lead", "收到，我先确认一下你的招聘条件。"));

  const criteriaCard = el("section", "chat-criteria-card");
  const criteriaHead = el("div", "chat-criteria-head");
  const criteriaTitle = el("div");
  criteriaTitle.append(el("h3", "", "需求解析"), el("p", "chat-criteria-note", "来自本轮 understand 事件"));
  const criteriaBadge = el("span", "chat-criteria-badge", "读取中");
  criteriaHead.append(criteriaTitle, criteriaBadge);
  const criteriaCopy = el("p", "chat-criteria-copy", "正在读取岗位描述，提取可以执行的条件…");
  const criteriaChips = el("div", "chat-criteria-chips");
  criteriaCard.append(criteriaHead, criteriaCopy, criteriaChips);

  const progress = el("section", "chat-progress");
  const progressHead = el("div", "chat-progress-head");
  const progressTitle = el("strong", "", "Agent 正在执行");
  const progressMeta = el("span", "", "分析执行路由");
  progressHead.append(progressTitle, progressMeta);
  const progressTrack = el("div", "chat-progress-track");
  progressTrack.setAttribute("aria-hidden", "true");
  const progressList = el("ol", "chat-progress-list");
  const progressFootnote = el("p", "chat-progress-footnote", "正在判断应使用轻量搜索还是完整反思链路。");
  progress.append(progressHead, progressTrack, progressList, progressFootnote);

  message.body.append(criteriaCard, progress);
  const run = {
    ...message,
    criteriaCard,
    criteriaBadge,
    criteriaCopy,
    criteriaChips,
    progress,
    progressTitle,
    progressMeta,
    progressTrack,
    progressList,
    progressFootnote,
    visitedStages: new Set(),
    route: null,
    resultArticle: null,
  };
  state.activeRun.chatRun = run;
  scrollChatToEnd();
  return run;
}

function compactCompletedChatRun(run) {
  if (!run?.article) return;
  run.article.classList.add("is-complete");
  const lead = run.article.querySelector(".chat-lead");
  if (lead) lead.textContent = "已完成检索，结果见下方；执行过程可在「执行观测」查看。";
}

function renderProgressTrack(track, stageKeys) {
  track.replaceChildren();
  track.style.setProperty("--progress-cols", String(Math.max(1, stageKeys.length)));
  for (const stage of stageKeys) {
    const segment = el("span", "chat-progress-segment");
    segment.dataset.stage = stage;
    segment.title = STAGE_LABEL[stage] || stage;
    track.append(segment);
  }
}

function ensureProgressSegment(run, stage) {
  if (!STAGE_LABEL[stage]) return;
  if ([...run.progressTrack.children].some((segment) => segment.dataset.stage === stage)) return;
  const segment = el("span", "chat-progress-segment");
  segment.dataset.stage = stage;
  segment.title = STAGE_LABEL[stage];
  run.progressTrack.append(segment);
  run.progressTrack.style.setProperty("--progress-cols", String(run.progressTrack.children.length));
}

function configureChatRoute(run, detail = {}) {
  const route = detail.difficultyRoute || "standard";
  if (run.route === route && run.progressTrack.children.length) return;
  run.route = route;
  renderProgressTrack(run.progressTrack, stageKeysForRoute(route, detail.plannedStages));
  run.progressFootnote.textContent = routeFootnote(route);
  const intent = detail.requestIntent || "SEARCH";
  const stages = routeStageTotal(route, detail.plannedStages);
  run.progressMeta.textContent = `${intent} · ${routeLabel(route)} · ${stages} 阶段`;
}

function addChatStage(stage, detail = {}) {
  const run = beginChatRun();
  if (detail.jobTitle && !run.jobTitle) run.jobTitle = detail.jobTitle;
  if (stage === "understand" || stage === "direct_search") {
    configureChatRoute(run, detail);
    if (stage === "direct_search") {
      const note = run.criteriaCard.querySelector(".chat-criteria-note");
      if (note) note.textContent = "来自本轮 direct_search 的 Hybrid V2 parser";
    }
    renderChatCriteria(run, detail);
  }
  ensureProgressSegment(run, stage);

  const item = el("li", "chat-progress-item is-active");
  const marker = el("span", "chat-progress-marker");
  const copy = el("div", "chat-progress-copy");
  const stageLabel = STAGE_LABEL[stage] || stage;
  const roundSuffix = stage === "execute" && state.executeCount > 1
    ? ` · 第 ${state.executeCount} 轮`
    : "";
  copy.append(
    el("strong", "", `${stageLabel}${roundSuffix}`),
    el("span", "", chatStageCopy(stage, detail)),
  );
  item.append(marker, copy);
  run.progressList.replaceChildren(item);
  if (stage === "finalize") {
    item.classList.remove("is-active");
    item.classList.add("is-done");
  }
  run.visitedStages.add(stage);
  for (const segment of run.progressTrack.children) {
    segment.classList.toggle("is-hit", run.visitedStages.has(segment.dataset.stage));
    segment.classList.toggle("is-current", segment.dataset.stage === stage);
  }
  run.progressMeta.textContent = `${run.visitedStages.size} 阶段 · ${routeLabel(run.route)} · ${state.stageCount} 个事件`;
  run.progressTitle.textContent = stage === "finalize"
    ? "执行完成"
    : run.route === "direct_search"
      ? "直连搜索执行中"
      : run.route === "single_pass"
        ? "轻量搜索执行中"
        : "Agent 正在执行";
  scrollChatToEnd();
}

function addChatCandidateCards(run, items, offset = 0) {
  const list = el("ul", "chat-candidates");
  items.forEach((item, index) => {
    const card = renderCandidate(item, offset + index + 1);
    card.classList.add("chat-candidate");
    list.append(card);
  });
  return list;
}

function replaceChatCandidateCards(items, offset = 0) {
  const workbench = state.activeWorkbench;
  if (workbench) {
    workbench.pageEntries = items.map((item, index) =>
      workbenchCandidateEntry(item, offset + index + 1, workbench.token),
    );
    renderWorkbenchList(workbench);
    updateWorkbenchChrome(workbench);
    return;
  }
  const run = state.activeRun?.chatRun;
  if (!run?.resultList) return;
  run.resultList.replaceChildren();
  items.forEach((item, index) => {
    const card = renderCandidate(item, offset + index + 1);
    card.classList.add("chat-candidate");
    run.resultList.append(card);
  });
}

function updateChatPager(payload = null) {
  const run = state.activeRun?.chatRun;
  if (!run?.resultPager) return;
  const page = payload?.page && typeof payload.page === "object"
    ? payload.page
    : payload ?? {
        offset: state.pageOffset,
        limit: state.pageLimit,
        total: state.pageTotal,
        currentPage: state.pageCurrent,
        totalPages: state.pageTotalPages,
        hasMore: state.pageHasMore,
      };
  const totalPages = Math.max(1, Number(page.totalPages ?? 1));
  const currentPage = Math.max(1, Number(page.currentPage ?? 1));
  const total = Math.max(0, Number(page.total ?? 0));
  const hasMore = Boolean(page.hasMore);
  const workbench = state.activeWorkbench;
  if (workbench && workbench.pager === run.resultPager) {
    workbench.page = {
      offset: Number(page.offset ?? 0),
      limit: Number(page.limit ?? PAGE_SIZE),
      total,
      currentPage,
      totalPages,
      hasMore,
    };
    syncWorkbenchPager(workbench);
    return;
  }
  run.resultPager.hidden = totalPages <= 1;
  if (totalPages <= 1) return;
  run.resultPageState.textContent = `第 ${currentPage} / ${totalPages} 页 · 共 ${total} 人`;
  run.resultPrevBtn.disabled = state.pageLoading || currentPage <= 1;
  run.resultNextBtn.disabled = state.pageLoading || !hasMore;
}

function createChatPager() {
  const pagerNode = el("nav", "chat-pager");
  pagerNode.setAttribute("aria-label", "候选人分页");
  const prev = el("button", "", "上一页");
  const stateLabel = el("span", "", "—");
  const next = el("button", "", "下一页");
  prev.type = "button";
  next.type = "button";
  prev.addEventListener("click", () => loadSessionPage(Math.max(0, state.pageOffset - state.pageLimit), { updateChat: true }));
  next.addEventListener("click", () => {
    if (state.pageHasMore) loadSessionPage(state.pageOffset + state.pageLimit, { updateChat: true });
  });
  pagerNode.append(prev, stateLabel, next);
  return { pagerNode, prev, next, stateLabel };
}

function workbenchCandidateKey(item, rank) {
  const explicit = item?.docId || item?.candidateProfileId || item?.id;
  return explicit ? String(explicit) : `candidate-${rank}`;
}

function workbenchCandidateEntry(item, rank, token) {
  return {
    key: workbenchCandidateKey(item, rank),
    item,
    rank,
    token,
  };
}

function workbenchSkillNames(item, limit = 3) {
  const names = [];
  const seen = new Set();
  for (const skill of Array.isArray(item?.skills) ? item.skills : []) {
    const name = String(skill?.name || "").trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    names.push(name);
    if (names.length >= limit) break;
  }
  return names;
}

function workbenchCandidateInitial(item) {
  const name = String(item?.name || "").trim();
  return name ? Array.from(name)[0] : "候";
}

function workbenchMetaText(item) {
  const parts = [];
  if (item?.workYears != null) parts.push(`${item.workYears} 年经验`);
  if (item?.currentResidence) parts.push(String(item.currentResidence));
  const skills = workbenchSkillNames(item);
  if (skills.length) parts.push(skills.join(" / "));
  return parts.join(" · ");
}

function hardExpectedValues(hard) {
  return hard?.expectedValues ?? hard?.expected_values ?? hard?.values ?? hard?.value;
}

function hardActualValues(hard) {
  if (hard?.actualValues !== undefined) return hard.actualValues;
  if (hard?.actual_values !== undefined) return hard.actual_values;
  if (hard?.actual !== undefined) return hard.actual;
  return null;
}

function hasHardValue(values) {
  const rows = Array.isArray(values) ? values : values === null || values === undefined ? [] : [values];
  return rows.some((value) => value !== null && value !== undefined && String(value).trim() !== "");
}

function hardConditionState(hard) {
  if (["satisfied", "violated", "unverified"].includes(hard?.status)) return hard.status;
  if (hard?.matched === true) return "satisfied";
  if (hard?.matched === false && hasHardValue(hardActualValues(hard))) return "violated";
  return "unverified";
}

function summarizeHardConditions(hardMatches) {
  const counts = { satisfied: 0, violated: 0, unverified: 0 };
  for (const hard of Array.isArray(hardMatches) ? hardMatches : []) {
    if (hard?.required === false) continue;
    const state = hardConditionState(hard);
    counts[state] += 1;
  }
  const applicable = counts.satisfied + counts.violated + counts.unverified;
  const status = counts.violated
    ? "violated"
    : counts.unverified
      ? "unverified"
      : applicable
        ? "satisfied"
        : "not_applicable";
  return { ...counts, applicable, status };
}

function candidateHardConditionState(explanation, hardMatches) {
  const summary = summarizeHardConditions(hardMatches);
  if (summary.applicable) return summary.status;
  const explicit = String(explanation?.hardConditionStatus || "");
  return ["satisfied", "violated", "unverified", "not_applicable"].includes(explicit)
    ? explicit
    : summary.status;
}

function workbenchMatchData(item) {
  const explanation =
    item?.matchExplanation && typeof item.matchExplanation === "object"
      ? item.matchExplanation
      : {};
  const matchedAbilities = Array.isArray(explanation.matchedAbilities)
    ? explanation.matchedAbilities.filter(Boolean)
    : [];
  const missingAbilities = Array.isArray(explanation.missingAbilities)
    ? explanation.missingAbilities.filter(Boolean)
    : [];
  const hardMatches = Array.isArray(explanation.hardMatches)
    ? explanation.hardMatches.filter(Boolean)
    : [];
  const pathRationale = Array.isArray(explanation.pathRationale)
    ? explanation.pathRationale.filter(Boolean)
    : [];
  const hardSummary = summarizeHardConditions(hardMatches);
  return {
    matchedAbilities,
    missingAbilities,
    hardMatches,
    pathRationale,
    hardPassed: hardSummary.satisfied,
    hardViolated: hardSummary.violated,
    hardUnverified: hardSummary.unverified,
    hardApplicable: hardSummary.applicable,
    hardStatus: candidateHardConditionState(explanation, hardMatches),
    abilityTotal: matchedAbilities.length + missingAbilities.length,
  };
}

function formatWorkbenchValues(values) {
  if (Array.isArray(values)) {
    const joined = values.map((value) => String(value ?? "").trim()).filter(Boolean).join(" / ");
    return joined || "—";
  }
  if (values === null || values === undefined || values === "") return "—";
  return String(values);
}

function formatWorkbenchHard(hard) {
  const field = CONDITION_LABEL[hard?.field] || hard?.field || hard?.text || "条件";
  const expected = formatWorkbenchValues(hardExpectedValues(hard));
  const actual = formatWorkbenchValues(hardActualValues(hard));
  const state = hardConditionState(hard);
  const actualText = actual === "—" ? "实际值未提供" : `当前 ${actual}`;
  return `${field}：期望 ${expected} · ${actualText} · ${HARD_CONDITION_LABELS[state]}`;
}

function formatWorkbenchScore(item) {
  if (item?.score === null || item?.score === undefined || item?.score === "") return "";
  const score = Number(item.score);
  return Number.isFinite(score) ? score.toFixed(2) : "";
}

function formatWorkbenchPercent(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return `${Math.round(numeric * 100)}%`;
}

function computeWorkbenchPageCoverage(entries) {
  let hardPassed = 0;
  let hardTotal = 0;
  let matched = 0;
  let abilityTotal = 0;
  for (const entry of entries) {
    const data = workbenchMatchData(entry.item);
    hardPassed += data.hardPassed;
    hardTotal += data.hardMatches.length;
    matched += data.matchedAbilities.length;
    abilityTotal += data.abilityTotal;
  }
  return {
    hardRate: hardTotal ? hardPassed / hardTotal : null,
    abilityCoverage: abilityTotal ? matched / abilityTotal : null,
  };
}

function workbenchEvidenceLines(item) {
  const summary = String(item?.summary || "").replace(/\s+/g, " ").trim();
  const sentences = summary
    .split(/[。；;]+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  if (sentences.length) return sentences.slice(0, 2);

  const works = Array.isArray(item?.workExperiences) ? item.workExperiences : [];
  return works
    .slice(0, 2)
    .map((work) => {
      const role = work?.job_title || work?.jobTitle || work?.position || work?.title || "";
      const company = work?.company_name || work?.companyName || work?.company || "";
      return [role, company].map((part) => String(part || "").trim()).filter(Boolean).join(" · ");
    })
    .filter(Boolean);
}

function renderWorkbenchEvidence(item) {
  const list = el("ul", "aw-evidence");
  const lines = workbenchEvidenceLines(item);
  if (!lines.length) {
    list.append(el("li", "is-empty", "暂无履历摘要，打开完整档案查看工作经历。"));
    return list;
  }
  for (const line of lines) {
    const itemNode = el("li");
    const labelled = line.match(/^([^：:]{2,28})[：:]\s*(.+)$/);
    if (labelled) {
      itemNode.append(el("strong", "", `${labelled[1]}：`), document.createTextNode(labelled[2]));
    } else {
      itemNode.textContent = line;
    }
    list.append(itemNode);
  }
  return list;
}

function workbenchStatusPill(item, data) {
  if (data.hardApplicable > 0) {
    if (data.hardStatus === "violated") {
      return {
        text: data.hardUnverified > 0
          ? `硬条件违规 ${data.hardViolated} · 待核实 ${data.hardUnverified}`
          : `硬条件违规 ${data.hardViolated}`,
        tone: "is-warning",
      };
    }
    if (data.hardStatus === "unverified") {
      return { text: `硬条件待核实 ${data.hardUnverified}`, tone: "is-warning" };
    }
    return { text: "硬条件满足", tone: "is-good" };
  }
  if (data.matchedAbilities.length) {
    return { text: `${data.matchedAbilities.length} 项能力命中`, tone: "is-neutral" };
  }
  return { text: "待核验", tone: "is-neutral" };
}

function openExecutionConsole() {
  viewConsoleBtn?.click();
}

function buildWorkbenchTaskHead(wb, payload, run, overview) {
  const metrics = payload.debug?.agentMetrics || {};
  const executionRounds = resolveExecutionRounds(payload);
  const reflectionIterations = resolveReflectionIterations(payload);
  const elapsedMs = Number(metrics.meta?.elapsedMs || 0);
  const route = payload.debug?.difficultyRoute || run?.route || "";
  const kicker = el("div", "aw-task-kicker");
  kicker.append(el("span", "aw-status-dot"));
  const pace = route === "direct_search"
    ? "直连检索"
    : `执行 ${executionRounds} 轮 · 策略调整 ${reflectionIterations} 次`;
  const duration = elapsedMs > 0
    ? ` · ${elapsedMs < 10000 ? `${(elapsedMs / 1000).toFixed(1)} 秒` : `${Math.round(elapsedMs / 1000)} 秒`}`
    : "";
  kicker.append(document.createTextNode(`检索完成 · ${pace}${duration}`));

  const taskTop = el("div", "aw-task-top");
  const titleBlock = el("div", "aw-task-title");
  const title = String(run?.jobTitle || wb.jobTitle || "").trim() || "本轮候选人结果";
  titleBlock.append(el("h1", "", title));
  if (wb.query) titleBlock.append(el("p", "", wb.query));

  const actions = el("div", "aw-task-actions");
  const runButton = el("button", "aw-task-action");
  runButton.type = "button";
  runButton.append(
    document.createTextNode("查看运行记录"),
    el("span", "aw-task-action-icon", "↗"),
  );
  runButton.addEventListener("click", openExecutionConsole);
  actions.append(runButton);
  taskTop.append(titleBlock, actions);

  const summary = el("div", "aw-summary-strip");
  const addMetric = (value, label) => {
    const metric = el("span", "aw-metric");
    metric.append(el("strong", "", value), el("span", "", label));
    summary.append(metric);
  };
  addMetric(overview.delivered, "交付候选人");
  if (overview.target > 0) addMetric(overview.target, "目标人数");
  if (overview.gap > 0) {
    const gap = el("span", "aw-gap-pill");
    gap.append(el("i"), document.createTextNode(`缺口 ${overview.gap} 人`));
    summary.append(gap);
  }

  const side = el("div", "aw-summary-side");
  const hardRate = metrics.match?.hardMatchHitRate;
  if (overview.hasHardGap) {
    side.append(el("span", "is-warning", `硬条件${hardGapBreakdown(overview)}`));
  } else if (overview.hardStatus === "clear") {
    side.append(el("span", "is-good", "硬条件均满足"));
  } else if (Number.isFinite(Number(hardRate))) {
    side.append(el("span", "", `硬条件覆盖 ${formatWorkbenchPercent(hardRate)}`));
  }
  const judged = Number(metrics.recall?.judged || 0);
  if (judged > 0) side.append(el("span", "", `${judged} 人通过判定`));
  if (side.childElementCount) summary.append(side);

  const head = el("header", "aw-task-head");
  head.append(kicker, taskTop, summary);
  return head;
}

function buildWorkbenchRunPanel(wb, payload, run, overview) {
  const metrics = payload.debug?.agentMetrics || {};
  const hasMetrics = Boolean(payload.debug?.agentMetrics);
  const pageCoverage = computeWorkbenchPageCoverage(wb.pageEntries);
  const executionRounds = resolveExecutionRounds(payload);
  const reflectionIterations = resolveReflectionIterations(payload);
  const judged = Number(metrics.recall?.judged || 0);
  const hardRate = hasMetrics ? metrics.match?.hardMatchHitRate : pageCoverage.hardRate;
  const abilityCoverage = hasMetrics ? metrics.match?.abilityCoverage : pageCoverage.abilityCoverage;
  const stageCount = Number(
    payload.debug?.executedStages?.length || state.runEvents.length || run?.visitedStages?.size || 0,
  );

  const panel = el("aside", "aw-run-panel");
  panel.setAttribute("aria-label", "本次运行摘要");

  const head = el("div", "aw-run-head");
  const heading = el("div");
  heading.append(el("span", "aw-rail-kicker", "本次运行"), el("h2", "", "结果摘要"));
  const status = el("span", "aw-run-status");
  status.append(el("i"), document.createTextNode("已完成"));
  head.append(heading, status);

  const body = el("div", "aw-run-body");
  const hitTarget = overview.target > 0 && overview.delivered >= overview.target;
  const gapTitle = overview.target <= 0
    ? "本轮检索已完成"
    : hitTarget
      ? overview.hasHardGap
        ? `目标人数已达成，${overview.hardAffected} 人需复核`
        : "目标交付已达成"
      : `距目标还差 ${overview.gap} 位`;
  const h3 = el("h3", "", gapTitle);
  if (!hitTarget && overview.gap > 0) h3.append(el("span", "", ""));
  body.append(h3);
  body.append(
    el(
      "p",
      "",
      hitTarget || overview.target <= 0
        ? overview.hasHardGap
          ? `已交付 ${overview.delivered} 位候选人，其中硬条件${hardGapBreakdown(overview)}，请优先复核。`
          : `已交付 ${overview.delivered} 位候选人，执行 ${executionRounds} 轮检索，策略调整 ${reflectionIterations} 次。可以继续追问，或调整条件发起下一轮。`
        : `已交付 ${overview.delivered} 位候选人，执行 ${executionRounds} 轮检索，策略调整 ${reflectionIterations} 次。当前仍有数量缺口，可补充或放宽条件后再次检索。`,
    ),
  );
  const progress = el("div", "aw-progress");
  progress.setAttribute("role", "progressbar");
  progress.setAttribute("aria-label", "目标交付完成度");
  progress.setAttribute("aria-valuemin", "0");
  progress.setAttribute("aria-valuemax", "100");
  const percent = overview.target > 0
    ? Math.min(100, Math.round((overview.delivered / overview.target) * 100))
    : 100;
  progress.setAttribute("aria-valuenow", String(percent));
  const progressFill = el("i");
  progressFill.style.width = `${percent}%`;
  progress.append(progressFill);
  const progressMeta = el("div", "aw-progress-meta");
  progressMeta.append(
    el("span", "", overview.target > 0 ? `${overview.delivered} / ${overview.target} 人` : `${overview.delivered} 人交付`),
    el("span", "", `${percent}%`),
  );
  body.append(progress, progressMeta);

  if (overview.gap > 0) {
    const nextStep = el("div", "aw-next-step");
    nextStep.append(
      el("span", "aw-next-step-label", "下一步建议"),
      el("strong", "", "补充或放宽城市、经验与技能条件，再发起一轮检索。"),
    );
    const adjust = el("button", "", "调整条件再搜 →");
    adjust.type = "button";
    adjust.addEventListener("click", () => {
      setComposerNote("可以补充或放宽条件后再次检索。");
      chatQueryInput.focus();
    });
    nextStep.append(adjust);
    body.append(nextStep);
  }
  panel.append(head, body);

  const coverage = el("section", "aw-run-section");
  coverage.append(el("div", "aw-rail-label", "覆盖情况"));
  const coverageList = el("div", "aw-coverage");
  const addCoverage = (label, value, tone = "is-good") => {
    const row = el("div", "aw-coverage-row");
    row.append(
      el("span", `aw-coverage-icon ${tone}`.trim(), tone === "is-warning" ? "!" : "✓"),
      el("span", "", label),
      el("strong", "", value),
    );
    coverageList.append(row);
  };
  const rateTone = (value) => {
    if (value === null || value === undefined) return "is-neutral";
    return Number(value) >= 0.65 ? "is-good" : "is-warning";
  };
  if (overview.hasHardGap) {
    addCoverage("硬条件缺口", hardGapBreakdown(overview), "is-warning");
  } else if (overview.hardStatus === "clear") {
    addCoverage("硬条件", "交付名单均满足", "is-good");
  } else {
    addCoverage(
      hasMetrics ? "硬条件覆盖" : "本页硬条件",
      formatWorkbenchPercent(hardRate),
      rateTone(hardRate),
    );
  }
  addCoverage(
    hasMetrics ? "能力覆盖" : "本页能力覆盖",
    formatWorkbenchPercent(abilityCoverage),
    rateTone(abilityCoverage),
  );
  addCoverage(
    hasMetrics ? "通过判定" : "本页候选人",
    hasMetrics ? (judged > 0 ? `${judged} 人` : "—") : `${wb.pageEntries.length} 人`,
    "is-good",
  );
  coverage.append(coverageList);
  panel.append(coverage);

  const ledger = el("section", "aw-run-section");
  ledger.append(el("div", "aw-rail-label", "运行账本"));
  const ledgerGrid = el("div", "aw-ledger");
  const addLedger = (value, label) => {
    const item = el("div");
    item.append(el("strong", "", value), el("span", "", label));
    ledgerGrid.append(item);
  };
  addLedger(String(executionRounds), "执行轮次");
  addLedger(String(reflectionIterations), "反思迭代");
  addLedger(stageCount > 0 ? String(stageCount) : "—", "执行阶段");
  addLedger(judged > 0 ? String(judged) : "—", "通过判定");
  ledger.append(ledgerGrid);
  panel.append(ledger);

  const decision = el("details", "aw-decision");
  const decisionSummary = el("summary", "aw-decision-summary");
  decisionSummary.append(
    el("span", "", "为什么在这里结束"),
    el("small", "", "展开真实结束依据"),
  );
  const decisionBody = el("div", "aw-decision-body");
  appendChatReasoning(decisionBody, payload);
  if (!decisionBody.querySelector(".chat-reasoning")) {
    decisionBody.append(
      el("p", "", overview.reason || "本轮已完成检索并收敛现有结果。"),
    );
  }
  decision.append(decisionSummary, decisionBody);
  panel.append(decision);

  const sort = el("details", "aw-sort");
  const sortSummary = el("summary", "aw-sort-summary");
  sortSummary.append(
    el("span", "", "排序依据"),
    el("small", "", "硬条件 › 能力 › 证据"),
  );
  sort.append(
    sortSummary,
    el("p", "", "综合顺位优先依据硬条件、能力覆盖与路径证据；相关度仅作同层参考，不代表通过率或匹配率。"),
  );
  panel.append(sort);

  const audit = el("button", "aw-audit-link");
  audit.type = "button";
  const auditMeta = [
    stageCount > 0 ? `${stageCount} 阶段` : "",
    judged > 0 ? `${judged} 人通过判定` : "",
    wb.total > 0 ? `${wb.total} 人交付` : "",
  ].filter(Boolean).join(" · ");
  audit.append(el("span", "", "查看完整运行记录"), el("small", "", auditMeta || "执行观测 ↗"));
  audit.addEventListener("click", openExecutionConsole);
  panel.append(audit);
  return panel;
}

function compareText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function compareCandidateName(entry) {
  return compareText(entry?.item?.name || entry?.item?.docId) || "该候选人";
}

function describeCompareHard(hard, index) {
  const field = compareText(hard?.field || hard?.text || hard?.label) || `condition-${index + 1}`;
  const status = hardConditionState(hard);
  return {
    field,
    label: compareText(hard?.fieldLabel || CONDITION_LABEL[hard?.field] || hard?.text || hard?.label || field) || "条件",
    operator: compareText(hard?.operator) || "eq",
    expected: formatWorkbenchValues(hardExpectedValues(hard)),
    actual: formatWorkbenchValues(hardActualValues(hard)),
    status,
    matched: status === "satisfied",
  };
}

function compareHardSignature(hard) {
  return `${hard.field}|${hard.operator}|${hard.expected}`;
}

function compareAbilityKey(value) {
  return compareText(value).toLocaleLowerCase("zh-CN");
}

function buildCompareCandidate(entry, index) {
  const data = workbenchMatchData(entry.item);
  const hardRows = [];
  const hardMap = new Map();
  data.hardMatches.forEach((hard, hardIndex) => {
    const row = describeCompareHard(hard, hardIndex);
    const baseKey = compareHardSignature(row);
    let key = baseKey;
    let duplicate = 2;
    while (hardMap.has(key)) {
      key = `${baseKey}#${duplicate}`;
      duplicate += 1;
    }
    hardMap.set(key, { ...row, key });
    hardRows.push({ ...row, key });
  });

  const abilities = new Map();
  data.matchedAbilities.forEach((ability) => {
    abilities.set(compareAbilityKey(ability), { label: compareText(ability), state: "matched" });
  });
  data.missingAbilities.forEach((ability) => {
    const key = compareAbilityKey(ability);
    if (!abilities.has(key)) abilities.set(key, { label: compareText(ability), state: "missing" });
  });

  const score = Number(entry.item?.score);
  const evidenceLines = workbenchEvidenceLines(entry.item);
  return {
    entry,
    index,
    item: entry.item,
    name: compareCandidateName(entry),
    data,
    hardRows,
    hardMap,
    abilities,
    score: Number.isFinite(score) ? score : null,
    evidenceLines,
    evidenceKey: evidenceLines.map(compareText).join(" | "),
    pathKey: data.pathRationale.map(compareText).join(" | "),
  };
}

function compareCandidateMetrics(candidate) {
  const hardTotal = candidate.data.hardMatches.length;
  const hardStatusRank = candidate.data.hardStatus === "satisfied"
    ? 2
    : candidate.data.hardStatus === "violated"
      ? 0
      : candidate.data.hardStatus === "unverified"
        ? 1
        : 2;
  return {
    hardTotal,
    hardPassed: candidate.data.hardPassed,
    hardViolated: candidate.data.hardViolated,
    hardUnverified: candidate.data.hardUnverified,
    hardStatus: candidate.data.hardStatus,
    hardStatusRank,
    hardRate: hardTotal ? candidate.data.hardPassed / hardTotal : null,
    matched: candidate.data.matchedAbilities.length,
    missing: candidate.data.missingAbilities.length,
    score: candidate.score,
  };
}

function compareCoreEqual(left, right, hardAligned = true) {
  const hardEqual = !hardAligned || (
    left.metrics.hardStatus === right.metrics.hardStatus &&
    left.metrics.hardRate === right.metrics.hardRate &&
    left.metrics.hardPassed === right.metrics.hardPassed
  );
  return (
    hardEqual &&
    left.metrics.matched === right.metrics.matched &&
    left.metrics.missing === right.metrics.missing
  );
}

function compareCandidatesByEvidence(left, right, hardAligned = true) {
  if (hardAligned) {
    if (left.metrics.hardStatusRank !== right.metrics.hardStatusRank) {
      return right.metrics.hardStatusRank - left.metrics.hardStatusRank;
    }
    const leftRate = left.metrics.hardRate ?? -1;
    const rightRate = right.metrics.hardRate ?? -1;
    if (leftRate !== rightRate) return rightRate - leftRate;
    if (left.metrics.hardPassed !== right.metrics.hardPassed) {
      return right.metrics.hardPassed - left.metrics.hardPassed;
    }
  }
  if (left.metrics.matched !== right.metrics.matched) {
    return right.metrics.matched - left.metrics.matched;
  }
  if (left.metrics.missing !== right.metrics.missing) {
    return left.metrics.missing - right.metrics.missing;
  }
  const leftScore = left.metrics.score ?? -Infinity;
  const rightScore = right.metrics.score ?? -Infinity;
  if (leftScore !== rightScore) return rightScore - leftScore;
  return left.index - right.index;
}

function compareLeaderReason(leader, candidates, hardAligned = true) {
  const metrics = leader.metrics;
  const others = candidates.filter((candidate) => candidate !== leader).map((candidate) => candidate.metrics);
  if (hardAligned) {
    if (
      metrics.hardStatus === "satisfied" &&
      others.every((other) => other.hardStatus !== "satisfied")
    ) {
      return `可核验硬条件全部满足（${metrics.hardPassed}/${metrics.hardTotal}）`;
    }
    if (
      metrics.hardTotal > 0 &&
      metrics.hardRate === 1 &&
      others.every((other) => other.hardRate === null || other.hardRate < 1)
    ) {
      return `可核验硬条件全部满足（${metrics.hardPassed}/${metrics.hardTotal}）`;
    }
    if (
      metrics.hardRate !== null &&
      others.every((other) => (other.hardRate ?? -1) < metrics.hardRate)
    ) {
      return `硬条件满足数领先（${metrics.hardPassed}/${metrics.hardTotal}）`;
    }
  }
  if (metrics.matched > 0 && others.every((other) => other.matched < metrics.matched)) {
    return `能力命中最多（${metrics.matched} 项）`;
  }
  if (others.every((other) => other.missing > metrics.missing)) {
    return `能力缺口更少（${metrics.missing} 项）`;
  }
  if (
    metrics.score !== null &&
    others.every((other) => other.score === null || other.score < metrics.score)
  ) {
    return `本轮相关度最高（${metrics.score.toFixed(2)}）`;
  }
  return "本轮证据相对更完整";
}

function selectCompareLeader(model) {
  const ranked = [...model.candidates].sort(
    (left, right) => compareCandidatesByEvidence(left, right, model.hardConditionsAligned),
  );
  const top = ranked[0];
  const tied = ranked.filter((candidate) =>
    compareCoreEqual(candidate, top, model.hardConditionsAligned));
  if (tied.length === 1) {
    return {
      kind: "leader",
      candidate: top,
      reason: compareLeaderReason(top, model.candidates, model.hardConditionsAligned),
    };
  }

  const scored = tied.filter((candidate) => candidate.metrics.score !== null);
  if (scored.length) {
    const topScore = Math.max(...scored.map((candidate) => candidate.metrics.score));
    const scoreLeaders = scored.filter((candidate) => candidate.metrics.score === topScore);
    if (scoreLeaders.length === 1) {
      return {
        kind: "leader",
        candidate: scoreLeaders[0],
        reason: `本轮相关度最高（${topScore.toFixed(2)}）`,
      };
    }
  }

  const hasEvidence = tied.some((candidate) => {
    const metrics = candidate.metrics;
    return metrics.hardTotal > 0 || metrics.matched > 0 || metrics.missing > 0;
  });
  return { kind: hasEvidence ? "tie" : "limited", candidates: tied };
}

function buildCompareModel(entries) {
  const candidates = entries.map(buildCompareCandidate);
  candidates.forEach((candidate) => {
    candidate.metrics = compareCandidateMetrics(candidate);
  });

  const hardRows = [];
  const seenHardRows = new Set();
  for (const candidate of candidates) {
    for (const row of candidate.hardRows) {
      if (seenHardRows.has(row.key)) continue;
      seenHardRows.add(row.key);
      hardRows.push({
        key: row.key,
        label: row.label,
        expected: row.expected,
        cells: candidates.map((item) => item.hardMap.get(row.key) || null),
      });
    }
  }
  for (const row of hardRows) {
    row.differs = new Set(
      row.cells.map((cell) => (cell ? `${cell.status}|${cell.actual}|${cell.expected}` : "missing")),
    ).size > 1;
    row.allMatched = row.cells.every((cell) => cell?.status === "satisfied");
    row.allMissed = row.cells.every((cell) => cell?.status === "violated");
  }

  const abilityRows = [];
  const seenAbilities = new Set();
  for (const candidate of candidates) {
    for (const [key, ability] of candidate.abilities) {
      if (seenAbilities.has(key)) continue;
      seenAbilities.add(key);
      abilityRows.push({
        key,
        label: ability.label,
        cells: candidates.map((item) => item.abilities.get(key) || null),
      });
    }
  }
  for (const row of abilityRows) {
    row.differs = new Set(row.cells.map((cell) => cell?.state || "unknown")).size > 1;
    row.allMatched = row.cells.every((cell) => cell?.state === "matched");
    row.allMissing = row.cells.every((cell) => cell?.state === "missing");
  }

  const model = {
    candidates,
    hardRows,
    abilityRows,
    differingHardRows: hardRows.filter((row) => row.differs),
    differingAbilityRows: abilityRows.filter((row) => row.differs),
    sharedAbilities: abilityRows.filter((row) => row.allMatched),
    sharedGaps: abilityRows.filter((row) => row.allMissing),
    hardCoverage: candidates.filter((candidate) => candidate.metrics.hardTotal > 0).length,
    hardFullyPassed: candidates.filter((candidate) => {
      const total = candidate.metrics.hardTotal;
      return total > 0 && candidate.metrics.hardStatus === "satisfied";
    }).length,
    hardNeedsReview: candidates.filter((candidate) =>
      ["violated", "unverified"].includes(candidate.metrics.hardStatus)).length,
    hardConditionsAligned: new Set(
      candidates.map((candidate) => candidate.hardRows.map((row) => row.key).sort().join("|")),
    ).size === 1,
    evidenceDiffers: new Set(candidates.map((candidate) => candidate.evidenceKey)).size > 1,
    pathDiffers: new Set(candidates.map((candidate) => candidate.pathKey)).size > 1,
  };
  model.differingCount = model.differingHardRows.length + model.differingAbilityRows.length;
  model.leader = selectCompareLeader(model);
  return model;
}

function compareCandidateEvidenceCopy(candidate) {
  const metrics = candidate.metrics;
  const parts = [];
  if (metrics.hardTotal) {
    const hardCopy = metrics.hardViolated
      ? `硬条件违规 ${metrics.hardViolated}${metrics.hardUnverified ? `、待核实 ${metrics.hardUnverified}` : ""}`
      : metrics.hardUnverified
        ? `硬条件待核实 ${metrics.hardUnverified}`
        : "硬条件满足";
    parts.push(hardCopy);
  }
  if (metrics.matched || metrics.missing) parts.push(`能力命中 ${metrics.matched} 项`);
  if (metrics.missing) parts.push(`缺口 ${metrics.missing} 项`);
  if (candidate.score !== null) parts.push(`相关度 ${candidate.score.toFixed(2)}`);
  return parts.join("，") || "本轮可比较的信号有限";
}

function renderCompareOverview(model) {
  if (!compareOverview) return;
  const verdict = el("article", "compare-verdict");
  const leader = model.leader;
  const titleLine = el("div", "compare-verdict-title");
  let badge = "证据提示";
  let title = "本轮对比摘要";
  let copy = "对比内容全部来自本轮检索结果。";
  let note = model.hardConditionsAligned
    ? "建议只用于确定查看顺序，不替代面试与人工判断。"
    : "候选人硬条件覆盖范围不同；建议只用于确定查看顺序，不替代面试与人工判断。";

  if (leader.kind === "leader") {
    verdict.classList.add("is-suggested");
    badge = "优先查看";
    title = `建议优先查看 ${leader.candidate.name}`;
    copy = `${leader.reason}；${compareCandidateEvidenceCopy(leader.candidate)}。`;
  } else if (leader.kind === "tie") {
    verdict.classList.add("is-tie");
    badge = "证据接近";
    const names = leader.candidates.map((candidate) => candidate.name).join("、");
    title = `${names} 证据接近`;
    copy = "本轮硬条件与能力覆盖没有拉开明显差距，建议结合履历与面试判断。";
  } else {
    verdict.classList.add("is-limited");
    badge = "人工判断";
    title = "本轮证据不足以形成优先建议";
    copy = "缺少可对齐的硬条件或能力判定，相关度只能作为查看顺序的参考。";
  }

  titleLine.append(el("span", "compare-verdict-badge", badge), el("h3", "", title));
  verdict.append(
    el("span", "compare-verdict-kicker", "证据提示"),
    titleLine,
    el("p", "compare-verdict-copy", copy),
    el("small", "compare-verdict-note", note),
  );

  const snapshot = el("div", "compare-snapshot");
  const addSnapshot = (label, value, detail, tone = "") => {
    const item = el("div", `compare-snapshot-item ${tone}`.trim());
    item.append(
      el("span", "compare-snapshot-label", label),
      el("strong", "", value),
      el("small", "", detail),
    );
    snapshot.append(item);
  };
  addSnapshot(
    "关键差异",
    `${model.differingCount} 项`,
    `硬条件 ${model.differingHardRows.length} · 能力 ${model.differingAbilityRows.length}`,
  );
  addSnapshot(
    "共同能力",
    `${model.sharedAbilities.length} 项`,
    model.sharedGaps.length ? `共同缺口 ${model.sharedGaps.length} 项` : "未发现共同缺口",
    model.sharedAbilities.length ? "is-good" : "",
  );
  addSnapshot(
    "各自硬条件",
    model.hardCoverage ? `${model.hardFullyPassed}/${model.hardCoverage}` : "—",
    model.hardRows.length
      ? model.hardCoverage < model.candidates.length
        ? `${model.candidates.length - model.hardCoverage} 人缺少硬条件证据`
        : model.hardNeedsReview
          ? `${model.hardNeedsReview} 人有违规或待核实项`
          : "按各自可核验条件统计"
      : "本轮没有可核验硬条件",
    model.hardNeedsReview ? "is-warning" : "is-good",
  );
  compareOverview.replaceChildren(verdict, snapshot);
}

function renderCompareMatrix(model) {
  if (!compareMatrix) return;
  const hardRows = compareViewMode === COMPARE_VIEW_ALL ? model.hardRows : model.differingHardRows;
  const abilityRows = compareViewMode === COMPARE_VIEW_ALL ? model.abilityRows : model.differingAbilityRows;
  const rowCount = hardRows.length + abilityRows.length;

  const header = el("div", "compare-section-head");
  const heading = el("div");
  heading.append(
    el("span", "compare-section-kicker", "关键条件"),
    el("h3", "", "条件矩阵"),
  );
  header.append(
    heading,
    el(
      "span",
      "compare-matrix-count",
      compareViewMode === COMPARE_VIEW_ALL ? `全部 ${rowCount} 项` : `${rowCount} 项差异`,
    ),
  );

  if (!rowCount) {
    const hasComparisonRows = model.hardRows.length + model.abilityRows.length > 0;
    const empty = el("div", "compare-matrix-empty");
    empty.append(
      el("strong", "", hasComparisonRows ? "本轮已核验条件一致" : "本轮没有可对比的条件证据"),
      el(
        "p",
        "",
        hasComparisonRows
          ? "切换到「全部信息」可以查看共同硬条件与能力证据。"
          : "候选人结果中没有返回可核验的硬条件或能力判定。",
      ),
    );
    compareMatrix.replaceChildren(header, empty);
    compareMatrix.hidden = false;
    return;
  }

  const scroll = el("div", "compare-matrix-scroll");
  const table = el("table", "compare-matrix-table");
  table.append(el("caption", "sr-only", "候选人的关键条件与能力覆盖矩阵"));
  const thead = el("thead");
  const headRow = el("tr");
  const corner = el("th", "", "对比项");
  corner.setAttribute("scope", "col");
  headRow.append(corner);
  for (const candidate of model.candidates) {
    const candidateHead = el("th");
    candidateHead.setAttribute("scope", "col");
    const scoreText = formatWorkbenchScore(candidate.item);
    candidateHead.append(el("span", "", candidate.name));
    if (scoreText) candidateHead.append(el("small", "", `相关度 ${scoreText}`));
    headRow.append(candidateHead);
  }
  thead.append(headRow);
  table.append(thead);

  const tbody = el("tbody");
  const appendGroup = (label, count) => {
    const row = el("tr", "compare-matrix-group");
    const cell = el("th");
    cell.setAttribute("scope", "rowgroup");
    cell.colSpan = model.candidates.length + 1;
    cell.append(el("span", "", label), el("small", "", `${count} 项`));
    row.append(cell);
    tbody.append(row);
  };

  if (hardRows.length) {
    appendGroup("硬条件", hardRows.length);
    for (const row of hardRows) {
      const tableRow = el("tr", `compare-matrix-row ${row.differs ? "is-different" : ""}`.trim());
      const label = el("th", "compare-matrix-label");
      label.setAttribute("scope", "row");
      label.append(el("span", "compare-row-title", row.label));
      if (row.expected && row.expected !== "—") {
        label.append(el("small", "compare-row-detail", `要求 ${row.expected}`));
      }
      if (row.differs) label.append(el("span", "compare-diff-mark", "差异"));
      tableRow.append(label);
      for (const cell of row.cells) {
        const value = el("td", "compare-matrix-cell");
        if (!cell) {
          value.append(el("span", "compare-status is-unverified", "? 待核实"));
        } else {
          const state = cell.status || (cell.matched ? "satisfied" : "unverified");
          const actual = cell.actual && cell.actual !== "—" ? cell.actual : "";
          const statusLabel = HARD_CONDITION_LABELS[state] || "待核实";
          const tone = state === "satisfied" ? "is-met" : state === "violated" ? "is-miss" : "is-unverified";
          const glyph = state === "satisfied" ? "✓" : state === "violated" ? "×" : "?";
          value.append(
            el(
              "span",
              `compare-status ${tone}`.trim(),
              [glyph, actual, statusLabel].filter(Boolean).join(" "),
            ),
          );
        }
        tableRow.append(value);
      }
      tbody.append(tableRow);
    }
  }

  if (abilityRows.length) {
    appendGroup("能力覆盖", abilityRows.length);
    for (const row of abilityRows) {
      const tableRow = el("tr", `compare-matrix-row ${row.differs ? "is-different" : ""}`.trim());
      const label = el("th", "compare-matrix-label");
      label.setAttribute("scope", "row");
      label.append(el("span", "compare-row-title", row.label));
      if (row.differs) label.append(el("span", "compare-diff-mark", "差异"));
      tableRow.append(label);
      for (const cell of row.cells) {
        const value = el("td", "compare-matrix-cell");
        if (!cell) {
          value.append(el("span", "compare-status is-na", "· 未提及"));
        } else if (cell.state === "matched") {
          value.append(el("span", "compare-status is-met", "✓ 命中"));
        } else if (cell.state === "missing") {
          value.append(el("span", "compare-status is-miss", "× 缺口"));
        } else {
          value.append(el("span", "compare-status is-na", "· 未提及"));
        }
        tableRow.append(value);
      }
      tbody.append(tableRow);
    }
  }

  table.append(tbody);
  scroll.append(table);
  const nodes = [header];
  if (!model.hardConditionsAligned) {
    nodes.push(
      el(
        "p",
        "compare-matrix-note",
        "候选人可核验的硬条件范围不同；“待核实”表示本轮没有该项证据，不代表违规。",
      ),
    );
  }
  nodes.push(scroll);
  compareMatrix.replaceChildren(...nodes);
  compareMatrix.hidden = false;
}

function compareSectionHeading(title, count = 0) {
  const heading = el("h4");
  heading.append(document.createTextNode(title));
  if (count > 0) heading.append(el("span", "aw-compare-section-count", `${count} 项不同`));
  return heading;
}

function renderCompareCandidate(candidate, model) {
  const data = candidate.data;
  const scoreText = formatWorkbenchScore(candidate.item);
  const isLeader = model.leader.kind === "leader" && model.leader.candidate === candidate;
  const card = el("article", `aw-compare-person ${isLeader ? "is-suggested" : ""}`.trim());
  card.dataset.candidateKey = candidate.entry.key;
  const head = el("div", "aw-compare-person-head");
  const avatar = el("span", "aw-avatar", workbenchCandidateInitial(candidate.item));
  avatar.setAttribute("aria-hidden", "true");
  const identity = el("div", "aw-compare-identity");
  identity.append(
    el("h3", "", candidate.name),
    el("p", "", workbenchMetaText(candidate.item)),
  );
  if (scoreText) identity.append(el("span", "aw-compare-score", `相关度 ${scoreText}`));
  head.append(avatar, identity);
  if (isLeader) {
    const badge = el("span", "aw-compare-leader-badge", "优先查看");
    badge.title = "按本轮硬条件与能力证据推荐的查看顺序";
    head.append(badge);
  }
  card.append(head);

  const stats = el("div", "aw-compare-stats");
  const addStat = (value, label, tone = "") => {
    const item = el("span", `aw-compare-stat ${tone}`.trim());
    item.append(el("strong", "", value), el("small", "", label));
    stats.append(item);
  };
  if (data.hardApplicable > 0) {
    addStat(
      data.hardViolated
        ? `违规 ${data.hardViolated}${data.hardUnverified ? ` · 待核实 ${data.hardUnverified}` : ""}`
        : data.hardUnverified
          ? `待核实 ${data.hardUnverified}`
          : "通过",
      "硬条件",
      data.hardStatus === "satisfied" ? "is-good" : "is-warning",
    );
  }
  if (data.abilityTotal) addStat(`${data.matchedAbilities.length}/${data.abilityTotal}`, "能力覆盖");
  if (data.missingAbilities.length) addStat(`${data.missingAbilities.length}`, "能力缺口", "is-warning");
  if (stats.childElementCount) card.append(stats);

  const visibleHardRows = model.hardRows.filter(
    (row) => compareViewMode === COMPARE_VIEW_ALL || row.differs,
  );
  const hardSection = el("section", "aw-compare-section");
  const hiddenHardCount = Math.max(0, data.hardMatches.length - visibleHardRows.length);
  if (visibleHardRows.length) {
    hardSection.classList.add("is-different");
    hardSection.append(compareSectionHeading("硬条件", model.differingHardRows.length));
    const list = el("ul", "aw-compare-list");
    for (const row of visibleHardRows) {
      const cell = candidate.hardMap.get(row.key);
      const state = cell?.status || (cell?.matched ? "satisfied" : "unverified");
      const actual = cell?.actual && cell.actual !== "—" ? cell.actual : "";
      const tone = state === "satisfied" ? "is-met" : state === "violated" ? "is-miss" : "is-unverified";
      const status = cell
        ? [actual, HARD_CONDITION_LABELS[state]].filter(Boolean).join(" · ")
        : "本轮待核实";
      const item = el("li", "aw-compare-hard-row");
      item.append(
        el("span", "aw-compare-hard-label", row.label),
        el(
          "span",
          `aw-compare-hard-value ${tone}`.trim(),
          status,
        ),
      );
      list.append(item);
    }
    hardSection.append(list);
  } else {
    hardSection.append(el("h4", "", "硬条件"));
    hardSection.append(
      el(
        "p",
        "aw-compare-empty",
        data.hardMatches.length
          ? hiddenHardCount
            ? `本轮硬条件组合与其他候选人一致，${hiddenHardCount} 项已收起。`
            : "本轮硬条件组合与其他候选人一致。"
          : "本轮没有可核验的硬条件。",
      ),
    );
  }
  if (visibleHardRows.length && compareViewMode === COMPARE_VIEW_DIFF && hiddenHardCount) {
    hardSection.append(el("p", "aw-compare-common-note", `另有 ${hiddenHardCount} 项硬条件一致，已收起。`));
  }
  card.append(hardSection);

  const visibleAbilityRows = model.abilityRows.filter(
    (row) => compareViewMode === COMPARE_VIEW_ALL || row.differs,
  );
  const hiddenAbilityCount = model.abilityRows.filter(
    (row) => !visibleAbilityRows.includes(row) && candidate.abilities.has(row.key),
  ).length;
  const abilitySection = el("section", "aw-compare-section");
  const matchedAbilities = [];
  const missingAbilities = [];
  const unknownAbilities = [];
  for (const row of visibleAbilityRows) {
    const state = candidate.abilities.get(row.key)?.state;
    if (state === "matched") matchedAbilities.push(row.label);
    else if (state === "missing") missingAbilities.push(row.label);
    else unknownAbilities.push(row.label);
  }
  const appendAbilityGroup = (label, abilities, tone) => {
    if (!abilities.length) return;
    const group = el("div", `aw-compare-ability-group ${tone}`.trim());
    const tags = el("div", "aw-compare-tags");
    for (const ability of abilities) tags.append(el("span", "aw-compare-tag", ability));
    group.append(el("span", "aw-compare-ability-label", label), tags);
    abilitySection.append(group);
  };
  if (matchedAbilities.length || missingAbilities.length || unknownAbilities.length) {
    abilitySection.classList.add("is-different");
    abilitySection.append(compareSectionHeading("能力证据", model.differingAbilityRows.length));
    appendAbilityGroup("命中", matchedAbilities, "is-match");
    appendAbilityGroup("缺口", missingAbilities, "is-missing");
    appendAbilityGroup("未提及", unknownAbilities, "is-neutral");
  } else {
    abilitySection.append(el("h4", "", "能力证据"));
    abilitySection.append(
      el(
        "p",
        "aw-compare-empty",
        data.abilityTotal
          ? hiddenAbilityCount
            ? `能力命中与缺口组合与其他候选人一致，${hiddenAbilityCount} 项共同能力已收起。`
            : "能力命中与缺口组合与其他候选人一致。"
          : "本轮没有能力判定明细。",
      ),
    );
  }
  if (
    visibleAbilityRows.length &&
    compareViewMode === COMPARE_VIEW_DIFF &&
    hiddenAbilityCount
  ) {
    abilitySection.append(el("p", "aw-compare-common-note", `另有 ${hiddenAbilityCount} 项共同能力信号已收起。`));
  }
  card.append(abilitySection);

  if (compareViewMode === COMPARE_VIEW_ALL || model.evidenceDiffers) {
    const evidenceSection = el("section", "aw-compare-section");
    evidenceSection.append(el("h4", "", "履历摘要"));
    const evidenceList = renderWorkbenchEvidence(candidate.item);
    evidenceList.classList.add("aw-compare-evidence");
    evidenceSection.append(evidenceList);
    card.append(evidenceSection);
  }

  if (compareViewMode === COMPARE_VIEW_ALL || model.pathDiffers) {
    const pathSection = el("section", "aw-compare-section");
    pathSection.append(el("h4", "", "来源路径"));
    if (data.pathRationale.length) {
      const list = el("ul", "aw-compare-list");
      for (const rationale of data.pathRationale) list.append(el("li", "", rationale));
      pathSection.append(list);
    } else {
      pathSection.append(el("p", "aw-compare-empty", "本轮没有路径说明。"));
    }
    card.append(pathSection);
  }
  return card;
}

function renderCompareCards(model) {
  if (!compareGrid) return;
  compareGrid.classList.toggle("is-all", compareViewMode === COMPARE_VIEW_ALL);
  compareGrid.replaceChildren(
    ...model.candidates.map((candidate) => renderCompareCandidate(candidate, model)),
  );
  compareGrid.style.setProperty("--compare-count", String(model.candidates.length));
}

function syncCompareViewButtons() {
  for (const button of compareViewButtons) {
    const active = button.dataset.compareView === compareViewMode;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

function updateCompareCopy(model) {
  if (!compareCopy) return;
  if (compareViewMode === COMPARE_VIEW_DIFF) {
    compareCopy.textContent = model.differingCount
      ? `已选择 ${model.candidates.length} 位候选人，正在聚焦 ${model.differingCount} 项差异，一致项已收起。`
      : `已选择 ${model.candidates.length} 位候选人，本轮已核验条件一致，当前展示全部信息。`;
    return;
  }
  compareCopy.textContent = `已选择 ${model.candidates.length} 位候选人，展示全部硬条件、能力证据与履历信息。`;
}

function renderCompareContent(model = compareModel) {
  if (!model) return;
  syncCompareViewButtons();
  renderCompareOverview(model);
  renderCompareMatrix(model);
  renderCompareCards(model);
  updateCompareCopy(model);
}

function setCompareView(view) {
  if (view !== COMPARE_VIEW_DIFF && view !== COMPARE_VIEW_ALL) return;
  compareViewMode = view;
  renderCompareContent();
}

let compareDialogTrigger = null;
let compareModel = null;
let compareViewMode = COMPARE_VIEW_DIFF;

function openWorkbenchComparison(wb) {
  hideCandidateTooltip();
  const entries = [...wb.compare.values()];
  if (entries.length < 2 || !compareDialog || !compareGrid) return;
  compareModel = buildCompareModel(entries);
  compareViewMode = compareModel.differingCount ? COMPARE_VIEW_DIFF : COMPARE_VIEW_ALL;
  renderCompareContent(compareModel);
  compareDialogTrigger = wb.compareButton || null;
  if (typeof compareDialog.showModal === "function") compareDialog.showModal();
  else compareDialog.setAttribute("open", "");
  announceAgentStatus(
    `已打开 ${entries.length} 位候选人的对比视图，识别到 ${compareModel.differingCount} 项关键差异。`,
  );
}

function setWorkbenchFeedback(wb, text, { warning = false } = {}) {
  if (!wb.feedback) return;
  wb.feedback.textContent = text;
  wb.feedback.classList.toggle("is-warning", warning);
  wb.feedback.classList.toggle("is-visible", Boolean(text));
}

function renderWorkbenchList(wb) {
  hideCandidateTooltip();
  const entries = wb.filter === "shortlisted"
    ? [...wb.shortlisted.values()]
    : wb.pageEntries;
  wb.list.replaceChildren();
  if (entries.length) {
    wb.list.append(...entries.map((entry) => renderWorkbenchCandidate(wb, entry)));
    wb.empty.hidden = true;
  } else {
    wb.empty.hidden = false;
    if (wb.filter === "shortlisted") {
      wb.emptyStrong.textContent = "还没有加入入围的候选人";
      wb.emptyCopy.textContent = "在候选人行点击「加入入围」，即可在这里集中查看。";
    } else {
      wb.emptyStrong.textContent = "本页没有候选人";
      wb.emptyCopy.textContent = "可以补充或放宽检索条件后重新运行。";
    }
  }
  wb.countBadge.textContent = String(
    wb.filter === "shortlisted" ? wb.shortlisted.size : wb.total,
  );
}

function syncWorkbenchPager(wb) {
  if (!wb.pager) return;
  if (wb.filter !== "all" || !wb.page) {
    wb.pager.hidden = true;
    return;
  }
  const totalPages = Math.max(1, Number(wb.page.totalPages || 1));
  const currentPage = Math.max(1, Number(wb.page.currentPage || 1));
  const total = Math.max(0, Number(wb.page.total || wb.total || 0));
  wb.pager.hidden = totalPages <= 1;
  if (totalPages <= 1) return;
  wb.pageState.textContent = `第 ${currentPage} / ${totalPages} 页 · 共 ${total} 人`;
  wb.prev.disabled = state.pageLoading || currentPage <= 1;
  wb.next.disabled = state.pageLoading || !wb.page.hasMore;
}

function updateWorkbenchChrome(wb) {
  wb.allTab.textContent = `全部 ${wb.total}`;
  wb.shortlistTab.textContent = `已入围 ${wb.shortlisted.size}`;
  wb.allTab.classList.toggle("is-active", wb.filter === "all");
  wb.shortlistTab.classList.toggle("is-active", wb.filter === "shortlisted");
  wb.allTab.setAttribute("aria-pressed", String(wb.filter === "all"));
  wb.shortlistTab.setAttribute("aria-pressed", String(wb.filter === "shortlisted"));
  wb.countBadge.textContent = String(
    wb.filter === "shortlisted" ? wb.shortlisted.size : wb.total,
  );
  wb.compareCount.textContent = String(wb.compare.size);
  wb.compareLabel.textContent = wb.compare.size >= 2 ? "开始比较 " : "比较 ";
  wb.compareButton.disabled = wb.compare.size < 2;
  wb.compareButton.classList.toggle("is-ready", wb.compare.size >= 2);
  wb.compareButton.setAttribute(
    "aria-label",
    wb.compare.size >= 2
      ? `开始比较已选择的 ${wb.compare.size} 位候选人`
      : "选择至少 2 位候选人后开始比较",
  );
  wb.compareButton.title = wb.compare.size >= 2
    ? "打开候选人对比"
    : "选择 2–4 位候选人后查看对比";
  syncWorkbenchPager(wb);
}

function setWorkbenchFilter(wb, filter) {
  if (filter !== "all" && filter !== "shortlisted") return;
  wb.filter = filter;
  renderWorkbenchList(wb);
  updateWorkbenchChrome(wb);
}

function toggleWorkbenchShortlist(wb, entry) {
  const name = entry.item?.name || entry.item?.docId || "该候选人";
  if (wb.shortlisted.has(entry.key)) {
    wb.shortlisted.delete(entry.key);
    setWorkbenchFeedback(wb, `已将 ${name} 移出本轮入围。`);
    announceAgentStatus(`已将 ${name} 移出本轮入围。`);
  } else {
    wb.shortlisted.set(entry.key, entry);
    setWorkbenchFeedback(wb, `已将 ${name} 加入入围，共 ${wb.shortlisted.size} 人。`);
    announceAgentStatus(`已将 ${name} 加入入围。`);
  }
  renderWorkbenchList(wb);
  updateWorkbenchChrome(wb);
}

function toggleWorkbenchCompare(wb, entry) {
  const name = entry.item?.name || entry.item?.docId || "该候选人";
  if (wb.compare.has(entry.key)) {
    wb.compare.delete(entry.key);
    const remaining = wb.compare.size;
    setWorkbenchFeedback(
      wb,
      remaining > 0
        ? `已将 ${name} 移出比较，当前还剩 ${remaining} 人。`
        : `已将 ${name} 移出比较。`,
    );
    renderWorkbenchList(wb);
    updateWorkbenchChrome(wb);
    announceAgentStatus(`当前已选择 ${remaining} 位候选人用于对比。`);
    return;
  } else if (wb.compare.size >= COMPARE_LIMIT) {
    setWorkbenchFeedback(wb, `最多选择 ${COMPARE_LIMIT} 位候选人进行对比。`, { warning: true });
    return;
  }
  wb.compare.set(entry.key, entry);
  const count = wb.compare.size;
  const shouldOpen = count === 2;
  setWorkbenchFeedback(
    wb,
    shouldOpen
      ? "已选择 2 人，正在打开对比。"
      : count === 1
        ? `已将 ${name} 加入比较，再选 1 位即可查看对比。`
        : `已选择 ${count} 人，点击「开始比较」查看。`,
  );
  renderWorkbenchList(wb);
  updateWorkbenchChrome(wb);
  announceAgentStatus(`当前已选择 ${count} 位候选人用于对比。`);
  if (shouldOpen) {
    window.requestAnimationFrame(() => openWorkbenchComparison(wb));
  }
}

function renderWorkbenchCandidate(wb, entry) {
  const item = entry.item;
  const data = workbenchMatchData(item);
  const key = entry.key;
  const shortlisted = wb.shortlisted.has(key);
  const compared = wb.compare.has(key);
  const candidateName = item?.name || item?.docId || "该候选人";
  const node = el("li", "aw-candidate");
  node.dataset.candidateKey = key;
  node.classList.toggle("is-shortlisted", shortlisted);
  node.classList.toggle("is-compared", compared);

  const top = el("div", "aw-candidate-top");
  const avatar = el("span", "aw-avatar", workbenchCandidateInitial(item));
  avatar.setAttribute("aria-hidden", "true");
  const person = el("div", "aw-person");
  const nameLine = el("div", "aw-name-line");
  const name = el("h3", "", candidateName);
  name.setAttribute("aria-label", `结果排序第 ${entry.rank} 位：${candidateName}`);
  const status = workbenchStatusPill(item, data);
  nameLine.append(name, el("span", `aw-match-pill ${status.tone}`.trim(), status.text));
  const meta = el("p", "aw-person-meta", workbenchMetaText(item));
  person.append(nameLine, meta);
  top.append(avatar, person);
  node.append(top, renderWorkbenchEvidence(item));

  const foot = el("div", "aw-candidate-foot");
  const stats = el("div", "aw-stats");
  const addStat = (text, tone = "") => {
    stats.append(el("span", `aw-stat ${tone}`.trim(), text));
  };
  const scoreText = formatWorkbenchScore(item);
  if (scoreText) {
    const scoreStat = el("span", "aw-stat is-score");
    scoreStat.title = "相关度仅作同层参考，不表示通过率或匹配率。";
    scoreStat.setAttribute(
      "aria-label",
      `相关度 ${scoreText}，仅作同层参考，不表示通过率或匹配率`,
    );
    scoreStat.append(el("span", "aw-score-label", "相关度"), el("b", "", scoreText));
    stats.append(scoreStat);
  }
  if (data.hardApplicable > 0) {
    const hardText = data.hardViolated
      ? `硬条件违规 ${data.hardViolated}${data.hardUnverified ? ` · 待核实 ${data.hardUnverified}` : ""}`
      : data.hardUnverified
        ? `硬条件待核实 ${data.hardUnverified}`
        : "硬条件满足";
    addStat(
      hardText,
      data.hardStatus === "satisfied" ? "is-good" : "is-warning",
    );
  }
  if (data.abilityTotal) {
    addStat(`能力 ${data.matchedAbilities.length}/${data.abilityTotal}`);
  }
  if (data.pathRationale.length) {
    addStat(`路径证据 ${data.pathRationale.length}`);
  }
  if (!stats.childElementCount) addStat("已进入候选名单");

  const actions = el("div", "aw-card-actions");
  const compareLabel = compared
    ? wb.compare.size >= 2 ? "已选比较" : "已选 · 还需 1 人"
    : "比较";
  const compareButton = el("button", "aw-card-action is-compare", compareLabel);
  compareButton.type = "button";
  compareButton.setAttribute("aria-pressed", String(compared));
  compareButton.setAttribute(
    "aria-label",
    compared
      ? `取消比较 ${candidateName}`
      : wb.compare.size >= COMPARE_LIMIT
        ? `最多选择 ${COMPARE_LIMIT} 位候选人`
        : `选择 ${candidateName} 进行对比`,
  );
  compareButton.disabled = !compared && wb.compare.size >= COMPARE_LIMIT;
  compareButton.title = compared
    ? wb.compare.size >= 2 ? "从对比中移除" : "已加入比较，再选择 1 位候选人"
    : wb.compare.size >= COMPARE_LIMIT
      ? `最多选择 ${COMPARE_LIMIT} 位候选人`
      : `选择 ${candidateName} 进行对比`;
  compareButton.addEventListener("click", () => toggleWorkbenchCompare(wb, entry));

  const docId = String(item?.docId || "");
  let resumeLink = null;
  if (docId) {
    resumeLink = el("a", "aw-card-action is-resume", "查看档案");
    resumeLink.href = candidateResumeUrl(item, entry.rank, entry.token);
    resumeLink.target = "_blank";
    resumeLink.rel = "noopener noreferrer";
    resumeLink.title = `查看 ${candidateName} 的完整档案`;
    resumeLink.setAttribute("aria-label", `查看 ${candidateName} 的完整档案（新标签页）`);
    bindCandidateResumeLink(resumeLink, item, entry.rank, entry.token);
  }

  const shortlistButton = el(
    "button",
    `aw-card-action is-shortlist ${shortlisted ? "is-active" : ""}`.trim(),
    shortlisted ? "已入围" : "加入入围",
  );
  shortlistButton.type = "button";
  shortlistButton.setAttribute("aria-pressed", String(shortlisted));
  shortlistButton.title = shortlisted ? "从本轮入围中移出" : "加入本轮入围";
  shortlistButton.addEventListener("click", () => toggleWorkbenchShortlist(wb, entry));
  actions.append(compareButton);
  if (resumeLink) actions.append(resumeLink);
  actions.append(shortlistButton);
  foot.append(stats, actions);
  node.append(foot);
  attachCandidateTooltip(node, item, entry.rank, {
    matchedAbilities: data.matchedAbilities,
    missingAbilities: data.missingAbilities,
    hardMatches: data.hardMatches,
    pathRationale: data.pathRationale,
  });
  return node;
}

function renderAgentWorkbench(container, payload, run, options = {}) {
  const items = Array.isArray(options.items) ? options.items : [];
  const offset = Math.max(0, Number(options.offset || 0));
  const token = options.token ?? payload.queryToken ?? state.queryToken ?? null;
  const total = Math.max(
    0,
    Number(payload.page?.total ?? payload.total ?? items.length),
  );
  const sequence = ++state.workbenchSequence;
  const wb = {
    id: `agent-workbench-${sequence}`,
    payload,
    run,
    token,
    query: String(options.query || payload.query || "").trim(),
    jobTitle: String(options.jobTitle || run?.jobTitle || "").trim(),
    restored: Boolean(options.restored),
    total,
    filter: "all",
    pageEntries: items.map((item, index) => workbenchCandidateEntry(item, offset + index + 1, token)),
    page: payload.page ? { ...payload.page, total } : null,
    shortlisted: new Map(),
    compare: new Map(),
  };
  state.activeWorkbench = wb;

  const overview = deliveryOverview(payload);
  const root = el("section", "agent-workbench");
  root.id = wb.id;
  root.dataset.workbenchId = wb.id;
  root.append(buildWorkbenchTaskHead(wb, payload, run, overview));

  const grid = el("div", "aw-workspace-grid");
  const candidatePanel = el("section", "aw-candidate-panel");
  const toolbar = el("div", "aw-candidate-toolbar");
  const candidateTitle = el("div", "aw-candidate-title");
  const countBadge = el("span", "aw-count-badge", String(total));
  candidateTitle.append(el("h2", "", "候选人"), countBadge);
  const tabs = el("div", "aw-tabs");
  tabs.setAttribute("role", "group");
  tabs.setAttribute("aria-label", "候选人筛选");
  const allTab = el("button", "aw-tab is-active", `全部 ${total}`);
  allTab.type = "button";
  allTab.setAttribute("aria-pressed", "true");
  const shortlistTab = el("button", "aw-tab", "已入围 0");
  shortlistTab.type = "button";
  shortlistTab.setAttribute("aria-pressed", "false");
  allTab.addEventListener("click", () => setWorkbenchFilter(wb, "all"));
  shortlistTab.addEventListener("click", () => setWorkbenchFilter(wb, "shortlisted"));
  tabs.append(allTab, shortlistTab);
  const feedback = el("span", "aw-toolbar-feedback");
  feedback.setAttribute("role", "status");
  feedback.setAttribute("aria-live", "polite");
  const toolbarActions = el("div", "aw-toolbar-actions");
  const compareButton = el("button", "aw-compare-button");
  compareButton.type = "button";
  compareButton.disabled = true;
  const compareCount = el("strong", "", "0");
  const compareLabel = document.createTextNode("比较 ");
  compareButton.append(compareLabel, compareCount);
  compareButton.addEventListener("click", () => openWorkbenchComparison(wb));
  toolbarActions.append(compareButton);
  toolbar.append(candidateTitle, tabs, feedback, toolbarActions);

  const candidateBody = el("div", "aw-candidate-body");
  const list = el("ul", "aw-candidate-list");
  const empty = el("div", "aw-candidate-empty");
  empty.hidden = true;
  const emptyStrong = el("strong");
  const emptyCopy = el("span");
  empty.append(emptyStrong, emptyCopy);
  const pagerParts = createChatPager();
  pagerParts.pagerNode.classList.add("aw-pager");
  candidateBody.append(empty, list, pagerParts.pagerNode);
  candidatePanel.append(toolbar, candidateBody);

  Object.assign(wb, {
    list,
    empty,
    emptyStrong,
    emptyCopy,
    countBadge,
    allTab,
    shortlistTab,
    feedback,
    compareButton,
    compareLabel,
    compareCount,
    pager: pagerParts.pagerNode,
    pageState: pagerParts.stateLabel,
    prev: pagerParts.prev,
    next: pagerParts.next,
  });

  grid.append(candidatePanel, buildWorkbenchRunPanel(wb, payload, run, overview));
  root.append(grid);
  container.append(root);
  container.closest(".chat-result-message")?.classList.add("is-workbench");

  renderWorkbenchList(wb);
  updateWorkbenchChrome(wb);
  setAgentResultMode(true);

  if (run) {
    run.resultList = list;
    run.resultPager = pagerParts.pagerNode;
    run.resultPrevBtn = pagerParts.prev;
    run.resultNextBtn = pagerParts.next;
    run.resultPageState = pagerParts.stateLabel;
    if (payload.page) updateChatPager(payload.page);
    else syncWorkbenchPager(wb);
  }
  return wb;
}

function appendChatDeliveryOverview(container, payload) {
  const overview = deliveryOverview(payload);
  const section = el("section", "chat-delivery-overview");
  section.classList.toggle("is-met", overview.reached);
  section.classList.toggle("is-gap", !overview.reached && overview.gap > 0);
  section.classList.toggle("is-review", overview.hasHardGap);

  const head = el("div", "chat-delivery-head");
  const title = el("div");
  title.append(
    el(
      "strong",
      "",
      overview.hasHardGap
        ? `已交付 ${overview.delivered} 人，${overview.hardAffected} 人需复核`
        : overview.reached
          ? "目标交付已达成"
          : "本次未达到目标人数",
    ),
    el(
      "span",
      "",
      overview.hasHardGap
        ? `人数目标优先保证；硬条件${hardGapBreakdown(overview)}，均已保留并明确标注。`
        : "目标按你设置的交付人数计算；缺口只表示数量未达标，不代表 Agent 执行失败。",
    ),
  );
  head.append(
    title,
    el(
      "span",
      "chat-delivery-state",
      overview.hasHardGap ? "需复核" : overview.reached ? "已达成" : "有缺口",
    ),
  );

  const facts = el("div", "chat-delivery-facts");
  const addFact = (value, label) => {
    const fact = el("span");
    fact.append(el("b", "", value), el("small", "", label));
    facts.append(fact);
  };
  addFact(overview.target, "目标人数");
  addFact(overview.delivered, "实际交付");
  addFact(overview.gap, "人数缺口");
  if (overview.hasHardGap) addFact(overview.hardAffected, "硬条件缺口");

  section.append(head, facts);
  if (overview.reason) {
    const reason = el("p", "chat-delivery-reason");
    reason.append(el("strong", "", "结束原因："), document.createTextNode(overview.reason));
    section.append(reason);
  }
  container.append(section);
}

function appendScoreMethodNote(container) {
  container.append(
    el(
      "p",
      "chat-score-note",
      "综合顺位优先依据硬条件、能力覆盖与证据；相关度仅作同层参考，不代表通过率或匹配率。",
    ),
  );
}

function reasoningStep(kicker, copy) {
  const item = el("li");
  item.append(el("span", "", kicker), el("p", "", copy));
  return item;
}

function formatRoundDuration(ms) {
  const value = Math.max(0, Number(ms || 0));
  if (!value) return "0 秒";
  if (value < 1000) return `${Math.round(value)} 毫秒`;
  return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} 秒`;
}

function roundAdjustmentParts(summary) {
  const parts = [];
  const paths = Number(summary?.newPathCount || 0);
  const softSignals = Number(summary?.newSoftSignals || 0);
  const sourceHints = Number(summary?.newSourceHints || 0);
  if (paths > 0) parts.push(`新增 ${paths} 条路径`);
  if (softSignals > 0) parts.push(`+${softSignals} 个软信号`);
  if (sourceHints > 0) parts.push(`+${sourceHints} 个来源假设`);
  return parts;
}

function roundActionLabels(summary) {
  const actions = Array.isArray(summary?.actions) ? summary.actions : [];
  const labels = [];
  for (const action of actions) {
    const label = ROUND_ACTION_LABEL[action?.kind] || String(action?.kind || "").trim();
    if (label && !labels.includes(label)) labels.push(label);
  }
  return labels;
}

function roundBadgeText(summary) {
  const parts = roundActionLabels(summary).slice(0, 2);
  const softSignals = Number(summary?.newSoftSignals || 0);
  const sourceHints = Number(summary?.newSourceHints || 0);
  if (softSignals > 0) parts.push(`+${softSignals} 个软信号`);
  if (sourceHints > 0) parts.push(`+${sourceHints} 个来源假设`);
  return parts.join(" · ") || roundAdjustmentParts(summary).join(" · ") || "画像更新";
}

function finalSearchHasGain(summary) {
  return ["newUnique", "newJudged", "newMustQualified"].some(
    (key) => Number(summary?.[key] || 0) > 0,
  );
}

function roundOutcomeCopy(summary) {
  if (summary?.stopReason) {
    return `去向 · 结束：${FINALIZE_REASON_COPY[summary.stopReason] || summary.stopReason}`;
  }
  if (summary?.next === "execute") return "去向 · 调整已生效，进入下一轮验证";
  if (summary?.next === "reflect") return "去向 · 缺口仍在，继续反思下一轮策略";
  if (summary?.next === "finalize") return "去向 · 策略已收敛，进入最终交付";
  return "";
}

function appendRoundEffects(container, payload) {
  const rounds = (Array.isArray(payload.debug?.roundSummaries) ? payload.debug.roundSummaries : [])
    .filter((summary) => summary && typeof summary === "object");
  const finalSearch =
    payload.debug?.finalSearch && typeof payload.debug.finalSearch === "object"
      ? payload.debug.finalSearch
      : null;
  const showFinalSearch = Boolean(finalSearch && finalSearchHasGain(finalSearch));
  if (rounds.length < 2 && !showFinalSearch) return;

  const section = el("section", "chat-round-effects");
  section.setAttribute("aria-label", "每轮搜索策略效果");
  const head = el("div", "chat-round-effects-head");
  const title = el("div");
  title.append(
    el("strong", "", "策略效果"),
    el("span", "", "真实 evaluate / reflect / revise / finalize 数据的增量与成本"),
  );
  head.append(title, el("span", "chat-round-effects-count", `${Math.max(1, rounds.length)} 轮`));
  section.append(head);

  if (rounds.length) {
    const list = el("ol", "chat-round-effect-list");
    for (const summary of rounds) {
      const roundNumber = Math.max(1, Number(summary.round || 1));
      const baseline = roundNumber === 1;
      const item = el("li", `chat-round-effect${baseline ? " is-baseline" : ""}`);
      const titleRow = el("div", "chat-round-effect-title");
      titleRow.append(
        el("b", "", `第 ${roundNumber} 轮`),
        el("span", "", baseline ? "初始策略" : "策略调整后"),
      );
      if (!baseline) {
        const badge = el("em", "chat-round-effect-badge", roundBadgeText(summary));
        if (summary.actions?.length) {
          badge.title = summary.actions
            .map((action) => `${ROUND_ACTION_LABEL[action.kind] || action.kind}${action.rationale ? `：${action.rationale}` : ""}`)
            .join("\n");
        }
        titleRow.append(badge);
      }
      item.append(titleRow);

      if (!baseline) {
        const gaps = (summary.gaps || []).filter(Boolean);
        const actions = Array.isArray(summary.actions) ? summary.actions : [];
        const trigger = gaps.length
          ? gaps.slice(0, 2).join("；")
          : actions.find((action) => action?.rationale)?.rationale || "覆盖检查触发策略调整";
        const triggerLine = el("p", "chat-round-effect-trigger");
        triggerLine.append(el("strong", "", "触发："), document.createTextNode(trigger));
        item.append(triggerLine);
      }

      const grid = el("div", "chat-round-effect-grid");
      const addMetric = (label, value, tone = "", tooltip = "") => {
        const metric = el("span", `chat-round-effect-metric ${tone}`.trim());
        metric.append(el("small", "", label), el("b", "", value));
        if (tooltip) metric.title = tooltip;
        grid.append(metric);
      };
      addMetric(`${baseline ? "" : "新增"}召回`, Number(summary.newUnique || 0), "is-yield");
      addMetric(`${baseline ? "" : "新增"}判断`, Number(summary.newJudged || 0), "is-judge");
      addMetric(
        `${baseline ? "" : "新增"}通过`,
        Number(summary.newMustQualified || 0),
        "is-pass",
        "新增判断中已命中全部必备能力的人数",
      );
      const cost = `${Number(summary.llmCallsDelta || 0)} LLM · ${formatRoundDuration(summary.elapsedMsDelta)}`;
      addMetric(
        baseline ? "起始成本" : "本轮消耗",
        baseline ? cost : `+${Number(summary.llmCallsDelta || 0)} LLM · +${formatRoundDuration(summary.elapsedMsDelta)}`,
        "is-cost",
      );
      item.append(grid);
      const outcome = roundOutcomeCopy(summary);
      if (outcome) item.append(el("p", "chat-round-effect-outcome", outcome));
      list.append(item);
    }
    section.append(list);
  }

  if (showFinalSearch) {
    const finalRow = el("div", "chat-round-final");
    const finalTitle = el("div", "chat-round-final-title");
    finalTitle.append(
      el("b", "", finalSearch.label || "最终收敛检索"),
      el("span", "", "画像富化后的额外召回与判断"),
    );
    const facts = el("div", "chat-round-final-facts");
    const addFact = (label, value) => {
      const fact = el("span");
      fact.append(el("b", "", value), el("small", "", label));
      facts.append(fact);
    };
    addFact("新召回", Number(finalSearch.newUnique || 0));
    addFact("新判断", Number(finalSearch.newJudged || 0));
    addFact("新通过", Number(finalSearch.newMustQualified || 0));
    addFact(
      "本轮消耗",
      `+${Number(finalSearch.llmCallsDelta || 0)} LLM · +${formatRoundDuration(finalSearch.elapsedMsDelta)}`,
    );
    finalRow.append(finalTitle, facts);
    section.append(finalRow);
  }

  container.append(section);
}

function appendChatReasoning(container, payload) {
  if (payload.debug?.difficultyRoute === "direct_search") return;
  const events = state.runEvents || [];
  const overview = deliveryOverview(payload);
  let reflectIndex = -1;
  events.forEach((event, index) => {
    if (event.stage === "reflect") reflectIndex = index;
  });

  const section = el("section", "chat-reasoning");
  const head = el("div", "chat-reasoning-head");
  const title = el("div");
  const steps = el("ol", "chat-reasoning-steps");

  if (reflectIndex >= 0) {
    const reflectEvent = events[reflectIndex];
    const prior = events.slice(0, reflectIndex);
    const executeEvent = [...prior].reverse().find((event) => event.stage === "execute");
    const evaluateEvent = [...prior].reverse().find((event) => event.stage === "evaluate");
    const reviseEvent = events
      .slice(reflectIndex + 1)
      .find((event) => event.stage === "revise");
    const round = events
      .slice(0, reflectIndex + 1)
      .filter((event) => event.stage === "execute").length;
    const continued = events
      .slice(reflectIndex + 1)
      .some((event) => event.stage === "execute");
    const gaps = (reflectEvent.detail?.gaps || []).filter(Boolean);
    const newSignals = Number(reflectEvent.detail?.newSoftSignals || 0);

    title.append(
      el("strong", "", continued ? "为什么继续搜索" : "为什么在这里结束"),
      el("span", "", `第 ${Math.max(1, round)} 轮 · 来自真实 evaluate / reflect / revise 事件`),
    );
    if (executeEvent?.detail?.recalled !== undefined) {
      steps.append(
        reasoningStep("观察", `工具执行后，当时累计召回 ${Number(executeEvent.detail.recalled || 0)} 人次。`),
      );
    }
    if (evaluateEvent?.detail?.next === "reflect") {
      steps.append(reasoningStep("判断", "覆盖检查未满足交付条件，转入反思，而不是重复原查询。"));
    }
    if (gaps.length) {
      steps.append(reasoningStep("缺口", `尚未覆盖的能力：${gaps.join("、")}。`));
    } else if (newSignals > 0) {
      steps.append(reasoningStep("反思", `反思阶段新增 ${newSignals} 个软信号，用于补充下一轮检索。`));
    }
    if (reviseEvent) {
      const softSignals = reviseEvent.detail?.softSignals;
      const ending = overview.reason || "本轮没有产生新的待执行路径，搜索就此结束。";
      if (softSignals !== undefined) {
        steps.append(
          reasoningStep(
            "修订",
            `画像当前累计 ${Number(softSignals || 0)} 个软信号；${
              continued
                ? reviseEvent.detail?.converged
                  ? "画像已收敛，进入下一轮验证。"
                  : "继续验证新增方向。"
                : ending
            }`,
          ),
        );
      } else if (continued) {
        steps.append(reasoningStep("修订", "已根据缺口调整搜索路径，并进入下一轮验证。"));
      } else {
        steps.append(reasoningStep("修订", `本轮修订后未进入下一轮；${ending}`));
      }
    } else {
      steps.append(
        reasoningStep(
          "修订",
          continued
            ? "已根据缺口调整搜索路径，并进入下一轮验证。"
            : overview.reason || "本次反思后没有产生新的可执行路径，随后收敛现有结果。",
        ),
      );
    }
  } else {
    title.append(
      el("strong", "", "本轮如何结束"),
      el("span", "", "来自真实覆盖检查与最终结束原因"),
    );
    const evaluateEvent = [...events].reverse().find((event) => event.stage === "evaluate");
    if (overview.reasonCode === "difficulty_single_pass") {
      steps.append(reasoningStep("路由", "需求被判定为低难度单次检索，覆盖检查后直接收敛。"));
    } else if (evaluateEvent?.detail?.next === "finalize") {
      steps.append(reasoningStep("判断", "覆盖检查已满足结束条件，未触发策略修订。"));
    } else if (overview.reason) {
      steps.append(reasoningStep("结束", overview.reason));
    } else {
      steps.append(reasoningStep("记录", "本次没有产生 reflect / revise 事件，未发生策略改写。"));
    }
  }

  head.append(title);
  section.append(head, steps);
  container.append(section);
}

function renderChatResult(payload) {
  const run = beginChatRun();
  configureChatRoute(run, {
    difficultyRoute: payload.debug?.difficultyRoute,
    plannedStages: payload.debug?.plannedStages,
  });
  const cancelled = Boolean(payload.debug?.cancelled);
  const clarification = Boolean(payload.clarification?.needClarification);
  const degradedReason =
    payload.debug?.degradedReason || payload.debug?.agentMetrics?.meta?.degradedReason || "";
  const degraded = Boolean(degradedReason);
  const items = Array.isArray(payload.items) ? payload.items : [];
  const resultTone = cancelled
    ? "stopped"
    : clarification
      ? "clarify"
      : degraded
        ? "degraded"
        : "complete";
  const resultLabel = cancelled
    ? "已停止"
    : clarification
      ? "需补充"
      : degraded
        ? "已降级"
        : "已完成";
  setChatStatus(run.status, resultLabel, resultTone);
  run.progressTitle.textContent = cancelled
    ? "搜索已停止"
    : clarification
      ? "等待补充需求"
      : "执行完成";
  run.progressMeta.textContent = clarification
    ? `${run.visitedStages.size} 阶段 · 等待补充`
    : `${run.visitedStages.size} 阶段 · ${routeLabel(run.route)} · ${state.stageCount} 个事件`;
  run.progressFootnote.textContent = clarification
    ? "只完成了岗位理解，未启动检索、候选人判断或策略修订。"
    : routeFootnote(run.route);
  for (const item of run.progressList.querySelectorAll(".chat-progress-item")) {
    item.classList.remove("is-active");
    item.classList.add("is-done");
  }
  for (const segment of run.progressTrack.children) {
    const visited = run.visitedStages.has(segment.dataset.stage);
    segment.classList.toggle("is-hit", visited);
    segment.classList.toggle("is-skipped", !visited && !clarification);
    segment.classList.remove("is-current");
  }

  const message = createChatMessage("agent", "chat-result-message agent-delivery");
  setChatStatus(message.status, resultLabel, resultTone);
  const head = el("div", "chat-result-head");
  const marker = el("span", "chat-result-check", cancelled ? "■" : clarification ? "!" : "✓");
  marker.classList.toggle("is-clarify", clarification);
  const heading = el("div");
  const headingTitle = cancelled
    ? "本次搜索已停止"
    : clarification
      ? "还需要补充岗位方向"
      : "本次搜索已完成";
  const headingCopy = cancelled
    ? "已保留停止前完成的工作，未继续发起新的检索或模型调用。"
    : clarification
      ? "当前需求只说明了技能，还没有说明希望寻找哪类候选人。补充方向后我会继续执行，不重复消耗本轮工作。"
      : degraded
        ? "搜索已完成，但部分阶段触发了降级。以下结果保留，建议结合匹配证据复核。"
        : "以下是根据当前需求筛选出的候选人。悬停或聚焦候选人，可查看完整匹配证据与来源路径。";
  heading.append(
    el("h3", "", headingTitle),
    el("p", "", headingCopy),
  );
  head.append(marker, heading);
  message.body.append(head);

  const metrics = payload.debug?.agentMetrics;
  if (metrics && !clarification) {
    const stats = el("div", "chat-result-stats");
    const add = (value, label) => {
      const item = el("span");
      item.append(el("b", "", value ?? "—"), el("small", "", label));
      stats.append(item);
    };
    const directRoute = payload.debug?.difficultyRoute === "direct_search";
    const overview = deliveryOverview(payload);
    add(Number(payload.total || 0), "交付候选人");
    if (overview.hasHardGap) add(overview.hardAffected, "硬条件缺口");
    if (directRoute) {
      add(1, "SearchTool 调用");
      add(metrics.meta?.agentLlmCalls ?? 0, "Agent LLM");
      add(metrics.meta?.parserLlmCalls ?? metrics.meta?.llmCalls ?? 0, "V2 parser LLM");
    } else {
      add(resolveExecutionRounds(payload), "执行轮次");
      add(metrics.recall?.judged ?? "—", "通过判定");
    }
    message.body.append(stats);
  }
  if (degraded) {
    const note = el("p", "chat-degrade-note");
    note.append(el("strong", "", "降级原因："), document.createTextNode(degradedReason));
    message.body.append(note);
  }
  appendRoundEffects(message.body, payload);

  let workbench = null;
  if (clarification) {
    const clarify = el("div", "chat-clarification");
    clarify.append(el("strong", "", "还需要补充一些信息"));
    const list = el("ul");
    for (const question of payload.clarification.questions || []) list.append(el("li", "", question));
    clarify.append(list);
    appendClarificationOptions(clarify, payload.clarification.options || []);
    message.body.append(clarify);
  } else if (!cancelled && items.length) {
    workbench = renderAgentWorkbench(message.body, payload, run, {
      items,
      offset: Number(payload.page?.offset || 0),
      token: payload.queryToken || state.queryToken || null,
      query: state.activeRun?.query || payload.query || "",
      jobTitle: run.jobTitle || "",
    });
  } else {
    if (!cancelled) {
      appendChatDeliveryOverview(message.body, payload);
      appendChatReasoning(message.body, payload);
    }
    if (items.length) appendScoreMethodNote(message.body);
    const list = addChatCandidateCards(run, items, Number(payload.page?.offset || 0));
    run.resultList = list;
    message.body.append(list);
    const pagerParts = createChatPager();
    run.resultPager = pagerParts.pagerNode;
    run.resultPrevBtn = pagerParts.prev;
    run.resultNextBtn = pagerParts.next;
    run.resultPageState = pagerParts.stateLabel;
    message.body.append(pagerParts.pagerNode);
    updateChatPager(payload.page ? { ...payload.page, totalPages: payload.page.totalPages } : {});
    if (!items.length) message.body.append(el("p", "chat-no-results", "本轮没有找到符合条件的候选人，可以补充或放宽条件后重试。"));
  }
  run.resultArticle = message.article;
  if (workbench) run.article.hidden = true;
  if (!clarification && !cancelled) compactCompletedChatRun(run);
  scrollChatToNode(message.article);
}

function renderChatReply(payload) {
  const pending = state.activeRun?.pending;
  pending?.remove();
  const message = createChatMessage("agent", "chat-reply-message");
  message.status?.remove();
  message.body.append(el("p", "chat-reply-text", payload.message || ""));
  const suggestions = payload.suggestions || [];
  if (suggestions.length) {
    const actions = el("div", "chat-reply-suggestions");
    for (const item of suggestions) {
      const button = el("button", "", item.label || item.query || "示例需求");
      button.type = "button";
      button.addEventListener("click", () => {
        chatQueryInput.value = item.query || "";
        chatQueryInput.focus();
      });
      actions.append(button);
    }
    message.body.append(actions);
  }
  announceAgentStatus("Search Agent 已回复，本次未启动搜索。");
  scrollChatToEnd();
}

function renderChatError(message, title = "执行失败") {
  state.activeRun?.pending?.remove();
  const run = state.activeRun?.chatRun;
  if (run) setChatStatus(run.status, "失败", "failed");
  const chatMessage = createChatMessage("agent", "chat-error-message");
  setChatStatus(chatMessage.status, title, "failed");
  chatMessage.body.append(
    el("h3", "", title === "服务繁忙" ? "当前搜索服务繁忙" : "这次执行没有完成"),
    el("p", "", message),
  );
  announceAgentStatus(`${title}：${message}`);
  scrollChatToEnd();
}

function resetChatView() {
  chatTranscript.replaceChildren();
  chatIntro.hidden = false;
  chatQueryInput.value = "";
  setAgentResultMode(false);
  state.activeRun = null;
  state.activeWorkbench = null;
  state.lastResult = null;
  state.runEvents = [];
  if (state.viewMode === "agent") chatScroll.scrollTop = 0;
}

function loadConversationState() {
  try {
    const requested = new URLSearchParams(location.search).get("conversation");
    const raw = localStorage.getItem(CONVERSATION_KEY) || sessionStorage.getItem(CONVERSATION_KEY);
    const saved = raw ? JSON.parse(raw) : {};
    state.conversationId = requested || saved.conversationId || null;
    state.conversationTurns = Number(saved.conversationTurns || 0);
  } catch (err) {
    state.conversationId = null;
  }
}

function saveConversationState() {
  try {
    localStorage.setItem(
      CONVERSATION_KEY,
      JSON.stringify({
        conversationId: state.conversationId,
        conversationTurns: state.conversationTurns,
      }),
    );
  } catch (err) {
    // The in-memory conversation still works when storage is unavailable.
  }
}

function syncConversationUrl() {
  try {
    const url = new URL(location.href);
    if (state.conversationId) url.searchParams.set("conversation", state.conversationId);
    else url.searchParams.delete("conversation");
    history.replaceState(null, "", url);
  } catch (err) {
    // URL state is optional; the active ID is also kept in localStorage.
  }
}

async function loadConversations() {
  if (state.conversationsLoading) return;
  state.conversationsLoading = true;
  try {
    const resp = await fetch("/conversations?companyId=1&limit=30", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const rows = await resp.json();
    state.conversations = Array.isArray(rows) ? rows : [];
  } catch (err) {
    state.conversations = state.conversations || [];
  } finally {
    state.conversationsLoading = false;
    renderConversation();
  }
}

function createTrashIcon() {
  const namespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(namespace, "path");
  path.setAttribute(
    "d",
    "M3.5 4.5h9M6 4.5V3h4v1.5M4.8 4.5l.5 8h5.4l.5-8M6.5 6.5v4M9.5 6.5v4",
  );
  svg.append(path);
  return svg;
}

async function deleteConversation(conversation, button, row) {
  const conversationId = conversation?.conversationId;
  if (!conversationId) return;
  const title = conversation.title || conversation.lastQuery || "未命名会话";
  const isActive = state.conversationId === conversationId;
  if (isActive && state.running) {
    setComposerNote("当前会话正在执行搜索，请先停止或等待完成后再删除。", { error: true });
    return;
  }
  const confirmed = window.confirm(
    `删除会话「${title}」？\n\n该会话的检索记录和候选人上下文将被移除，此操作无法撤销。`,
  );
  if (!confirmed) return;

  button.disabled = true;
  row.classList.add("is-deleting");
  row.setAttribute("aria-busy", "true");
  try {
    const resp = await fetch(
      `/conversations/${encodeURIComponent(conversationId)}?companyId=1`,
      { method: "DELETE" },
    );
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const result = await resp.json().catch(() => ({}));
    state.conversations = state.conversations.filter(
      (item) => item.conversationId !== conversationId,
    );
    if (isActive) {
      startNewConversation();
    } else {
      renderConversation();
    }
    setComposerNote(
      result.deleted === false
        ? "该会话已不存在，列表已同步。"
        : `已删除会话「${title}」。`,
    );
  } catch (err) {
    button.disabled = false;
    row.classList.remove("is-deleting");
    row.removeAttribute("aria-busy");
    setComposerNote(`删除会话失败：${err.message}`, { error: true });
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

  if (agentSessionCount) {
    agentSessionCount.textContent = `${state.conversations.length} 个会话`;
  }
  if (agentSessionList) {
    agentSessionList.replaceChildren();
    state.conversations.forEach((conversation, index) => {
      const item = el("li", "agent-session-row");
      const button = el("button", "agent-session-item");
      const title = conversation.title || conversation.lastQuery || "未命名会话";
      button.type = "button";
      button.title = title;
      button.classList.toggle("is-active", conversation.conversationId === state.conversationId);
      button.append(
        el("span", "agent-session-index", String(index + 1).padStart(2, "0")),
        el("span", "agent-session-query", title),
        el("small", "", `${Number(conversation.turnCount || 0)} 轮`),
      );
      button.addEventListener("click", () => {
        void restoreConversation(conversation.conversationId);
        if (sidebarMediaQuery.matches) setSidebarVisible(false);
      });
      const deleteButton = el("button", "agent-session-delete");
      deleteButton.type = "button";
      deleteButton.title = `删除会话：${title}`;
      deleteButton.setAttribute("aria-label", `删除会话：${title}`);
      deleteButton.append(createTrashIcon());
      deleteButton.addEventListener("click", (event) => {
        event.stopPropagation();
        void deleteConversation(conversation, deleteButton, item);
      });
      item.append(button, deleteButton);
      agentSessionList.append(item);
    });
  }
  if (agentSessionEmpty) {
    agentSessionEmpty.hidden = state.conversations.length > 0;
  }
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
  state.excludedCandidateIds = new Set();
  state.queryToken = null;
  try {
    localStorage.removeItem(CONVERSATION_KEY);
    sessionStorage.removeItem(CONVERSATION_KEY);
  } catch (err) {
    // Ignore storage failures; clearing in-memory state is enough.
  }
  syncConversationUrl();
  hidePager();
  renderConversation();
}

async function restoreConversation(conversationId) {
  if (!conversationId || state.running) return;
  try {
    const resp = await fetch(
      `/conversations/${encodeURIComponent(conversationId)}?companyId=1`,
      { cache: "no-store" },
    );
    if (!resp.ok) throw new Error(resp.status === 404 ? "该会话已过期或被删除。" : `HTTP ${resp.status}`);
    const memory = await resp.json();
    const turns = Array.isArray(memory.turns) ? memory.turns : [];
    state.conversationId = memory.conversationId;
    state.conversationTurns = Number(memory.totalTurns || turns.length || 0);
    state.conversationHistory = turns.slice(-6).map((turn) => ({
      query: turn.query || "",
      total: Number(turn.resultCount || 0),
    }));
    state.excludedCandidateIds = new Set(memory.excludedCandidateIds || []);
    state.queryToken = turns[turns.length - 1]?.queryToken || null;
    saveConversationState();
    syncConversationUrl();
    renderConversation();
    renderRestoredConversation(memory);
  } catch (err) {
    runHint.classList.add("error");
    runHint.textContent = err.message;
    if (state.conversationId === conversationId) {
      resetConversation();
      await loadConversations();
    }
  }
}

function renderRestoredConversation(memory) {
  resetUI();
  resetChatView();
  const turns = Array.isArray(memory.turns) ? memory.turns.slice(-6) : [];
  if (!turns.length) return;
  chatIntro.hidden = true;

  const lastTurnIndex = turns.length - 1;
  for (let turnIndex = 0; turnIndex < turns.length; turnIndex += 1) {
    const turn = turns[turnIndex];
    appendChatUserMessage(turn.query || "");
    const cancelled = turn.status === "cancelled";
    const clarification = Boolean(turn.clarification?.needClarification);
    const routed = turn.status === "reply";
    const items = Array.isArray(turn.resultItems) ? turn.resultItems : [];
    const useWorkbench =
      turnIndex === lastTurnIndex &&
      turn.status === "completed" &&
      !cancelled &&
      !clarification &&
      !routed &&
      items.length > 0;
    const message = createChatMessage(
      "agent",
      `chat-result-message agent-delivery restored-conversation-message${useWorkbench ? " is-workbench" : ""}`,
    );
    const tone = routed ? "complete" : cancelled ? "stopped" : clarification ? "clarify" : turn.status === "degraded" ? "degraded" : "complete";
    const label = routed
      ? INTENT_LABEL[turn.route] || "意图回复"
      : cancelled ? "已停止" : clarification ? "需补充" : turn.status === "degraded" ? "已降级" : "已完成";
    setChatStatus(message.status, label, tone);
    if (!useWorkbench) {
      message.body.append(
        el("h3", "", routed ? "会话回复" : "历史检索结果"),
        el("p", "chat-result-copy", turn.assistantSummary || "本轮对话已恢复。"),
      );
    }

    if (clarification) {
      const questions = (turn.clarification.questions || []).filter(Boolean);
      if (questions.length) {
        const list = el("ul", "chat-clarification-list");
        for (const question of questions) list.append(el("li", "", question));
        message.body.append(list);
      }
    }

    if (items.length) {
      if (useWorkbench) {
        const workbenchPayload = {
          query: turn.query || "",
          total: Number(turn.resultCount || items.length),
          topK: Number(turn.topK || 0),
          items,
          clarification: null,
          queryToken: turn.queryToken || null,
          page: null,
          hardConditionSummary: turn.hardConditionSummary || null,
          debug: {},
        };
        renderAgentWorkbench(message.body, workbenchPayload, null, {
          items,
          offset: 0,
          token: turn.queryToken || null,
          query: turn.query || "",
          jobTitle: turn.jobProfileSummary?.title || "",
          restored: true,
        });
      } else {
        appendScoreMethodNote(message.body);
        const list = el("ul", "chat-candidates");
        items.forEach((item, index) => {
          const card = renderCandidate(item, index + 1, {
            resumeToken: turn.queryToken || null,
          });
          card.classList.add("chat-candidate");
          list.append(card);
        });
        message.body.append(list);
      }
    }
  }

  const last = turns[turns.length - 1];
  const restoredPayload = {
    query: last.query || "",
    total: Number(last.resultCount || 0),
    topK: Number(last.topK || 0),
    items: Array.isArray(last.resultItems) ? last.resultItems : [],
    clarification: last.clarification || null,
    hardConditionSummary: last.hardConditionSummary || null,
    debug: {},
  };
  const restoredOverview = deliveryOverview(restoredPayload);
  state.lastResult = restoredPayload;
  if (last.status === "reply") {
    results.replaceChildren();
    hideConsoleDelivery();
    resultsEmpty.hidden = true;
    hidePager();
    resultsHint.textContent = "意图回复";
  } else {
    renderResults(restoredPayload);
    resultsHint.textContent = `${restoredPayload.items.length} 人 · 历史会话`;
  }
  metrics.hidden = true;
  traceWrap.hidden = true;
  setAgentState(
    "completed",
    "历史会话已恢复",
    last.status === "reply"
      ? `共 ${state.conversationTurns} 轮，当前展示最近一次意图回复。`
      : restoredOverview.hasHardGap
        ? `共 ${state.conversationTurns} 轮，当前展示最近一轮结果；${restoredOverview.hardAffected} 人存在硬条件缺口，请复核。`
        : `共 ${state.conversationTurns} 轮，当前展示最近一轮结果。`,
  );
  scrollChatToEnd("auto");
}

function buildRail(stageKeys = STAGE_KEYS) {
  const rail = $("stage-rail");
  rail.replaceChildren();
  rail.style.setProperty("--rail-cols", String(Math.max(1, stageKeys.length)));
  for (const key of stageKeys) {
    const label = STAGE_LABEL[key] || key;
    const item = el("span", "rail-item");
    item.append(el("span", "", label), el("b", "", key));
    item.dataset.stage = key;
    rail.append(item);
  }
}

function markRail(stage) {
  if (!STAGE_LABEL[stage]) return;
  for (const item of document.querySelectorAll(".rail-item")) {
    item.classList.toggle("active", item.dataset.stage === stage);
    item.classList.remove("is-skipped");
    if (item.dataset.stage === stage) item.classList.add("hit");
  }
}

function completeRail() {
  const visited = new Set(state.runEvents.map((event) => event.stage));
  for (const item of document.querySelectorAll(".rail-item")) {
    const hit = visited.has(item.dataset.stage);
    item.classList.toggle("hit", hit);
    item.classList.toggle("is-skipped", !hit);
    item.classList.remove("active");
  }
}

function resetUI() {
  timeline.replaceChildren();
  results.replaceChildren();
  timelineEmpty.hidden = true;
  resultsEmpty.hidden = true;
  setResultsEmptyCopy("等待 Agent 交付", "这里不会只给名字：命中能力、硬条件与来源路径会一起到达。");
  metrics.hidden = true;
  traceWrap.hidden = true;
  state.routeMeta = null;
  resetRouteSummary();
  traceJson.textContent = "";
  resultsHint.textContent = "—";
  hideConsoleDelivery();
  hidePager();
  state.queryToken = null;
  state.pageOffset = 0;
  state.pageTotal = 0;
  state.pageTotalPages = 1;
  state.pageCurrent = 1;
  state.pageHasMore = false;
  state.executeCount = 0;
  state.stageCount = 0;
  buildRail();
  setAgentProgress();
  setAgentState(
    "idle",
    "Agent 待命",
    "等待岗位简报；收到后会先判断难度，再选择直连、轻量或完整反思路径。",
  );
}

function setRunning(active) {
  state.running = active;
  runBtn.disabled = active;
  stopBtn.hidden = !active;
  stopBtn.disabled = false;
  newConversationBtn.disabled = active;
  if (chatRunBtn) chatRunBtn.disabled = active;
  if (chatStopBtn) {
    chatStopBtn.hidden = !active;
    chatStopBtn.disabled = false;
  }
  if (chatNewBtn) chatNewBtn.disabled = active;
  runHint.classList.remove("error");
  if (active) {
    runHint.textContent = "Agent 正在运行：阶段事件会实时出现在中间轨迹。";
  }
  if (state.runtimeMode) renderRuntimeMode();
  else syncModeToggleState();
}

function addRoundDivider(round) {
  const divider = el("li", "round-divider", `ROUND ${round}`);
  timeline.append(divider);
}

function addWaiting() {
  const waiting = el("li", "waiting");
  const copy = el("div", "waiting-copy");
  copy.append(
    el("strong", "", "Agent 正在继续执行"),
    el("span", "", "下一阶段完成后会自动更新，不需要刷新页面。"),
  );
  waiting.append(copy);
  waiting.id = "waiting-row";
  timeline.append(waiting);
}

function removeWaiting() {
  document.getElementById("waiting-row")?.remove();
}

function stageDetail(stage, detail = {}) {
  const parts = [];
  const list = (values) => (values || []).join(" · ");

  if (stage === "direct_search") {
    if (detail.jobTitle) parts.push(kv("岗位", detail.jobTitle));
    if (detail.skills?.length) parts.push(kv("技能", list(detail.skills)));
    if (detail.cities?.length) parts.push(kv("城市", list(detail.cities)));
    if (detail.years?.length) parts.push(kv("年限", detail.years.join("–")));
    if (detail.irSource) {
      parts.push(kv("IR 来源", detail.irSource === "hybrid_v2_parser" ? "Hybrid V2 parser" : "本地规则回退"));
    }
    if (detail.parserAttempts !== undefined) parts.push(kv("Parser 调用", detail.parserAttempts));
    if (detail.ir) {
      const raw = el("details", "raw");
      raw.append(
        summaryWithLabel("CandidateSearchIR", "查看 CandidateSearchIR 原始数据"),
        el("pre", "", JSON.stringify(detail.ir, null, 2)),
      );
      parts.push(raw);
    }
  } else if (stage === "understand") {
    if (detail.jobTitle) parts.push(kv("岗位", detail.jobTitle));
    if (detail.mustAbilities?.length) parts.push(kv("必备能力", list(detail.mustAbilities)));
    if (detail.hardConstraints !== undefined) parts.push(kv("硬条件", detail.hardConstraints));
    if (detail.historyTurns) parts.push(kv("历史上下文", `${detail.historyTurns} 轮`));
  } else if (stage === "plan") {
    if (detail.paths !== undefined) parts.push(kv("路径", detail.paths));
    if (detail.rationales?.length) {
      const stack = el("div", "plan-path-list");
      detail.rationales.forEach((rationale, index) => {
        const item = el("div", "plan-path");
        item.append(
          el("span", "plan-path-no", String(index + 1).padStart(2, "0")),
          el("span", "plan-path-copy", rationale),
        );
        stack.append(item);
      });
      parts.push(stack);
    }
  } else if (stage === "execute") {
    parts.push(toolCallCard(detail));
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

function toolCallCard(detail = {}) {
  const card = el("div", "tool-call");
  const head = el("div", "tool-call-head");
  head.append(
    el("span", "tool-call-icon", "↳"),
    el("strong", "", "SearchTool"),
    el("code", "", "search_batch"),
    el("span", "tool-call-status", "RETURNED"),
  );
  const metrics = el("div", "tool-call-metrics");
  const addMetric = (label, value) => {
    if (value === undefined || value === null) return;
    const metric = el("span", "tool-call-metric");
    metric.append(el("small", "", label), el("b", "", value));
    metrics.append(metric);
  };
  addMetric("PATHS", detail.paths);
  addMetric("RECALLED", detail.recalled);
  addMetric("QUALITY", (detail.qualities || []).join(" · "));
  card.append(head, metrics);
  return card;
}

function stageNarrative(stage, detail = {}) {
  const list = (values) => (values || []).filter(Boolean).join("、");
  if (stage === "direct_search") {
    const skills = detail.skills?.length ? `识别到技能：${list(detail.skills)}。` : "";
    const cities = detail.cities?.length ? `城市条件：${list(detail.cities)}。` : "";
    return `${skills}${cities}Hybrid V2 parser LLM 会先生成 CandidateSearchIR，再直接调用 SearchTool；不启动岗位理解、规划、判断和反思阶段。`;
  }
  if (stage === "understand") {
    const abilities = detail.mustAbilities?.length
      ? `我锁定了 ${detail.mustAbilities.length} 项必备能力：${list(detail.mustAbilities)}。`
      : "我先读取岗位目标，提取可以检索的能力与条件。";
    const hard = detail.hardConstraints
      ? `同时保留 ${detail.hardConstraints} 项硬条件。`
      : "";
    return `${abilities}${hard}接下来不会只搜一个关键词，而会把这些要求拆成互补路径。`;
  }
  if (stage === "plan") {
    return `我形成了 ${detail.paths || 0} 条检索路径。每条路径承担不同的证据来源，先并行召回，再根据覆盖情况决定是否继续。`;
  }
  if (stage === "execute") {
    return `第 ${Math.max(1, state.executeCount)} 轮检索已发起。我调用 SearchTool 同时执行 ${detail.paths || 0} 条路径，共召回 ${detail.recalled || 0} 人次，现在交给能力判定。`;
  }
  if (stage === "judge") {
    return `我逐个核对候选人的能力证据与硬条件，目前累计 ${detail.qualified || 0} 人通过门槛。接下来检查这些结果是否覆盖岗位的全部要求。`;
  }
  if (stage === "evaluate") {
    if (detail.next === "reflect") {
      return "覆盖检查没有通过：现有候选人还不足以支撑交付。我会进入反思，定位缺口并改写下一轮策略。";
    }
    if (detail.next === "finalize") {
      return "覆盖检查通过，现有候选已经可以交付。我会收敛名单，并把匹配证据整理完整。";
    }
    return `本轮评估触发终止条件${detail.stopReason ? `：${detail.stopReason}` : ""}，不再继续搜索。`;
  }
  if (stage === "reflect") {
    const gaps = detail.gaps?.length ? `当前缺口是：${list(detail.gaps)}。` : "当前能力覆盖仍有缺口。";
    return `我比较了已命中的候选人，发现共同特征还不能覆盖全部要求。${gaps}下一轮需要换一个搜索方向，而不是重复原查询。`;
  }
  if (stage === "revise") {
    const signals = detail.softSignals ? `我把缺口转成 ${detail.softSignals} 个新的软信号，` : "我更新了检索条件，";
    const convergence = detail.converged ? "画像已经收敛，准备再验证一轮。" : "画像尚未收敛，继续进行下一轮验证。";
    return `${signals}并重新排序搜索路径。${convergence}`;
  }
  if (stage === "finalize") {
    return `搜索闭环结束。本轮最终交付 ${detail.total || 0} 位候选人，共收敛 ${detail.searchTotal || 0} 条检索结果，匹配证据与来源路径已经整理完成。`;
  }
  if (stage.startsWith("degrade")) {
    return `执行过程中触发降级${detail.reason ? `：${detail.reason}` : ""}。系统保留了已完成的结果，并记录降级原因。`;
  }
  return "Agent 已记录本阶段结果，并继续判断下一步。";
}

function stageRunDetail(stage, detail = {}) {
  if (stage === "direct_search") return "正在调用 Hybrid V2 parser LLM 生成 CandidateSearchIR。";
  if (stage === "understand") return "正在读取岗位要求，提取能力与硬条件。";
  if (stage === "plan") return `正在形成互补的检索路径${detail.paths ? `，当前 ${detail.paths} 条` : ""}。`;
  if (stage === "execute") return `SearchTool 正在召回候选人${detail.recalled ? `，已返回 ${detail.recalled} 人次` : ""}。`;
  if (stage === "judge") return "正在逐个核对候选人的能力证据与硬条件。";
  if (stage === "evaluate") return "正在判断当前结果是否覆盖全部岗位要求。";
  if (stage === "reflect") return "正在比较已命中候选人，定位还没有覆盖的能力缺口。";
  if (stage === "revise") return "正在把缺口转换为新的搜索信号，并重排下一轮路径。";
  if (stage === "finalize") return "正在收敛结果，整理最终名单与匹配证据。";
  return "正在处理阶段结果，并判断下一步。";
}

function addMission(query, topK) {
  const li = el("li", "agent-task-message");
  li.id = "agent-mission-row";
  const avatar = el("span", "agent-task-avatar", "你");
  const bubble = el("div", "agent-task-bubble");
  const head = el("div", "agent-task-head");
  head.append(el("strong", "", "本轮任务"), el("span", "", "交给 Search Agent"));
  const meta = el("div", "agent-task-meta");
  meta.append(
    el("span", "", `目标交付 ${topK} 人`),
    el("span", "", "自主规划 · 预算受控 · 可追溯"),
  );
  bubble.append(head, el("p", "agent-task-query", query), meta);
  li.append(avatar, bubble);
  timeline.append(li);
  timeline.scrollTop = timeline.scrollHeight;
}

function snapTimelineToLatestCard() {
  requestAnimationFrame(() => {
    const maxScroll = Math.max(0, timeline.scrollHeight - timeline.clientHeight);
    if (!maxScroll) return;
    let target = 0;
    for (const item of timeline.children) {
      if (item.offsetTop <= maxScroll + 1) target = item.offsetTop;
    }
    timeline.scrollTop = Math.min(maxScroll, target);
  });
}

function appendDeliverySummary(payload) {
  const metrics = payload.debug?.agentMetrics || {};
  const directRoute = payload.debug?.difficultyRoute === "direct_search";
  const executionRounds = resolveExecutionRounds(payload);
  const judged = metrics.recall?.judged;
  const overview = deliveryOverview(payload);
  const li = el("li", "agent-delivery");
  const head = el("div", "delivery-head");
  head.append(
    el("span", "delivery-check", "✓"),
    el("div", "delivery-copy"),
  );
  const copy = head.lastElementChild;
  copy.append(
    el(
      "strong",
      "",
      overview.reached
        ? overview.hasHardGap
          ? `候选人名单已交付，${overview.hardAffected} 人需复核`
          : "候选人名单已交付"
        : "候选人名单已交付，目标人数有缺口",
    ),
    el(
      "span",
      "",
      overview.reason || "匹配证据、硬条件与来源路径已经同步到候选人档案。",
    ),
  );
  const stats = el("div", "delivery-stats");
  const addStat = (value, label, warning = false) => {
    const stat = el("span");
    stat.classList.toggle("is-warning", warning);
    stat.append(el("b", "", value), el("small", "", label));
    stats.append(stat);
  };
  addStat(overview.target, "目标");
  addStat(overview.delivered, "交付");
  addStat(overview.gap, "缺口", overview.gap > 0);
  if (overview.hasHardGap) addStat(overview.hardAffected, "硬条件缺口", true);
  if (directRoute) {
    addStat(1, "SearchTool 调用");
    addStat(metrics.meta?.agentLlmCalls ?? 0, "Agent LLM");
    addStat(metrics.meta?.parserLlmCalls ?? metrics.meta?.llmCalls ?? 0, "V2 parser LLM");
  } else {
    addStat(executionRounds, "执行轮次");
    if (judged !== undefined) addStat(judged, "通过判定");
  }
  li.append(head, stats);
  timeline.append(li);
  timeline.scrollTop = timeline.scrollHeight;
}

function addEvent(stage, detail) {
  removeWaiting();
  state.stageCount += 1;

  if (stage === "understand" || stage === "direct_search") {
    buildRail(stageKeysForRoute(detail.difficultyRoute, detail.plannedStages));
    metrics.hidden = false;
    state.routeMeta = {
      intent: detail.requestIntent || "SEARCH",
      intentRule: detail.intentRule || "search",
      difficultyRoute: detail.difficultyRoute,
      plannedStages: detail.plannedStages,
    };
  }
  if (state.routeMeta) {
    updateRouteSummary({ ...state.routeMeta, executedCount: state.stageCount });
  }

  if (stage === "execute") {
    if (state.executeCount >= 1) addRoundDivider(state.executeCount + 1);
    state.executeCount += 1;
  }

  const meta = STAGE_META[stage] || { kind: "EVENT", glyph: "·" };
  const li = el("li", `event event-${stage}`);
  if (stage.startsWith("degrade")) li.classList.add("degrade");
  if (stage === "finalize") li.classList.add("final");

  const marker = el("span", "event-glyph", meta.glyph);
  marker.title = meta.kind;
  const body = el("div", "event-body");
  const head = el("div", "event-head");
  head.append(
    el("span", "stage-name", STAGE_LABEL[stage] || stage),
    el("span", "stage-kind", meta.kind),
    el("code", "", stage),
    el("span", "event-time", `+${formatSeconds(performance.now() - state.startedAt)}`),
  );

  const narration = el("p", "event-narration", stageNarrative(stage, detail));
  const detailWrap = el("div", "event-detail");
  const parts = stageDetail(stage, detail);
  if (parts.length) detailWrap.append(...parts);

  const raw = el("details", "raw");
  const stageLabel = STAGE_LABEL[stage] || stage;
  raw.append(
    summaryWithLabel("raw event", `查看${stageLabel}阶段的原始事件数据`),
    el("pre", "", JSON.stringify(detail, null, 2)),
  );
  detailWrap.append(raw);

  body.append(head, narration, detailWrap);
  li.append(marker, body);
  timeline.append(li);
  timeline.scrollTop = timeline.scrollHeight;

  markRail(stage);
  timelineHint.textContent = `Agent 正在执行 · ${state.stageCount} 个阶段事件`;
  setAgentProgress();
  setAgentState(
    "running",
    `正在${STAGE_LABEL[stage] || stage}`,
    stageRunDetail(stage, detail),
  );
}

function renderSkills(item) {
  const names = (item.skills || []).map((s) => s?.name).filter(Boolean).slice(0, 6);
  return names.map((name) => chip(name, "skill"));
}

function applyCandidateExclusionState(docId) {
  const excluded = state.excludedCandidateIds.has(String(docId));
  for (const card of document.querySelectorAll("[data-candidate-id]")) {
    if (card.dataset.candidateId !== String(docId)) continue;
    card.classList.toggle("is-excluded", excluded);
    const button = card.querySelector(".cand-exclude");
    if (!button) continue;
    const candidateName = button.dataset.candidateName || card.dataset.candidateId || "该候选人";
    button.textContent = excluded ? "恢复" : "排除";
    button.setAttribute("aria-pressed", String(excluded));
    button.setAttribute(
      "aria-label",
      excluded
        ? `恢复 ${candidateName} 到后续检索范围`
        : `排除 ${candidateName}，不再参与后续搜索`,
    );
    button.title = excluded ? "恢复到后续检索范围" : "在后续检索中排除此人";
  }
}

async function toggleCandidateExclusion(docId, button) {
  if (!state.conversationId || !docId) return;
  const excluded = !state.excludedCandidateIds.has(String(docId));
  button.disabled = true;
  try {
    const resp = await fetch(
      `/conversations/${encodeURIComponent(state.conversationId)}/candidates/${encodeURIComponent(docId)}?companyId=1`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ excluded }),
      },
    );
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const memory = await resp.json();
    state.excludedCandidateIds = new Set(memory.excludedCandidateIds || []);
    applyCandidateExclusionState(docId);
    setComposerNote(
      excluded
        ? "已排除该候选人，后续搜索会携带排除条件。"
        : "已恢复该候选人，后续搜索不再自动排除。",
    );
  } catch (err) {
    runHint.classList.add("error");
    runHint.textContent = `更新排除条件失败：${err.message}`;
  } finally {
    button.disabled = false;
  }
}

function formatCandidateRank(rank) {
  const value = Number(rank);
  if (!Number.isFinite(value) || value < 1) return "—";
  return String(Math.trunc(value)).padStart(2, "0");
}

function resumeCacheKey(docId) {
  return `${RESUME_CACHE_PREFIX}${docId}`;
}

function candidateResumeUrl(item, rank, token = state.queryToken) {
  const url = new URL(RESUME_PAGE, location.origin);
  if (item.docId) url.searchParams.set("docId", String(item.docId));
  if (token) url.searchParams.set("token", token);
  if (rank != null) url.searchParams.set("rank", String(rank));
  const returnTo = `${location.pathname}${location.search}${location.hash}`;
  if (returnTo.startsWith("/") && !returnTo.startsWith("//")) {
    url.searchParams.set("returnTo", returnTo);
  }
  return `${url.pathname}${url.search}`;
}

function rememberCandidateForResume(item, rank, token = state.queryToken) {
  if (!item?.docId) return;
  try {
    localStorage.setItem(
      resumeCacheKey(item.docId),
      JSON.stringify({
        version: 2,
        item,
        rank,
        token: token || null,
        cachedAt: Date.now(),
      }),
    );
  } catch (err) {
    // The session token fallback still resolves the resume when storage is unavailable.
  }
}

function bindCandidateResumeLink(link, item, rank, token = state.queryToken) {
  const remember = () => rememberCandidateForResume(item, rank, token);
  link.addEventListener("pointerdown", remember);
  link.addEventListener("click", remember);
  link.addEventListener("keydown", (event) => {
    if (event.key === "Enter") remember();
  });
}

const candidateTooltipContent = new WeakMap();
let candidateTooltipNode = null;
let candidateTooltipTrigger = null;
let candidateTooltipHideTimer = 0;
let candidateTooltipSequence = 0;
let candidateTooltipOpenedByFocus = false;
let candidateTooltipPinned = false;

function ensureCandidateTooltip() {
  if (candidateTooltipNode) return candidateTooltipNode;
  const tooltip = el("div", "candidate-tooltip");
  tooltip.id = "candidate-evidence-tooltip";
  tooltip.setAttribute("role", "tooltip");
  tooltip.hidden = true;
  tooltip.addEventListener("pointerenter", cancelCandidateTooltipHide);
  tooltip.addEventListener("pointerleave", () => {
    if (candidateTooltipTrigger) scheduleCandidateTooltipHide(candidateTooltipTrigger);
  });
  tooltip.addEventListener("focusin", cancelCandidateTooltipHide);
  tooltip.addEventListener("focusout", () => {
    if (candidateTooltipTrigger) scheduleCandidateTooltipHide(candidateTooltipTrigger);
  });
  document.body.append(tooltip);
  candidateTooltipNode = tooltip;
  return tooltip;
}

function cancelCandidateTooltipHide() {
  if (!candidateTooltipHideTimer) return;
  clearTimeout(candidateTooltipHideTimer);
  candidateTooltipHideTimer = 0;
}

function hideCandidateTooltip() {
  cancelCandidateTooltipHide();
  if (candidateTooltipNode) candidateTooltipNode.hidden = true;
  candidateTooltipTrigger = null;
  candidateTooltipOpenedByFocus = false;
  candidateTooltipPinned = false;
}

function scheduleCandidateTooltipHide(trigger) {
  cancelCandidateTooltipHide();
  candidateTooltipHideTimer = window.setTimeout(() => {
    candidateTooltipHideTimer = 0;
    if (candidateTooltipTrigger === trigger) hideCandidateTooltip();
  }, 160);
}

function positionCandidateTooltip(trigger, tooltip, anchorEvent = null) {
  const margin = 12;
  const gap = 16;
  const viewportWidth = document.documentElement.clientWidth;
  const viewportHeight = window.innerHeight;
  const width = tooltip.offsetWidth;
  const height = tooltip.offsetHeight;
  const hasPointer = Boolean(
    anchorEvent &&
      Number.isFinite(anchorEvent.clientX) &&
      Number.isFinite(anchorEvent.clientY),
  );
  let left;
  let top;
  if (hasPointer) {
    left = anchorEvent.clientX + gap;
    top = anchorEvent.clientY + gap;
    if (left + width > viewportWidth - margin) left = anchorEvent.clientX - width - gap;
    if (top + height > viewportHeight - margin) top = anchorEvent.clientY - height - gap;
  } else {
    const box = trigger.getBoundingClientRect();
    left = box.right + gap;
    top = box.top;
    if (left + width > viewportWidth - margin) left = box.left - width - gap;
    if (left < margin) {
      left = box.left;
      top = box.bottom + gap;
      if (top + height > viewportHeight - margin) top = box.top - height - gap;
    }
  }
  left = Math.max(margin, Math.min(left, viewportWidth - width - margin));
  top = Math.max(margin, Math.min(top, viewportHeight - height - margin));
  tooltip.style.left = `${Math.round(left)}px`;
  tooltip.style.top = `${Math.round(top)}px`;
}

function showCandidateTooltip(trigger, anchorEvent = null) {
  const content = candidateTooltipContent.get(trigger);
  if (!content) return;
  cancelCandidateTooltipHide();
  const tooltip = ensureCandidateTooltip();
  if (candidateTooltipTrigger !== trigger || tooltip.firstElementChild !== content) {
    tooltip.replaceChildren(content);
  }
  candidateTooltipTrigger = trigger;
  candidateTooltipPinned = !anchorEvent;
  tooltip.hidden = false;
  positionCandidateTooltip(trigger, tooltip, anchorEvent);
}

function bindCandidateTooltip(trigger) {
  trigger.classList.add("has-candidate-tooltip");
  trigger.tabIndex = 0;
  trigger.addEventListener("pointerenter", (event) => {
    if (event.pointerType && event.pointerType !== "mouse" && event.pointerType !== "pen") return;
    showCandidateTooltip(trigger, event);
  });
  trigger.addEventListener("pointerleave", () => scheduleCandidateTooltipHide(trigger));
  trigger.addEventListener("focusin", () => {
    candidateTooltipOpenedByFocus = true;
    showCandidateTooltip(trigger);
  });
  trigger.addEventListener("focusout", (event) => {
    if (!trigger.contains(event.relatedTarget)) scheduleCandidateTooltipHide(trigger);
  });
  trigger.addEventListener("click", (event) => {
    if (event.target.closest("button, a, summary, input, textarea, select")) return;
    if (!window.matchMedia("(hover: none)").matches) return;
    if (candidateTooltipOpenedByFocus && candidateTooltipTrigger === trigger) {
      candidateTooltipOpenedByFocus = false;
      return;
    }
    if (candidateTooltipTrigger === trigger && candidateTooltipNode && !candidateTooltipNode.hidden) {
      hideCandidateTooltip();
    } else {
      showCandidateTooltip(trigger);
    }
  });
}

document.addEventListener(
  "pointerdown",
  (event) => {
    if (!candidateTooltipNode || candidateTooltipNode.hidden) return;
    if (candidateTooltipNode.contains(event.target) || candidateTooltipTrigger?.contains(event.target)) return;
    hideCandidateTooltip();
  },
  true,
);

window.addEventListener(
  "scroll",
  (event) => {
    if (!candidateTooltipNode || candidateTooltipNode.hidden) return;
    if (candidateTooltipNode.contains(event.target)) return;
    if (candidateTooltipPinned && candidateTooltipTrigger) {
      const box = candidateTooltipTrigger.getBoundingClientRect();
      if (box.bottom < 8 || box.top > window.innerHeight - 8) {
        hideCandidateTooltip();
      } else {
        positionCandidateTooltip(candidateTooltipTrigger, candidateTooltipNode);
      }
      return;
    }
    hideCandidateTooltip();
  },
  true,
);

window.addEventListener("resize", hideCandidateTooltip);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") hideCandidateTooltip();
});

function buildCandidateTooltip(item, rank, explanation, groups, showRawMatchExplanation) {
  const { matchedAbilities, missingAbilities, hardMatches, pathRationale } = groups;
  const scoreText = formatWorkbenchScore(item);
  const hasDetails = Boolean(
    item.summary ||
      matchedAbilities.length ||
      missingAbilities.length ||
      hardMatches.length ||
      pathRationale.length ||
      showRawMatchExplanation,
  );
  if (!hasDetails) return null;

  const content = el("div", "candidate-tooltip-content");
  const head = el("div", "candidate-tooltip-head");
  const title = el("div", "candidate-tooltip-title");
  title.append(
    el(
      "span",
      "candidate-tooltip-kicker",
      `结果 ${formatCandidateRank(rank)} · 匹配依据${scoreText ? ` · 相关度 ${scoreText}` : ""}`,
    ),
    el("strong", "", item.name || item.docId || "候选人"),
  );
  head.append(title);

  const explain = el("div", "explain candidate-tooltip-explain");
  const description = ["匹配依据"];
  if (scoreText) description.push(`相关度 ${scoreText}`);
  if (item.summary) {
    explain.append(el("p", "candidate-tooltip-summary", item.summary));
    description.push(item.summary);
  }
  const addTagSection = (label, values, tone = "") => {
    if (!values.length) return;
    const row = el("div", "explain-tags");
    for (const value of values) row.append(chip(value, tone));
    const section = el("section", "candidate-tooltip-section");
    section.append(el("h4", "", label), row);
    explain.append(section);
    description.push(`${label}：${values.join("、")}`);
  };
  const addListSection = (label, entries) => {
    if (!entries.length) return;
    const list = el("ul");
    for (const entry of entries) list.append(el("li", entry.tone || "", entry.text));
    const section = el("section", "candidate-tooltip-section");
    section.append(el("h4", "", label), list);
    explain.append(section);
    description.push(`${label}：${entries.map((entry) => entry.text).join("；")}`);
  };

  addTagSection("命中能力", matchedAbilities, "is-match");
  addTagSection("未覆盖能力", missingAbilities, "is-missing");
  addListSection(
    "来源路径",
    pathRationale.map((rationale) => ({ text: rationale })),
  );
  addListSection(
    "硬条件",
    hardMatches.map((hard) => {
      const state = hardConditionState(hard);
      const expected = formatWorkbenchValues(hardExpectedValues(hard));
      const actual = formatWorkbenchValues(hardActualValues(hard));
      const actualText = actual === "—" ? "" : ` · 当前 ${actual}`;
      return {
        text: `${hard.field || "?"}：期望 ${expected}${actualText} · ${HARD_CONDITION_LABELS[state]}`,
        tone: state === "satisfied" ? "" : "is-warning",
      };
    }),
  );
  if (showRawMatchExplanation) {
    const raw = el("details", "candidate-tooltip-raw");
    raw.append(
      summaryWithLabel("raw matchExplanation", "查看原始匹配解释数据"),
      el("pre", "", JSON.stringify(explanation, null, 2)),
    );
    explain.append(raw);
  }

  content.append(head, explain);
  return { content, description: description.join("。") };
}

function attachCandidateTooltip(
  trigger,
  item,
  rank,
  groups,
  showRawMatchExplanation = false,
) {
  const tooltipDetail = buildCandidateTooltip(
    item,
    rank,
    item?.matchExplanation || {},
    groups,
    showRawMatchExplanation,
  );
  if (!tooltipDetail) return null;
  const description = el("span", "sr-only");
  description.id = `candidate-evidence-${++candidateTooltipSequence}`;
  description.textContent = tooltipDetail.description;
  trigger.setAttribute("aria-describedby", description.id);
  candidateTooltipContent.set(trigger, tooltipDetail.content);
  trigger.append(description);
  bindCandidateTooltip(trigger);
  return tooltipDetail;
}

function renderCandidate(
  item,
  rank,
  { showRawMatchExplanation = false, resumeToken = state.queryToken } = {},
) {
  const li = el("li", "candidate");
  const docId = String(item.docId || "");
  const candidateName = item.name || item.docId || "该候选人";
  const resumeUrl = docId ? candidateResumeUrl(item, rank, resumeToken) : "";
  const excluded = state.excludedCandidateIds.has(docId);
  li.dataset.candidateId = docId;
  li.classList.toggle("is-excluded", excluded);
  const top = el("div", "cand-top");
  const who = el("div", "cand-id");
  const name = el("h3");
  if (resumeUrl) {
    const nameLink = el("a", "candidate-name-link", candidateName);
    nameLink.href = resumeUrl;
    nameLink.target = "_blank";
    nameLink.rel = "noopener noreferrer";
    nameLink.title = "在新页面查看完整简历";
    nameLink.setAttribute("aria-label", `查看 ${item.name || item.docId} 的完整简历（新标签页）`);
    bindCandidateResumeLink(nameLink, item, rank, resumeToken);
    name.append(nameLink);
  } else {
    name.textContent = item.name || item.docId;
  }
  who.append(name);
  const meta = el("p", "cand-meta");
  if (item.docId) meta.append(el("span", "cand-meta-id", item.docId));
  if (item.workYears != null) meta.append(el("span", "", `${item.workYears} 年经验`));
  if (item.currentResidence) meta.append(el("span", "", item.currentResidence));
  if (meta.childElementCount) who.append(meta);
  const scoreText = Number(item.score || 0).toFixed(2);
  const score = el("div", "score-block");
  score.title = "相关度仅作同层参考，不表示通过率或匹配率。";
  score.setAttribute(
    "aria-label",
    `相关度 ${scoreText}，仅作同层参考，不表示通过率或匹配率`,
  );
  score.append(el("span", "score-label", "相关度"), el("span", "score", scoreText));
  const control = el("div", "cand-control");
  control.append(score);
  if (resumeUrl) {
    const resumeLink = el("a", "cand-resume", "简历");
    resumeLink.href = resumeUrl;
    resumeLink.target = "_blank";
    resumeLink.rel = "noopener noreferrer";
    resumeLink.title = "查看完整简历";
    resumeLink.setAttribute("aria-label", `查看 ${item.name || item.docId} 的完整简历（新标签页）`);
    bindCandidateResumeLink(resumeLink, item, rank, resumeToken);
    control.append(resumeLink);
  }
  if (state.conversationId && docId) {
    const excludeButton = el("button", "cand-exclude", excluded ? "恢复" : "排除");
    excludeButton.type = "button";
    excludeButton.dataset.candidateName = candidateName;
    excludeButton.setAttribute("aria-pressed", String(excluded));
    excludeButton.setAttribute(
      "aria-label",
      excluded
        ? `恢复 ${candidateName} 到后续检索范围`
        : `排除 ${candidateName}，不再参与后续搜索`,
    );
    excludeButton.title = excluded ? "恢复到后续检索范围" : "在后续检索中排除此人";
    excludeButton.addEventListener("click", (event) => {
      event.stopPropagation();
      void toggleCandidateExclusion(docId, excludeButton);
    });
    control.append(excludeButton);
  }
  const rankNode = el("span", "rank", formatCandidateRank(rank));
  rankNode.setAttribute("aria-label", `结果排序第 ${rank} 位`);
  top.append(rankNode, who, control);

  const explanation = item.matchExplanation || {};
  const matchedAbilities = (explanation.matchedAbilities || []).filter(Boolean);
  const missingAbilities = (explanation.missingAbilities || []).filter(Boolean);
  const hardMatches = (explanation.hardMatches || []).filter(Boolean);
  const pathRationale = (explanation.pathRationale || []).filter(Boolean);

  const evidence = el("div", "cand-evidence");
  const facts = el("div", "cand-evidence-facts");
  const addFact = (label, value, { tone = "", title = "" } = {}) => {
    const fact = el("span", `cand-evidence-fact ${tone}`.trim());
    fact.append(
      el("span", "cand-evidence-fact-label", label),
      el("b", "", value),
    );
    if (title) fact.title = title;
    facts.append(fact);
  };
  if (matchedAbilities.length) {
    addFact("能力命中", `${matchedAbilities.length} 项`);
  }
  if (hardMatches.length) {
    const hardSummary = summarizeHardConditions(hardMatches);
    const title = hardMatches.map((hard) => formatWorkbenchHard(hard)).join("\n");
    const hardText = hardSummary.violated
      ? `违规 ${hardSummary.violated}${hardSummary.unverified ? ` · 待核实 ${hardSummary.unverified}` : ""}`
      : hardSummary.unverified
        ? `待核实 ${hardSummary.unverified}`
        : "满足";
    addFact("硬条件", hardText, {
      tone: hardSummary.status === "satisfied" ? "" : "is-warning",
      title,
    });
  }
  if (missingAbilities.length) {
    addFact("能力缺口", `${missingAbilities.length} 项`, {
      tone: "is-warning",
      title: missingAbilities.join("\n"),
    });
  }
  if (pathRationale.length) {
    addFact("路径证据", `${pathRationale.length} 条`, { title: pathRationale.join("\n") });
  }
  if (facts.childElementCount) evidence.append(facts);

  const signals = el("div", "cand-signals");
  const appendSignalGroup = (label, abilities, { tagTone = "", groupTone = "" } = {}) => {
    if (!abilities.length) return;
    const group = el("div", `cand-signal-group ${groupTone}`.trim());
    const tags = el("div", "tag-row cand-evidence-tags");
    for (const ability of abilities.slice(0, 3)) {
      tags.append(chip(ability, tagTone));
    }
    if (abilities.length > 3) {
      tags.append(el("span", "cand-evidence-more", `+${abilities.length - 3}`));
    }
    group.append(el("span", "cand-signal-label", label), tags);
    signals.append(group);
  };
  appendSignalGroup("命中能力", matchedAbilities, { tagTone: "is-match", groupTone: "is-match" });
  appendSignalGroup("未覆盖", missingAbilities, { tagTone: "is-missing", groupTone: "is-missing" });

  const skills = renderSkills(item);
  if (skills.length) {
    const skillGroup = el("div", "cand-signal-group cand-skill-group");
    const skillTags = el("div", "tag-row cand-skills");
    skillTags.setAttribute("aria-label", "简历技能");
    skillTags.append(...skills);
    skillGroup.append(el("span", "cand-signal-label", "技能"), skillTags);
    signals.append(skillGroup);
  }
  if (signals.childElementCount) evidence.append(signals);

  li.append(top);
  if (evidence.childElementCount) li.append(evidence);
  attachCandidateTooltip(
    li,
    item,
    rank,
    { matchedAbilities, missingAbilities, hardMatches, pathRationale },
    showRawMatchExplanation,
  );
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
  items.forEach((item, index) => {
    results.append(renderCandidate(item, offset + index + 1, { showRawMatchExplanation: true }));
  });
}

function hideConsoleDelivery() {
  const overview = $("delivery-overview");
  if (overview) {
    overview.hidden = true;
    overview.replaceChildren();
  }
  const note = $("candidate-method-note");
  if (note) note.hidden = true;
}

function renderConsoleDelivery(payload) {
  const overviewNode = $("delivery-overview");
  if (!overviewNode) return;
  const overview = deliveryOverview(payload);
  overviewNode.replaceChildren();
  overviewNode.hidden = false;
  overviewNode.classList.toggle("is-met", overview.reached);
  overviewNode.classList.toggle("is-gap", !overview.reached);
  overviewNode.classList.toggle("is-review", overview.hasHardGap);

  const head = el("div", "delivery-overview-head");
  const copy = el("div");
  copy.append(
    el(
      "strong",
      "",
      overview.hasHardGap
        ? `已交付 ${overview.delivered} 人，${overview.hardAffected} 人需复核`
        : overview.reached
          ? "目标交付已达成"
          : "目标人数尚未达成",
    ),
    el(
      "span",
      "",
      overview.hasHardGap
        ? `名单人数优先保证；硬条件${hardGapBreakdown(overview)}，只做标注与后置，不会删除候选人。`
        : "缺口只表示数量未达标，不代表 Agent 执行失败。",
    ),
  );
  head.append(
    copy,
    el(
      "span",
      "delivery-overview-state",
      overview.hasHardGap ? "需复核" : overview.reached ? "已达成" : "有缺口",
    ),
  );

  const facts = el("div", "delivery-overview-facts");
  const addFact = (value, label) => {
    const fact = el("span");
    fact.append(el("b", "", value), el("small", "", label));
    facts.append(fact);
  };
  addFact(overview.target, "目标");
  addFact(overview.delivered, "交付");
  addFact(overview.gap, "缺口");
  if (overview.hasHardGap) addFact(overview.hardAffected, "硬条件缺口");
  overviewNode.append(head, facts);
  if (overview.reason) {
    const reason = el("p", "delivery-overview-reason");
    reason.append(el("strong", "", "结束原因："), document.createTextNode(overview.reason));
    overviewNode.append(reason);
  }

  const note = $("candidate-method-note");
  if (note) note.hidden = !(payload.items || []).length;
}

function renderResults(payload) {
  const items = payload.items || [];
  const clarification = payload.clarification;

  if (clarification?.needClarification) {
    hideConsoleDelivery();
    results.replaceChildren();
    hidePager();
    resultsEmpty.hidden = true;
    resultsHint.textContent = "需要澄清";
    const card = el("li", "err-card");
    card.append(el("p", "warn-line", "岗位描述需要澄清："));
    const ul = el("ul");
    for (const question of clarification.questions || []) ul.append(el("li", "", question));
    card.append(ul);
    appendClarificationOptions(card, clarification.options || []);
    results.append(card);
    return;
  }

  if (payload.debug?.cancelled) hideConsoleDelivery();
  else renderConsoleDelivery(payload);
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

function setResultsEmptyCopy(title, detail) {
  const titleNode = resultsEmpty.querySelector("strong");
  const detailNode = resultsEmpty.querySelector("span");
  if (titleNode) titleNode.textContent = title;
  if (detailNode) detailNode.textContent = detail;
}

async function loadSessionPage(offset, { updateChat = false } = {}) {
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
  if (updateChat) updateChatPager();
  try {
    const resp = await fetch(
      `/sessions/${encodeURIComponent(state.queryToken)}?offset=${offset}&limit=${state.pageLimit}&companyId=1`,
    );
    if (resp.status === 404) throw new Error("该分页会话已过期，请重新运行搜索。");
    if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
    const payload = await resp.json();
    renderCandidateList(payload.items || [], Number(payload.offset || 0));
    if (updateChat) replaceChatCandidateCards(payload.items || [], Number(payload.offset || 0));
    resultsEmpty.hidden = (payload.items || []).length > 0;
    updatePager(payload);
    if (updateChat) updateChatPager(payload);
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
    if (updateChat) updateChatPager();
  }
}

function setMetric(id, value) {
  $(id).textContent = value ?? "—";
}

function formatSeconds(milliseconds) {
  const value = Number(milliseconds);
  if (!Number.isFinite(value) || value < 0) return "—";
  return `${(value / 1000).toFixed(1)} s`;
}

function renderMetrics(payload) {
  const m = payload.debug?.agentMetrics;
  metrics.hidden = false;
  updateRouteSummary({
    intent: payload.debug?.requestIntent || "SEARCH",
    intentRule: payload.debug?.intentRule || "search",
    difficultyRoute: payload.debug?.difficultyRoute,
    plannedStages: payload.debug?.plannedStages,
    executedStages: payload.debug?.executedStages,
  });
  if (!m) {
    setMetric("m-calls", "—");
    setMetric("m-tokens", "—");
    const executionRounds = resolveExecutionRounds(payload);
    setMetric("m-rounds", executionRounds > 0 ? executionRounds : "—");
    setMetric("m-iterations", "—");
    setMetric("m-elapsed", formatSeconds(performance.now() - state.startedAt));
    setMetric("m-judged", payload.total ?? "—");
    setMetric("m-degraded", "—");
    return;
  }
  const meta = m.meta || {};
  const recall = m.recall || {};
  setMetric("m-calls", meta.llmCalls);
  setMetric(
    "m-tokens",
    `${meta.tokensEstimated ? "≈" : ""}${Number(meta.totalTokens || 0).toLocaleString()}`,
  );
  setMetric("m-rounds", resolveExecutionRounds(payload));
  setMetric("m-iterations", resolveReflectionIterations(payload));
  setMetric("m-elapsed", formatSeconds(meta.elapsedMs));
  setMetric("m-judged", `${recall.judged ?? "—"} / ${recall.target ?? "—"}`);
  setMetric("m-degraded", meta.degradedReason || "无");
}

function renderTrace(payload) {
  const debug = payload.debug;
  if (!debug) return;
  traceJson.textContent = JSON.stringify(
    {
      agentTrace: debug.agentTrace,
      agentMetrics: debug.agentMetrics,
      degradedReason: debug.degradedReason,
      finalizeReason: debug.finalizeReason,
      requestIntent: debug.requestIntent,
      intentRule: debug.intentRule,
      difficultyRoute: debug.difficultyRoute,
      plannedStages: debug.plannedStages,
      executedStages: debug.executedStages,
    },
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

function showError(message, title = "执行失败") {
  runHint.classList.add("error");
  runHint.textContent = message;
  setAgentState("error", title, message);
  const card = el("li", "err-card", message);
  results.append(card);
  resultsEmpty.hidden = true;
  hideConsoleDelivery();
}

const INTENT_LABEL = {
  SEARCH: "招聘搜索",
  GREETING: "问候",
  IDENTITY: "身份说明",
  HELP: "能力说明",
  OUT_OF_SCOPE: "范围说明",
};

function renderAssistantReply(payload) {
  results.replaceChildren();
  hideConsoleDelivery();
  resultsEmpty.hidden = false;
  setResultsEmptyCopy("本次未启动检索", "这条输入由意图路由直接回复，不会调用搜索工具或生成候选人名单。");
  metrics.hidden = true;
  traceWrap.hidden = true;
  resultsHint.textContent = "未启动检索";

  const card = el("li", "assistant-card agent-reply-card");
  const head = el("div", "assistant-head");
  const title = el("div", "assistant-title");
  title.append(
    el("strong", "", "Search Agent"),
    el("span", "assistant-intent", INTENT_LABEL[payload.intent] || payload.intent || "提示"),
  );
  head.append(el("span", "assistant-mark", "SA"), title);
  card.append(head, el("p", "assistant-message", payload.message || ""));
  card.append(el("p", "assistant-routing", "意图路由直接回复 · 未启动 Harness · 未调用 LLM"));

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
  timeline.append(card);
  timeline.scrollTop = timeline.scrollHeight;
}

async function runSearch(query, topK) {
  if (state.running) return;
  resetUI();
  setAgentResultMode(false);
  queryInput.value = query;
  chatQueryInput.value = query;
  topKInput.value = topK;
  chatTopKInput.value = topK;
  chatIntro.hidden = true;
  state.runEvents = [];
  state.lastResult = null;
  appendChatUserMessage(query);
  state.activeRun = {
    query,
    topK,
    startedAt: performance.now(),
    pending: appendChatPending(),
    chatRun: null,
  };
  setRunning(true);
  state.startedAt = performance.now();
  timelineHint.textContent = "连接中…";
  setAgentState("running", "正在接管任务", "Agent 正在解析岗位简报并准备检索策略。");
  announceAgentStatus("Search Agent 已开始分析招聘需求。");
  addMission(query, topK);
  addWaiting();

  const controller = new AbortController();
  state.controller = controller;
  let sawResult = false;
  let sawReply = false;
  let sawError = false;
  let sawCancelled = false;
  let sawClarification = false;

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
    if (!resp.ok || !resp.body) {
      let payload = null;
      try {
        payload = await resp.json();
      } catch (err) {
        payload = null;
      }
      const error = new Error(`HTTP ${resp.status} ${resp.statusText}`);
      error.status = resp.status;
      error.busyDetail = payload?.detail || null;
      throw error;
    }

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
          state.runEvents.push({
            stage: payload.stage,
            detail: payload.detail || {},
            at: Math.round(performance.now() - state.startedAt),
          });
          addChatStage(payload.stage, payload.detail || {});
        } else if (event === "reply") {
          sawReply = true;
          if (payload.conversationId) {
            state.conversationId = payload.conversationId;
            state.conversationTurns = Number(payload.conversationTurns || 1);
            saveConversationState();
            syncConversationUrl();
            rememberConversationTurn(query, 0);
            void loadConversations();
          }
          removeWaiting();
          document.getElementById("agent-mission-row")?.remove();
          renderAssistantReply(payload);
          renderChatReply(payload);
          timelineHint.textContent = "已回复 · Agent 未启动";
          setAgentState(
            "routed",
            "未启动 Agent",
            payload.conversationId
              ? "意图路由直接回复；本次对话已保存，可在左侧最近会话中恢复。"
              : "意图路由直接回复，本次没有调用模型或检索工具。",
          );
        } else if (event === "result") {
          sawResult = true;
          sawCancelled = Boolean(payload.debug && payload.debug.cancelled);
          const needsClarification = Boolean(payload.clarification?.needClarification);
          sawClarification = needsClarification;
          if (payload.conversationId) {
            state.conversationId = payload.conversationId;
            state.conversationTurns = Number(payload.conversationTurns || state.conversationTurns || 1);
            saveConversationState();
            syncConversationUrl();
          }
          state.queryToken = payload.queryToken || null;
          state.lastResult = payload;
          removeWaiting();
          renderResults(payload);
          renderMetrics(payload);
          renderTrace(payload);
          renderChatResult(payload);
          if (!sawCancelled && !needsClarification) appendDeliverySummary(payload);
          if (!sawCancelled && !needsClarification) completeRail();
          if (!sawCancelled && !needsClarification) snapTimelineToLatestCard();
          if (!sawCancelled && !needsClarification) {
            rememberConversationTurn(query, Number(payload.total || 0));
          }
          if (!sawCancelled) void loadConversations();
          timelineHint.textContent = sawCancelled
            ? "已取消 · 未产出部分结果"
            : needsClarification
              ? "等待补充 · 未启动检索"
              : `完成 · ${state.stageCount} 个阶段事件 · 执行 ${resolveExecutionRounds(payload)} 轮 · 反思 ${resolveReflectionIterations(payload)} 次`;
          setAgentProgress();
          const overview = deliveryOverview(payload);
          const hardGapNotice = overview.hasHardGap
            ? `其中硬条件${hardGapBreakdown(overview)}，均已保留在名单中并按组后置。`
            : "";
          setAgentState(
            sawCancelled
              ? "stopped"
              : needsClarification
                ? "clarify"
                : payload.debug?.degradedReason
                  ? "degraded"
                  : "completed",
            sawCancelled
              ? "已请求停止"
              : needsClarification
                ? "等待补充需求"
                : payload.debug?.degradedReason
                  ? "本轮完成（降级）"
                  : "本轮完成",
            sawCancelled
              ? "当前阶段结束后会停止后续检索与模型调用。"
              : needsClarification
                ? "当前输入缺少岗位方向；选择或补充一条更具体的招聘需求后继续。"
                : payload.debug?.degradedReason
                  ? `已交付 ${Number(payload.total || 0)} 位候选人；执行过程中触发降级：${payload.debug.degradedReason}${hardGapNotice ? ` ${hardGapNotice}` : ""}`
                  : overview.hasHardGap
                    ? `已交付 ${Number(payload.total || 0)} 位候选人，${hardGapNotice}`
                    : `已交付 ${Number(payload.total || 0)} 位候选人，匹配证据与来源路径已同步。`,
          );
          announceAgentStatus(
            sawCancelled
              ? "已请求停止，当前阶段结束后会停止后续工作。"
              : needsClarification
                ? "需要补充岗位方向后继续检索。"
                : `检索完成。目标 ${overview.target} 人，实际交付 ${overview.delivered} 人，缺口 ${overview.gap} 人。${overview.hasHardGap ? `硬条件${hardGapBreakdown(overview)}，需要复核。` : ""}${payload.debug?.degradedReason ? "本轮执行发生降级，请复核结果。" : ""}`,
          );
        } else if (event === "error") {
          sawError = true;
          showError(`Agent 执行失败：${payload.message || "unknown error"}`);
          renderChatError(`Agent 执行失败：${payload.message || "unknown error"}`);
        }
      }
    }
    if (!sawResult && !sawReply && !sawError && state.running) {
      showError("连接已结束，但没有收到最终结果。");
      renderChatError("连接已结束，但没有收到最终结果。");
    }
    if (sawResult && sawCancelled) {
      runHint.textContent = "已取消。后端会在当前阶段结束后停止后续工作。";
    } else if (sawClarification) {
      runHint.textContent = "等待补充需求。选择推荐方向或补充岗位信息后再次运行。";
    } else if (sawReply) {
      runHint.textContent = "已回复。此请求不需要启动搜索 Agent。";
    } else if (sawResult) {
      runHint.textContent = "完成。可修改需求后重新运行。";
    }
  } catch (err) {
    if (err.name === "AbortError") {
      runHint.textContent = "已请求停止：当前阶段结束后不会再发起新的检索或模型调用。";
      setAgentState("stopped", "正在停止", "当前阶段结束后不会再发起新的检索或模型调用。");
      announceAgentStatus("已请求停止，当前阶段结束后会停止后续工作。");
      if (state.activeRun?.chatRun) {
        setChatStatus(state.activeRun.chatRun.status, "停止中", "stopped");
      }
    } else {
      if (err?.busyDetail?.code === "server_busy") {
        const retryAfter = Number(err.busyDetail.retryAfterSec || 0);
        const message = retryAfter > 0
          ? `当前搜索并发已满，请 ${retryAfter} 秒后重试。`
          : "当前搜索并发已满，请稍后重试。";
        showError(message, "服务繁忙");
        renderChatError(message, "服务繁忙");
      } else {
        showError(`请求失败：${err.message}`);
        renderChatError(`请求失败：${err.message}`);
      }
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

function startNewConversation() {
  if (state.running) return;
  resetConversation();
  resetUI();
  resetChatView();
  timelineEmpty.hidden = false;
  resultsEmpty.hidden = false;
  runHint.classList.remove("error");
  runHint.textContent = "已开启新会话。下一条需求不会引用之前的上下文。";
  if (state.viewMode === "agent") chatQueryInput.focus();
  else queryInput.focus();
}

async function loadHealth() {
  try {
    const resp = await fetch("/healthz", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const health = await resp.json();
    applyRuntimeHealth(health);
  } catch (err) {
    state.runtimeMode = null;
    state.onlineConfigured = false;
    renderRuntimeMode({ healthy: false });
  }
}

function initViewMode() {
  const params = new URLSearchParams(location.search);
  let saved = null;
  try {
    saved = sessionStorage.getItem(VIEW_KEY);
  } catch (err) {
    // Default to the conversational view when storage is unavailable.
  }
  const requested = params.get("view") || saved || "agent";
  setViewMode(requested, { persist: false });
}

function readSidebarPreference() {
  try {
    return localStorage.getItem(SIDEBAR_KEY) !== "hidden";
  } catch (err) {
    return true;
  }
}

function sidebarIsVisible() {
  if (!agentView) return false;
  return sidebarMediaQuery.matches
    ? agentView.classList.contains("is-sidebar-open")
    : !agentView.classList.contains("is-sidebar-collapsed");
}

function setSidebarVisible(visible, { persist = true, moveFocus = false } = {}) {
  const mobile = sidebarMediaQuery.matches;
  const expanded = Boolean(visible);
  const collapsed = !mobile && !expanded;
  if (agentView) {
    agentView.classList.toggle("is-sidebar-collapsed", collapsed);
    agentView.classList.toggle("is-sidebar-open", mobile && expanded);
  }
  if (agentSidebar) {
    const hidden = mobile && !expanded;
    agentSidebar.setAttribute("aria-hidden", String(hidden));
    if ("inert" in agentSidebar) agentSidebar.inert = hidden;
  }
  if (agentMain) {
    const hiddenFromInteraction = mobile && expanded;
    if ("inert" in agentMain) agentMain.inert = hiddenFromInteraction;
    if (hiddenFromInteraction) agentMain.setAttribute("aria-hidden", "true");
    else agentMain.removeAttribute("aria-hidden");
  }
  if (sidebarContent) {
    sidebarContent.setAttribute("aria-hidden", String(collapsed));
    if ("inert" in sidebarContent) sidebarContent.inert = collapsed;
  }
  if (sidebarToggle) {
    const label = expanded ? "收起最近会话" : "展开最近会话";
    sidebarToggle.setAttribute("aria-expanded", String(expanded));
    sidebarToggle.setAttribute("aria-label", label);
    sidebarToggle.title = label;
  }
  if (sidebarToggleLabel) {
    sidebarToggleLabel.textContent = expanded ? "收起会话" : "展开会话";
  }
  if (sidebarScrim) {
    sidebarScrim.hidden = !(mobile && expanded);
  }
  if (persist && !mobile) {
    try {
      localStorage.setItem(SIDEBAR_KEY, expanded ? "shown" : "hidden");
    } catch (err) {
      // Sidebar preference is optional.
    }
  }
  if (moveFocus) {
    if (expanded) requestAnimationFrame(() => sidebarNewBtn?.focus());
    else if (mobile) sidebarToggle?.focus();
    else requestAnimationFrame(() => sidebarRailToggle?.focus());
  }
}

function initSidebarState() {
  const syncForViewport = () => {
    setSidebarVisible(
      sidebarMediaQuery.matches ? false : readSidebarPreference(),
      { persist: false },
    );
  };
  syncForViewport();
  if (typeof sidebarMediaQuery.addEventListener === "function") {
    sidebarMediaQuery.addEventListener("change", syncForViewport);
  } else {
    sidebarMediaQuery.addListener(syncForViewport);
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

  chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const query = chatQueryInput.value.trim();
    if (!query) {
      chatQueryInput.focus();
      setComposerNote("请先描述目标岗位或需要的能力。", { error: true });
      return;
    }
    setComposerNote(defaultComposerNote());
    const topK = Math.max(1, Math.min(50, Number(chatTopKInput.value) || 20));
    chatTopKInput.value = topK;
    runSearch(query, topK);
    chatQueryInput.value = "";
  });

  chatQueryInput.addEventListener("input", () => {
    if (composerNote.classList.contains("is-error")) setComposerNote(defaultComposerNote());
  });

  chatQueryInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      chatForm.requestSubmit();
    }
  });

  chatStopBtn.addEventListener("click", () => {
    if (!state.controller) return;
    chatStopBtn.disabled = true;
    runHint.classList.remove("error");
    runHint.textContent = "正在停止：等待当前阶段结束…";
    state.controller.abort();
  });

  for (const button of document.querySelectorAll(".example")) {
    button.addEventListener("click", () => {
      queryInput.value = button.dataset.q || "";
      queryInput.focus();
    });
  }

  for (const button of document.querySelectorAll(".chat-example")) {
    button.addEventListener("click", () => {
      chatQueryInput.value = button.dataset.q || "";
      chatQueryInput.focus();
      scrollChatToEnd("smooth");
    });
  }

  viewAgentBtn.addEventListener("click", () => setViewMode("agent"));
  viewConsoleBtn.addEventListener("click", () => {
    if (sidebarMediaQuery.matches) setSidebarVisible(false);
    setViewMode("console");
  });
  modeChip.addEventListener("click", toggleRuntimeMode);

  newConversationBtn.addEventListener("click", startNewConversation);
  chatNewBtn.addEventListener("click", startNewConversation);
  sidebarNewBtn?.addEventListener("click", () => {
    startNewConversation();
    if (sidebarMediaQuery.matches) setSidebarVisible(false);
  });
  sidebarToggle?.addEventListener("click", () => {
    setSidebarVisible(!sidebarIsVisible(), { moveFocus: true });
  });
  sidebarCollapse?.addEventListener("click", () => {
    setSidebarVisible(false, { moveFocus: true });
  });
  sidebarRailToggle?.addEventListener("click", () => {
    setSidebarVisible(true, { moveFocus: true });
  });
  sidebarScrim?.addEventListener("click", () => {
    setSidebarVisible(false, { moveFocus: true });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sidebarMediaQuery.matches && sidebarIsVisible()) {
      setSidebarVisible(false, { moveFocus: true });
    }
  });

  prevPageBtn.addEventListener("click", () => {
    loadSessionPage(Math.max(0, state.pageOffset - state.pageLimit));
  });

  nextPageBtn.addEventListener("click", () => {
    if (state.pageHasMore) loadSessionPage(state.pageOffset + state.pageLimit);
  });

  compareCloseBtn?.addEventListener("click", () => compareDialog?.close());
  compareViewButtons.forEach((button) => {
    button.addEventListener("click", () => setCompareView(button.dataset.compareView));
  });
  compareDialog?.addEventListener("click", (event) => {
    if (event.target === compareDialog) compareDialog.close();
  });
  compareDialog?.addEventListener("close", () => {
    compareDialogTrigger?.focus?.();
    compareDialogTrigger = null;
    compareModel = null;
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

async function initializeConversationState() {
  await loadConversations();
  if (state.conversationId) await restoreConversation(state.conversationId);
}

loadConversationState();
buildRail();
renderConversation();
initSidebarState();
bindEvents();
initViewMode();
initFromUrl();
loadHealth();
void initializeConversationState();
