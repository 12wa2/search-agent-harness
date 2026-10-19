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
const STAGE_ORDER = new Map(STAGES.map(([key], i) => [key, i]));
const STAGE_META = {
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

const state = {
  viewMode: "agent",
  runtimeMode: null,
  onlineConfigured: false,
  modeSwitching: false,
  running: false,
  controller: null,
  startedAt: 0,
  activeRun: null,
  lastResult: null,
  runEvents: [],
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
    modeChip.title = healthy ? "正在读取运行模式" : "无法读取运行模式";
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

  let title = `运行模式：${label}。点击切换到${targetLabel}；LLM、检索工具与候选人库会一起切换。`;
  if (state.running) {
    title = `运行模式：${label}。Agent 运行中，完成或停止后可切换。`;
  } else if (offline && !state.onlineConfigured) {
    title = "运行模式：快速演示。完整在线尚未配置；点击查看配置提示。";
  }
  modeChip.title = title;
  modeChip.setAttribute("aria-label", title);
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

async function toggleRuntimeMode() {
  if (!state.runtimeMode || state.running || state.modeSwitching) return;
  const target = state.runtimeMode === "offline" ? "online" : "offline";
  const targetLabel = target === "online" ? "完整在线" : "快速演示";
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

const CHAT_STATUS_TONES = [
  "is-running",
  "is-complete",
  "is-degraded",
  "is-stopped",
  "is-failed",
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
    } catch (err) {
      // View preference is optional.
    }
  }
  if (next === "agent") scrollChatToEnd("auto");
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

function appendChatUserMessage(query, topK) {
  const { body } = createChatMessage("user", "agent-task-message");
  const bubble = el("div", "chat-user-bubble");
  bubble.append(el("p", "", query));
  const meta = el("div", "chat-user-meta");
  meta.append(el("span", "", `目标交付 ${topK} 人`), el("span", "", "招聘需求"));
  bubble.append(meta);
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
  const chips = run.criteriaChips;
  chips.replaceChildren();

  if (detail.jobTitle) chips.append(chip(detail.jobTitle, "criterion-title"));
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
  run.criteriaCopy.textContent = `我会优先寻找${title}方向，并重点核对以下能力与硬性条件。`;
  run.criteriaBadge.textContent = "已完成";
  run.criteriaBadge.classList.add("is-ready");
  run.criteriaCard.classList.add("is-ready");
}

function chatStageCopy(stage, detail = {}) {
  const list = (values) => (values || []).filter(Boolean).join("、");
  if (stage === "understand") return "正在读取岗位描述，提取必备能力、硬条件和可迁移经验。";
  if (stage === "plan") return `正在规划 ${detail.paths || 0} 条互补的搜索路径，避免只依赖一个关键词。`;
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
      ? `定位到尚未覆盖的能力：${list(detail.gaps)}。下一轮会调整搜索策略。`
      : "正在比较已命中候选人，定位仍需补齐的能力缺口。";
  }
  if (stage === "revise") return "已把缺口转换为新的搜索信号，并重新排列下一轮路径。";
  if (stage === "finalize") return `正在收敛结果，本次计划交付 ${detail.total || 0} 位候选人。`;
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
  const progressMeta = el("span", "", "实时");
  progressHead.append(progressTitle, progressMeta);
  const progressList = el("ol", "chat-progress-list");
  progress.append(progressHead, progressList);

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
    progressList,
    details,
    detailsSummary,
    detailsBody,
    resultArticle: null,
  };
  state.activeRun.chatRun = run;
  scrollChatToEnd();
  return run;
}

function addChatStage(stage, detail = {}) {
  const run = beginChatRun();
  if (stage === "understand") renderChatCriteria(run, detail);

  for (const item of run.progressList.querySelectorAll(".chat-progress-item.is-active")) {
    item.classList.remove("is-active");
    item.classList.add("is-done");
  }
  if (stage === "execute" && state.executeCount > 1) {
    run.progressList.append(el("li", "chat-round", `第 ${state.executeCount} 轮检索`));
  }
  const item = el("li", "chat-progress-item is-active");
  const marker = el("span", "chat-progress-marker");
  const copy = el("div", "chat-progress-copy");
  copy.append(
    el("strong", "", STAGE_LABEL[stage] || stage),
    el("span", "", chatStageCopy(stage, detail)),
  );
  item.append(marker, copy);
  run.progressList.append(item);
  if (stage === "finalize") {
    item.classList.remove("is-active");
    item.classList.add("is-done");
  }
  run.progressMeta.textContent = `${state.stageCount} 个阶段事件`;
  run.progressTitle.textContent = stage === "finalize" ? "执行完成" : "Agent 正在执行";
  rebuildChatDetails(run);
  scrollChatToEnd();
}

