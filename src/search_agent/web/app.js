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
const CONVERSATION_KEY = "search-agent:conversation";
const VIEW_KEY = "search-agent:view";
const SIDEBAR_KEY = "search-agent:sidebar";
const RESUME_PAGE = "/resume.html";
const RESUME_CACHE_PREFIX = "search-agent:candidate:";
const FINALIZE_REASON_COPY = Object.freeze({
  target_reached: "达到目标人数，搜索提前收敛。",
  max_iterations: "达到迭代轮数上限，交付当前最优结果。",
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

const state = {
  viewMode: "agent",
  runtimeMode: null,
  onlineConfigured: false,
  modeSwitching: false,
  running: false,
  controller: null,
  startedAt: 0,
  activeRun: null,
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
const modeChip = $("mode-chip");
const modeLabel = $("mode-label");
const onlineModeDialog = $("online-mode-dialog");

function defaultComposerNote() {
  return state.runtimeMode === "offline" || !state.runtimeMode
    ? "当前快速演示：脚本化 LLM + 16 人内存库，无需 API Key · Enter 发送，Shift+Enter 换行"
    : "当前完整在线：在线 LLM + Hybrid V2 + Elasticsearch 候选人库 · Enter 发送，Shift+Enter 换行";
}

function setComposerNote(text, { error = false } = {}) {
  composerNote.textContent = text;
  composerNote.classList.toggle("is-error", error);
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

function deliveryOverview(payload) {
  const target = Math.max(0, Number(payload.topK ?? state.activeRun?.topK ?? 0));
  const delivered = Math.max(0, Number(payload.total || 0));
  const gap = Math.max(0, target - delivered);
  const reached = target > 0 && delivered >= target;
  const reasonCode = payload.debug?.finalizeReason || "";
  return {
    target,
    delivered,
    gap,
    reached,
    reasonCode,
    reason: FINALIZE_REASON_COPY[reasonCode] || "",
  };
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

  const details = el("details", "chat-run-details");
  const detailsSummary = el("summary");
  detailsSummary.append(el("span", "", "Agent 执行详情"), el("small", "", "阶段 · 路径 · Trace"));
  const detailsBody = el("div", "chat-details-body");
  details.append(detailsSummary, detailsBody);

  message.body.append(criteriaCard, progress, details);
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
    details,
    detailsSummary,
    detailsBody,
    resultArticle: null,
  };
  state.activeRun.chatRun = run;
  scrollChatToEnd();
  return run;
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
  rebuildChatDetails(run);
  scrollChatToEnd();
}

function rebuildChatDetails(run) {
  const body = run.detailsBody;
  body.replaceChildren();

  if (state.lastResult?.debug?.agentMetrics) {
    const metrics = state.lastResult.debug.agentMetrics;
    const directRoute = state.lastResult.debug.difficultyRoute === "direct_search";
    const meta = metrics.meta || {};
    const rounds = metrics.rounds || {};
    const recall = metrics.recall || {};
    const stats = el("div", "chat-detail-metrics");
    const add = (label, value) => {
      const stat = el("span");
      stat.append(el("b", "", value ?? "—"), el("small", "", label));
      stats.append(stat);
    };
    add(directRoute ? "Agent LLM" : "LLM 调用", directRoute ? meta.agentLlmCalls ?? 0 : meta.llmCalls);
    if (directRoute) add("V2 parser", meta.parserLlmCalls ?? meta.llmCalls ?? 0);
    add("Tokens", `${meta.tokensEstimated ? "≈" : ""}${Number(meta.totalTokens || 0).toLocaleString()}`);
    add(directRoute ? "工具调用" : "迭代轮次", directRoute ? 1 : rounds.iterations);
    add("耗时", formatSeconds(meta.elapsedMs));
    add(directRoute ? "SearchTool" : "通过判定", directRoute ? 1 : recall.judged ?? "—");
    body.append(stats);
  }

  const timeline = el("ol", "chat-detail-timeline");
  for (const event of state.runEvents) {
    const item = el("li");
    const head = el("div", "chat-detail-stage");
    head.append(
      el("strong", "", STAGE_LABEL[event.stage] || event.stage),
      el("code", "", event.stage),
      el("span", "", `+${formatSeconds(event.at)}`),
    );
    item.append(head, el("p", "", chatStageCopy(event.stage, event.detail)));
    const parts = stageDetail(event.stage, event.detail);
    if (parts.length) {
      const facts = el("div", "chat-detail-facts");
      facts.append(...parts);
      item.append(facts);
    }
    const raw = el("details", "chat-raw");
    raw.append(
      el("summary", "", "raw event"),
      el("pre", "", JSON.stringify(event.detail, null, 2)),
    );
    item.append(raw);
    timeline.append(item);
  }
  body.append(timeline);

  if (state.lastResult?.debug) {
    const debug = state.lastResult.debug;
    const raw = el("details", "chat-raw chat-raw-trace");
    raw.append(
      el("summary", "", "原始 agentTrace / agentMetrics"),
      el("pre", "", JSON.stringify({
        agentTrace: debug.agentTrace,
        agentMetrics: debug.agentMetrics,
        degradedReason: debug.degradedReason,
      }, null, 2)),
    );
    body.append(raw);
  }
  run.detailsSummary.querySelector("small").textContent =
    `${state.runEvents.length} 个阶段事件 · 路径 · Trace`;
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
  const run = state.activeRun?.chatRun;
  if (!run?.resultList) return;
  run.resultList.replaceChildren();
  items.forEach((item, index) => {
    const card = renderCandidate(item, offset + index + 1);
    card.classList.add("chat-candidate");
    run.resultList.append(card);
  });
}

function updateChatPager(payload = {}) {
  const run = state.activeRun?.chatRun;
  if (!run?.resultPager) return;
  const totalPages = Number(payload.totalPages || payload.page?.totalPages || 1);
  const currentPage = Number(payload.currentPage || payload.page?.currentPage || 1);
  const total = Number(payload.total ?? payload.page?.total ?? 0);
  const hasMore = Boolean(payload.hasMore ?? payload.page?.hasMore);
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

function appendChatDeliveryOverview(container, payload) {
  const overview = deliveryOverview(payload);
  const section = el("section", "chat-delivery-overview");
  section.classList.toggle("is-met", overview.reached);
  section.classList.toggle("is-gap", !overview.reached && overview.gap > 0);

  const head = el("div", "chat-delivery-head");
  const title = el("div");
  title.append(
    el("strong", "", overview.reached ? "目标交付已达成" : "本次未达到目标人数"),
    el("span", "", "目标按你设置的交付人数计算；缺口只表示数量未达标，不代表 Agent 执行失败。"),
  );
  head.append(title, el("span", "chat-delivery-state", overview.reached ? "已达成" : "有缺口"));

  const facts = el("div", "chat-delivery-facts");
  const addFact = (value, label) => {
    const fact = el("span");
    fact.append(el("b", "", value), el("small", "", label));
    facts.append(fact);
  };
  addFact(overview.target, "目标人数");
  addFact(overview.delivered, "实际交付");
  addFact(overview.gap, "人数缺口");

  section.append(head, facts);
  if (overview.reason) {
    const reason = el("p", "chat-delivery-reason");
    reason.append(el("strong", "", "结束原因："), document.createTextNode(overview.reason));
    section.append(reason);
  }
  container.append(section);
}

function reasoningStep(kicker, copy) {
  const item = el("li");
  item.append(el("span", "", kicker), el("p", "", copy));
  return item;
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
    add(Number(payload.total || 0), "交付候选人");
    if (directRoute) {
      add(1, "SearchTool 调用");
      add(metrics.meta?.agentLlmCalls ?? 0, "Agent LLM");
      add(metrics.meta?.parserLlmCalls ?? metrics.meta?.llmCalls ?? 0, "V2 parser LLM");
    } else {
      add(metrics.rounds?.iterations ?? Math.max(1, state.executeCount), "检索轮次");
      add(metrics.recall?.judged ?? "—", "通过判定");
    }
    message.body.append(stats);
  }
  if (degraded) {
    const note = el("p", "chat-degrade-note");
    note.append(el("strong", "", "降级原因："), document.createTextNode(degradedReason));
    message.body.append(note);
  }

  if (clarification) {
    const clarify = el("div", "chat-clarification");
    clarify.append(el("strong", "", "还需要补充一些信息"));
    const list = el("ul");
    for (const question of payload.clarification.questions || []) list.append(el("li", "", question));
    clarify.append(list);
    appendClarificationOptions(clarify, payload.clarification.options || []);
    message.body.append(clarify);
  } else {
    if (!cancelled) {
      appendChatDeliveryOverview(message.body, payload);
      appendChatReasoning(message.body, payload);
    }
    const items = payload.items || [];
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
  rebuildChatDetails(run);
  scrollChatToEnd();
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
  scrollChatToEnd();
}

function renderChatError(message) {
  state.activeRun?.pending?.remove();
  const run = state.activeRun?.chatRun;
  if (run) setChatStatus(run.status, "失败", "failed");
  const chatMessage = createChatMessage("agent", "chat-error-message");
  setChatStatus(chatMessage.status, "执行失败", "failed");
  chatMessage.body.append(
    el("h3", "", "这次执行没有完成"),
    el("p", "", message),
  );
  scrollChatToEnd();
}

function resetChatView() {
  chatTranscript.replaceChildren();
  chatIntro.hidden = false;
  chatQueryInput.value = "";
  state.activeRun = null;
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

  for (const turn of turns) {
    appendChatUserMessage(turn.query || "");
    const message = createChatMessage("agent", "chat-result-message agent-delivery restored-conversation-message");
    const cancelled = turn.status === "cancelled";
    const clarification = Boolean(turn.clarification?.needClarification);
    const routed = turn.status === "reply";
    const tone = routed ? "complete" : cancelled ? "stopped" : clarification ? "clarify" : turn.status === "degraded" ? "degraded" : "complete";
    const label = routed
      ? INTENT_LABEL[turn.route] || "意图回复"
      : cancelled ? "已停止" : clarification ? "需补充" : turn.status === "degraded" ? "已降级" : "已完成";
    setChatStatus(message.status, label, tone);
    message.body.append(
      el("h3", "", routed ? "会话回复" : "历史检索结果"),
      el("p", "chat-result-copy", turn.assistantSummary || "本轮对话已恢复。"),
    );

    if (clarification) {
      const questions = (turn.clarification.questions || []).filter(Boolean);
      if (questions.length) {
        const list = el("ul", "chat-clarification-list");
        for (const question of questions) list.append(el("li", "", question));
        message.body.append(list);
      }
    }

    const items = Array.isArray(turn.resultItems) ? turn.resultItems : [];
    if (items.length) {
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

  const last = turns[turns.length - 1];
  const restoredPayload = {
    query: last.query || "",
    total: Number(last.resultCount || 0),
    topK: Number(last.topK || 0),
    items: Array.isArray(last.resultItems) ? last.resultItems : [],
    clarification: last.clarification || null,
    debug: {},
  };
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
        el("summary", "", "CandidateSearchIR"),
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
  const rounds = metrics.rounds?.iterations ?? state.executeCount;
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
    el("strong", "", overview.reached ? "候选人名单已交付" : "候选人名单已交付，目标人数有缺口"),
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
  if (directRoute) {
    addStat(1, "SearchTool 调用");
    addStat(metrics.meta?.agentLlmCalls ?? 0, "Agent LLM");
    addStat(metrics.meta?.parserLlmCalls ?? metrics.meta?.llmCalls ?? 0, "V2 parser LLM");
  } else {
    addStat(rounds, "检索轮次");
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
  raw.append(el("summary", "", "raw event"), el("pre", "", JSON.stringify(detail, null, 2)));
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
    button.textContent = excluded ? "恢复" : "排除";
    button.setAttribute("aria-pressed", String(excluded));
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
    el("span", "candidate-tooltip-kicker", `结果 ${formatCandidateRank(rank)} · 匹配依据`),
    el("strong", "", item.name || item.docId || "候选人"),
  );
  head.append(title);

  const explain = el("div", "explain candidate-tooltip-explain");
  const description = ["匹配依据"];
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
      const values = Array.isArray(hard.values) ? hard.values.join("/") : hard.values;
      return {
        text: `${hard.field || "?"}: ${values ?? "—"} ${hard.matched ? "✓ 满足" : "! 未满足"}`,
        tone: hard.matched ? "" : "is-warning",
      };
    }),
  );
  if (showRawMatchExplanation) {
    const raw = el("details", "candidate-tooltip-raw");
    raw.append(el("summary", "", "raw matchExplanation"), el("pre", "", JSON.stringify(explanation, null, 2)));
    explain.append(raw);
  }

  content.append(head, explain);
  return { content, description: description.join("。") };
}

function renderCandidate(
  item,
  rank,
  { showRawMatchExplanation = false, resumeToken = state.queryToken } = {},
) {
  const li = el("li", "candidate");
  const docId = String(item.docId || "");
  const resumeUrl = docId ? candidateResumeUrl(item, rank, resumeToken) : "";
  const excluded = state.excludedCandidateIds.has(docId);
  li.dataset.candidateId = docId;
  li.classList.toggle("is-excluded", excluded);
  const top = el("div", "cand-top");
  const who = el("div", "cand-id");
  const name = el("h3");
  if (resumeUrl) {
    const nameLink = el("a", "candidate-name-link", item.name || item.docId);
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
  score.title = "排序分用于同一结果集内排序，不表示绝对通过率。";
  score.setAttribute("aria-label", `排序分 ${scoreText}，用于同一结果集内排序，不表示绝对通过率`);
  score.append(el("span", "score-label", "排序分"), el("span", "score", scoreText));
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
    excludeButton.setAttribute("aria-pressed", String(excluded));
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
    const passed = hardMatches.filter((hard) => hard.matched).length;
    const title = hardMatches
      .map((hard) => {
        const values = Array.isArray(hard.values) ? hard.values.join("/") : hard.values;
        return `${hard.field || "?"}: ${values ?? "—"} ${hard.matched ? "满足" : "未满足"}`;
      })
      .join("\n");
    addFact("硬条件", `${passed}/${hardMatches.length}`, {
      tone: passed < hardMatches.length ? "is-warning" : "",
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
  const tooltipDetail = buildCandidateTooltip(
    item,
    rank,
    explanation,
    { matchedAbilities, missingAbilities, hardMatches, pathRationale },
    showRawMatchExplanation,
  );
  if (tooltipDetail) {
    const description = el("span", "sr-only");
    description.id = `candidate-evidence-${++candidateTooltipSequence}`;
    description.textContent = tooltipDetail.description;
    li.setAttribute("aria-describedby", description.id);
    candidateTooltipContent.set(li, tooltipDetail.content);
    li.append(description);
    bindCandidateTooltip(li);
  }
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

  const head = el("div", "delivery-overview-head");
  const copy = el("div");
  copy.append(
    el("strong", "", overview.reached ? "目标交付已达成" : "目标人数尚未达成"),
    el("span", "", "缺口只表示数量未达标，不代表 Agent 执行失败。"),
  );
  head.append(copy, el("span", "delivery-overview-state", overview.reached ? "已达成" : "有缺口"));

  const facts = el("div", "delivery-overview-facts");
  const addFact = (value, label) => {
    const fact = el("span");
    fact.append(el("b", "", value), el("small", "", label));
    facts.append(fact);
  };
  addFact(overview.target, "目标");
  addFact(overview.delivered, "交付");
  addFact(overview.gap, "缺口");
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
      `/sessions/${encodeURIComponent(state.queryToken)}?offset=${offset}&limit=${state.pageLimit}`,
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
    setMetric("m-iterations", Math.max(1, state.executeCount));
    setMetric("m-elapsed", formatSeconds(performance.now() - state.startedAt));
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

function showError(message) {
  runHint.classList.add("error");
  runHint.textContent = message;
  setAgentState("error", "执行失败", message);
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
              : `完成 · ${state.stageCount} 个阶段事件 · ${Math.max(1, state.executeCount)} 轮`;
          setAgentProgress();
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
                  ? `已交付 ${Number(payload.total || 0)} 位候选人；执行过程中触发降级：${payload.debug.degradedReason}`
                  : `已交付 ${Number(payload.total || 0)} 位候选人，匹配证据与来源路径已同步。`,
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
      if (state.activeRun?.chatRun) {
        setChatStatus(state.activeRun.chatRun.status, "停止中", "stopped");
      }
    } else {
      showError(`请求失败：${err.message}`);
      renderChatError(`请求失败：${err.message}`);
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