function rebuildChatDetails(run) {
  const body = run.detailsBody;
  body.replaceChildren();

  if (state.lastResult?.debug?.agentMetrics) {
    const metrics = state.lastResult.debug.agentMetrics;
    const meta = metrics.meta || {};
    const rounds = metrics.rounds || {};
    const recall = metrics.recall || {};
    const stats = el("div", "chat-detail-metrics");
    const add = (label, value) => {
      const stat = el("span");
      stat.append(el("b", "", value ?? "—"), el("small", "", label));
      stats.append(stat);
    };
    add("LLM 调用", meta.llmCalls);
    add("Tokens", `${meta.tokensEstimated ? "≈" : ""}${Number(meta.totalTokens || 0).toLocaleString()}`);
    add("迭代轮次", rounds.iterations);
    add("耗时", meta.elapsedMs ? `${meta.elapsedMs} ms` : "—");
    add("通过判定", recall.judged ?? "—");
    body.append(stats);
  }

  const timeline = el("ol", "chat-detail-timeline");
  for (const event of state.runEvents) {
    const item = el("li");
    const head = el("div", "chat-detail-stage");
    head.append(
      el("strong", "", STAGE_LABEL[event.stage] || event.stage),
      el("code", "", event.stage),
      el("span", "", `+${event.at} ms`),
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
    const rank = card.querySelector(".rank");
    if (rank) rank.textContent = `#${offset + index + 1}`;
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
    const rank = card.querySelector(".rank");
    if (rank) rank.textContent = `#${offset + index + 1}`;
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

function renderChatResult(payload) {
  const run = beginChatRun();
  const cancelled = Boolean(payload.debug?.cancelled);
  const degradedReason =
    payload.debug?.degradedReason || payload.debug?.agentMetrics?.meta?.degradedReason || "";
  const degraded = Boolean(degradedReason);
  const resultTone = cancelled ? "stopped" : degraded ? "degraded" : "complete";
  const resultLabel = cancelled ? "已停止" : degraded ? "已降级" : "已完成";
  setChatStatus(run.status, resultLabel, resultTone);
  run.progressTitle.textContent = cancelled ? "搜索已停止" : "执行完成";
  run.progressMeta.textContent = `${Math.max(1, state.executeCount)} 轮 · ${state.stageCount} 个阶段事件`;

  const message = createChatMessage("agent", "chat-result-message agent-delivery");
  setChatStatus(message.status, resultLabel, resultTone);
  const head = el("div", "chat-result-head");
  const marker = el("span", "chat-result-check", cancelled ? "■" : "✓");
  const heading = el("div");
  heading.append(
    el("h3", "", cancelled ? "本次搜索已停止" : "本次搜索已完成"),
    el("p", "", cancelled
      ? "已保留停止前完成的工作，未继续发起新的检索或模型调用。"
      : degraded
        ? "搜索已完成，但部分阶段触发了降级。以下结果保留，建议结合匹配证据复核。"
        : "以下是根据当前需求筛选出的候选人。展开卡片可查看完整匹配证据与来源路径。"),
  );
  head.append(marker, heading);
  message.body.append(head);

  const metrics = payload.debug?.agentMetrics;
  if (metrics) {
    const stats = el("div", "chat-result-stats");
    const add = (value, label) => {
      const item = el("span");
      item.append(el("b", "", value ?? "—"), el("small", "", label));
      stats.append(item);
    };
    add(Number(payload.total || 0), "交付候选人");
    add(metrics.rounds?.iterations ?? Math.max(1, state.executeCount), "检索轮次");
    add(metrics.recall?.judged ?? "—", "通过判定");
    message.body.append(stats);
  }
  if (degraded) {
    const note = el("p", "chat-degrade-note");
    note.append(el("strong", "", "降级原因："), document.createTextNode(degradedReason));
    message.body.append(note);
  }

  if (payload.clarification?.needClarification) {
    const clarify = el("div", "chat-clarification");
    clarify.append(el("strong", "", "还需要补充一些信息"));
    const list = el("ul");
    for (const question of payload.clarification.questions || []) list.append(el("li", "", question));
    clarify.append(list);
    message.body.append(clarify);
  } else {
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
  setChatStatus(message.status, INTENT_LABEL[payload.intent] || "已回复", "complete");
  message.body.append(el("p", "chat-reply-text", payload.message || ""));
  message.body.append(el("p", "chat-route-note", "意图路由直接回复 · 未启动 Harness · 未调用搜索工具或 LLM"));
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
  conversationLabel.textContent = state.conversationId && round > 1
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

function completeRail() {
  for (const item of document.querySelectorAll(".rail-item")) {
    item.classList.add("hit");
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
  setAgentProgress();
  setAgentState("idle", "Agent 待命", "等待岗位简报；收到后会先拆解能力，再决定从哪里搜。");
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

  if (stage === "understand") {
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
  const rounds = metrics.rounds?.iterations ?? state.executeCount;
  const judged = metrics.recall?.judged;
  const li = el("li", "agent-delivery");
  const head = el("div", "delivery-head");
  head.append(
    el("span", "delivery-check", "✓"),
    el("div", "delivery-copy"),
  );
  const copy = head.lastElementChild;
  copy.append(
    el("strong", "", "候选人名单已交付"),
    el("span", "", "匹配证据、硬条件与来源路径已经同步到候选人档案。"),
  );
  const stats = el("div", "delivery-stats");
  const addStat = (value, label) => {
    const stat = el("span");
    stat.append(el("b", "", value), el("small", "", label));
    stats.append(stat);
  };
  addStat(Number(payload.total || 0), "交付");
  addStat(rounds, "检索轮次");
  if (judged !== undefined) addStat(judged, "通过判定");
  li.append(head, stats);
  timeline.append(li);
  timeline.scrollTop = timeline.scrollHeight;
}

function addEvent(stage, detail) {
  removeWaiting();
  state.stageCount += 1;

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
    el("span", "event-time", `+${Math.round(performance.now() - state.startedAt)} ms`),
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

function renderCandidate(item, rank) {
  const li = el("li", "candidate");
  const top = el("div", "cand-top");
  const meta = [item.docId, item.workYears != null ? `${item.workYears} 年` : null, item.currentResidence]
    .filter(Boolean)
    .join(" · ");
  const who = el("div", "cand-id");
  who.append(el("h3", "", item.name || item.docId), el("p", "", meta));
  const scoreText = Number(item.score || 0).toFixed(2);
  const score = el("div", "score-block");
  score.title = "排序分用于同一结果集内排序，不表示绝对通过率。";
  score.setAttribute("aria-label", `排序分 ${scoreText}，用于同一结果集内排序，不表示绝对通过率`);
  score.append(el("span", "score-label", "排序分"), el("span", "score", scoreText));
  top.append(el("span", "rank", rank), who, score);

  const explanation = item.matchExplanation || {};
  const matchedAbilities = (explanation.matchedAbilities || []).filter(Boolean);
  const hardMatches = (explanation.hardMatches || []).filter(Boolean);
  const pathRationale = (explanation.pathRationale || []).filter(Boolean);

  const evidence = el("div", "cand-evidence");
  const facts = el("div", "cand-evidence-facts");
  if (matchedAbilities.length) {
    facts.append(el("span", "cand-evidence-fact", `命中 ${matchedAbilities.length} 项能力`));
  }
  if (hardMatches.length) {
    const passed = hardMatches.filter((hard) => hard.matched).length;
    const fact = el("span", "cand-evidence-fact", `硬条件 ${passed}/${hardMatches.length}`);
    fact.title = hardMatches
      .map((hard) => {
        const values = Array.isArray(hard.values) ? hard.values.join("/") : hard.values;
        return `${hard.field || "?"}: ${values ?? "—"} ${hard.matched ? "满足" : "未满足"}`;
      })
      .join("\n");
    facts.append(fact);
  }
  if (pathRationale.length) {
    const fact = el("span", "cand-evidence-fact", `${pathRationale.length} 条来源路径`);
    fact.title = pathRationale.join("\n");
    facts.append(fact);
  }
  if (facts.childElementCount) evidence.append(facts);

  const evidenceTags = el("div", "cand-evidence-tags");
  for (const ability of matchedAbilities.slice(0, 3)) {
    evidenceTags.append(chip(ability, "is-match"));
  }
  if (matchedAbilities.length > 3) {
    evidenceTags.append(el("span", "cand-evidence-more", `+${matchedAbilities.length - 3}`));
  }
  if (evidenceTags.childElementCount) evidence.append(evidenceTags);

  const tags = el("div", "tag-row cand-skills");
  tags.setAttribute("aria-label", "简历技能");
  for (const skill of renderSkills(item)) tags.append(skill);

  const details = el("details");
  const summary = el("summary", "", "匹配依据 / 来源路径");
  const explain = el("div", "explain");

  if (item.summary) explain.append(el("p", "", item.summary));
  if (matchedAbilities.length) {
    const row = el("div", "explain-tags");
    for (const ability of matchedAbilities) row.append(chip(ability));
    explain.append(el("p", "", "命中能力"), row);
  }
  if (pathRationale.length) {
    const ul = el("ul");
    for (const rationale of pathRationale) ul.append(el("li", "", rationale));
    if (ul.childElementCount) explain.append(el("p", "", "来源路径"), ul);
  }
  if (hardMatches.length) {
    const ul = el("ul");
    for (const hard of hardMatches) {
      const values = Array.isArray(hard.values) ? hard.values.join("/") : hard.values;
      ul.append(el("li", "", `${hard.field || "?"}: ${values ?? "—"} ${hard.matched ? "✓" : "✗"}`));
    }
    explain.append(el("p", "", "硬条件"), ul);
  }
  const raw = el("details");
  raw.append(el("summary", "", "raw matchExplanation"), el("pre", "", JSON.stringify(explanation, null, 2)));
  explain.append(raw);

  details.append(summary, explain);
  li.append(top);
  if (evidence.childElementCount) li.append(evidence);
  if (tags.childElementCount) li.append(tags);
  li.append(details);
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
  setAgentState("error", "执行失败", message);
  const card = el("li", "err-card", message);
  results.append(card);
  resultsEmpty.hidden = true;
}

const INTENT_LABEL = {
  GREETING: "问候",
  HELP: "能力说明",
  OUT_OF_SCOPE: "范围说明",
};

function renderAssistantReply(payload) {
  results.replaceChildren();
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
  appendChatUserMessage(query, topK);
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
          removeWaiting();
          document.getElementById("agent-mission-row")?.remove();
          renderAssistantReply(payload);
          renderChatReply(payload);
          timelineHint.textContent = "已回复 · Agent 未启动";
          setAgentState("routed", "未启动 Agent", "意图路由直接回复，本次没有调用模型或检索工具。");
        } else if (event === "result") {
          sawResult = true;
          sawCancelled = Boolean(payload.debug && payload.debug.cancelled);
          if (payload.conversationId) {
            state.conversationId = payload.conversationId;
            state.conversationTurns = Number(payload.conversationTurns || state.conversationTurns || 1);
            saveConversationState();
          }
          state.queryToken = payload.queryToken || null;
          state.lastResult = payload;
          removeWaiting();
          renderResults(payload);
          renderMetrics(payload);
          renderTrace(payload);
          renderChatResult(payload);
          if (!sawCancelled) appendDeliverySummary(payload);
          if (!sawCancelled) completeRail();
          if (!sawCancelled) snapTimelineToLatestCard();
          if (!sawCancelled) {
            rememberConversationTurn(query, Number(payload.total || 0));
          }
          timelineHint.textContent = sawCancelled
            ? "已取消 · 未产出部分结果"
            : `完成 · ${state.stageCount} 个阶段事件 · ${Math.max(1, state.executeCount)} 轮`;
          setAgentProgress();
          setAgentState(
            sawCancelled ? "stopped" : payload.debug?.degradedReason ? "degraded" : "completed",
            sawCancelled ? "已请求停止" : payload.debug?.degradedReason ? "本轮完成（降级）" : "本轮完成",
            sawCancelled
              ? "当前阶段结束后会停止后续检索与模型调用。"
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
  viewConsoleBtn.addEventListener("click", () => setViewMode("console"));
  modeChip.addEventListener("click", toggleRuntimeMode);

  newConversationBtn.addEventListener("click", startNewConversation);
  chatNewBtn.addEventListener("click", startNewConversation);

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
initViewMode();
initFromUrl();
loadHealth();
