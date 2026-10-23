#!/usr/bin/env node
// Generate the bilingual architecture overview used by README.md and README.en.md.
// The SVG is intentionally dependency-free so it can be regenerated anywhere.

import fs from "node:fs";
import nodePath from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = nodePath.resolve(nodePath.dirname(fileURLToPath(import.meta.url)), "..");
const docsDir = nodePath.join(repoRoot, "docs");

const C = {
  paper: "#f2f5f0",
  surface: "#fbfcf8",
  white: "#ffffff",
  ink: "#15221d",
  muted: "#596b63",
  quiet: "#74847f",
  rule: "#d3ded7",
  ruleSoft: "#e4ebe5",
  accent: "#126653",
  accentStrong: "#0b4f40",
  accentWash: "#e4f0ea",
  accentLine: "#bcd7cc",
  forest: "#0c2f27",
  forestSoft: "#16473c",
  forestLine: "#27423a",
  forestMuted: "#a9c1b7",
  signal: "#e96d42",
  warningInk: "#7b4e17",
  warningWash: "#fbf2e4",
  warningLine: "#e8cda9",
  infoInk: "#315b78",
  infoWash: "#e9f1f6",
  infoLine: "#c8dbe6",
  neutralWash: "#eef2ec",
  neutralLine: "#d3ded7",
};

const SANS = "Aptos, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif";
const MONO = "Cascadia Mono, Cascadia Code, Consolas, Liberation Mono, monospace";
const W = 1600;
const H = 1040;

const copy = {
  zh: {
    lang: "zh-CN",
    title: "端到端系统架构",
    subtitle: "确定性检索执行 · 受控策略推理 · 全链路可观测",
    desc: "Search Agent Harness 的端到端架构：Web Console、FastAPI、意图路由、自适应 3/5/8 阶段 Harness、SearchTool 协议、Hybrid V2 检索、LLM Gateway、会话与可观测性。",
    stats: [
      ["3 / 5 / 8", "自适应路由"],
      ["2", "工具方法"],
      ["8", "核心阶段"],
    ],
    sections: {
      request: "01 / 请求路径",
      orchestration: "02 / 受控编排",
      retrieval: "03 / 工具与模型",
      evidence: "CROSS-CUTTING / 状态与证据",
    },
    web: {
      title: "Web Console",
      subtitle: "对话、执行观测与会话恢复",
      modes: ["Agent", "Observe", "Sessions"],
      footer: "REST + SSE · zero-build UI",
    },
    requestCard: {
      title: "FastAPI + 路由",
      tag: "HTTP + SSE",
      endpoints: ["POST /search", "POST /search/stream", "GET /conversations", "GET /sessions/:token"],
      footer: "permission-scoped request context",
      routerTitle: "Intent & Entry Router",
      routerSub: "规则优先 · 可选入口复核",
      branches: [
        ["reply", "问候 / 帮助 / 明确无关任务"],
        ["SEARCH", "IntentRouter → EntryRouter"],
        ["route", "direct_search  |  agent"],
      ],
      routerFooter: "IntentRouter 本身不访问 SearchTool",
    },
    scope: {
      title: "Request context",
      subtitle: "每次检索绑定同一作用域",
      chips: ["tenant", "permission", "L1 history", "L3 exclusions"],
      footer: "可选 fail-closed 校验",
    },
    orchestration: {
      title: "Search Agent Harness",
      subtitle: "显式状态机 · 预算控制 · 阶段级降级",
      routes: ["DIRECT · 3", "LIGHT · 5", "REFLECTIVE · 8"],
      routeNote: "按请求复杂度选择",
      stageLabel: "SELECTIVE STAGE GRAPH",
      stageNote: "完整反思路径；直连与轻量路径跳过未用阶段",
      stageFooter: "direct  V2 parser → SearchTool（Agent LLM = 0） · light 5 · reflective 8",
      controlLabel: "CONTROL PLANE / 工程层约束",
      control: [
        ["预算", "迭代 · 路径 · LLM · Token · 墙钟"],
        ["签名防重", "等价意图不重复检索"],
        ["阶段降级", "可解释回退或终止"],
        ["可取消", "断连 / 协作式取消"],
        ["Trace & Metrics", "阶段事件 · 决策 · 用量"],
      ],
    },
    stages: [
      ["01", "读取岗位", "understand", "LLM", "llm"],
      ["02", "制定计划", "plan", "LLM", "llm"],
      ["03", "调用工具", "execute", "TOOL", "tool"],
      ["04", "筛选候选人", "judge", "LLM", "llm"],
      ["05", "检查覆盖", "evaluate", "RULE", "rule"],
      ["06", "定位缺口", "reflect", "LLM", "llm"],
      ["07", "改写策略", "revise", "RULE", "rule"],
      ["08", "交付名单", "finalize", "LLM + TOOL", "final"],
    ],
    gateway: {
      title: "LLM Gateway",
      tag: "OPENAI-COMPATIBLE",
      subtitle: "Primary → fallback · usage accounting",
      chips: ["understand", "plan", "judge", "reflect", "finalize"],
      parser: "V2 parser",
      note: "structured JSON · token / latency accounted",
    },
    tool: {
      title: "SearchTool",
      tag: "PROTOCOL BOUNDARY",
      subtitle: "换检索后端，不改 Agent 阶段",
      methods: ["search_with_intent()", "rerank_pool()"],
      footer: "Agent → contract → backend",
    },
    retrieval: {
      title: "Hybrid V2 Retrieval",
      tag: "ELASTICSEARCH 8",
      subtitle: "IR 路径 · recall pool · 可解释证据",
      pipeline: [
        ["01", "Parser → Candidate IR", "LLM structured parse"],
        ["02", "Normalize / compile", "hard filters + policy"],
        ["03", "BM25 + multi-field kNN", "parallel recall channels"],
        ["04", "RRF + bounded rerank", "fuse and order"],
        ["05", "hardMatches + evidence", "candidate-level facts"],
        ["06", "quality + timings", "return recall pool"],
      ],
      footer: "返回 recall pool，而不是静默截成 top-k。",
    },
    backends: ["ES 8", "Memory", "SQLite FTS5"],
    evidence: [
      ["会话与状态", "L1 最近上下文 · L2 会话归档 · L3 已看 / 排除", "Redis 热存储 · 内存回退"],
      ["流式与追踪", "SSE 阶段事件 · agentTrace · 指标", "预算 / 降级 / 取消原因"],
      ["评测与交付", "离线 A/B · baseline · 540 项测试", "排序名单 + 候选人证据"],
    ],
    footer: "确定性负责边界，推理负责策略。",
    version: "search-agent-harness · v0.1.0",
    labels: {
      events: "events / state",
      continueRoute: "未达标 · continue",
      converged: "收敛 · done",
      nextRound: "下一轮 · next round",
      reply: "no LLM",
      search: "SEARCH",
    },
  },
  en: {
    lang: "en",
    title: "End-to-End Architecture",
    subtitle: "Deterministic retrieval · controlled strategy · observable execution",
    desc: "End-to-end architecture of Search Agent Harness: Web Console, FastAPI, intent routing, adaptive 3/5/8-stage harness, SearchTool protocol, Hybrid V2 retrieval, LLM Gateway, sessions and observability.",
    stats: [
      ["3 / 5 / 8", "ADAPTIVE ROUTES"],
      ["2", "TOOL METHODS"],
      ["8", "CORE STAGES"],
    ],
    sections: {
      request: "01 / REQUEST PATH",
      orchestration: "02 / CONTROLLED ORCHESTRATION",
      retrieval: "03 / TOOLS & MODELS",
      evidence: "CROSS-CUTTING / STATE & EVIDENCE",
    },
    web: {
      title: "Web Console",
      subtitle: "Conversation, live execution and sessions",
      modes: ["Agent", "Observe", "Sessions"],
      footer: "REST + SSE · zero-build UI",
    },
    requestCard: {
      title: "FastAPI + Routing",
      tag: "HTTP + SSE",
      endpoints: ["POST /search", "POST /search/stream", "GET /conversations", "GET /sessions/:token"],
      footer: "permission-scoped request context",
      routerTitle: "Intent & Entry Router",
      routerSub: "rules first · optional entry review",
      branches: [
        ["reply", "greeting / help / explicit off-topic"],
        ["SEARCH", "IntentRouter → EntryRouter"],
        ["route", "direct_search  |  agent"],
      ],
      routerFooter: "IntentRouter never touches SearchTool",
    },
    scope: {
      title: "Request context",
      subtitle: "one scope travels with every search",
      chips: ["tenant", "permission", "L1 history", "L3 exclusions"],
      footer: "optional fail-closed check",
    },
    orchestration: {
      title: "Search Agent Harness",
      subtitle: "Explicit state machine · budgets · stage degradation",
      routes: ["DIRECT · 3", "LIGHT · 5", "REFLECTIVE · 8"],
      routeNote: "selected per request",
      stageLabel: "SELECTIVE STAGE GRAPH",
      stageNote: "full reflective path; direct and light routes skip unused stages",
      stageFooter: "direct  V2 parser → SearchTool (agent LLM = 0) · light 5 · reflective 8",
      controlLabel: "CONTROL PLANE / DETERMINISTIC GUARDRAILS",
      control: [
        ["Budget", "iterations · paths · LLM · tokens · wall clock"],
        ["Intent signature", "dedupe equivalent retrieval"],
        ["Stage degradation", "explainable fallback or stop"],
        ["Cancellation", "disconnect / cooperative stop"],
        ["Trace & metrics", "stage events · decisions · usage"],
      ],
    },
    stages: [
      ["01", "Understand", "understand", "LLM", "llm"],
      ["02", "Plan", "plan", "LLM", "llm"],
      ["03", "Execute", "execute", "TOOL", "tool"],
      ["04", "Judge", "judge", "LLM", "llm"],
      ["05", "Evaluate", "evaluate", "RULE", "rule"],
      ["06", "Reflect", "reflect", "LLM", "llm"],
      ["07", "Revise", "revise", "RULE", "rule"],
      ["08", "Finalize", "finalize", "LLM + TOOL", "final"],
    ],
    gateway: {
      title: "LLM Gateway",
      tag: "OPENAI-COMPATIBLE",
      subtitle: "Primary → fallback · usage accounting",
      chips: ["understand", "plan", "judge", "reflect", "finalize"],
      parser: "V2 parser",
      note: "structured JSON · token / latency accounted",
    },
    tool: {
      title: "SearchTool",
      tag: "PROTOCOL BOUNDARY",
      subtitle: "replace the backend, keep every stage",
      methods: ["search_with_intent()", "rerank_pool()"],
      footer: "Agent → contract → backend",
    },
    retrieval: {
      title: "Hybrid V2 Retrieval",
      tag: "ELASTICSEARCH 8",
      subtitle: "IR path · recall pool · explainable evidence",
      pipeline: [
        ["01", "Parser → Candidate IR", "LLM structured parse"],
        ["02", "Normalize / compile", "hard filters + policy"],
        ["03", "BM25 + multi-field kNN", "parallel recall channels"],
        ["04", "RRF + bounded rerank", "fuse and order"],
        ["05", "hardMatches + evidence", "candidate-level facts"],
        ["06", "quality + timings", "return recall pool"],
      ],
      footer: "Returns the recall pool; finalize owns the top-k cut.",
    },
    backends: ["ES 8", "Memory", "SQLite FTS5"],
    evidence: [
      ["Conversation & session", "L1 recent context · L2 archive · L3 exclusions", "Redis hot store · in-memory fallback"],
      ["Streaming & trace", "SSE stage events · agentTrace · metrics", "budget / degrade / cancellation reasons"],
      ["Evaluation & delivery", "offline A/B · baseline · 540 tests", "ranked shortlist + candidate evidence"],
    ],
    footer: "Determinism where it matters. Reasoning where it helps.",
    version: "search-agent-harness · v0.1.0",
    labels: {
      events: "events / state",
      continueRoute: "continue",
      converged: "done",
      nextRound: "next round",
      reply: "no LLM",
      search: "SEARCH",
    },
  },
};

const esc = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");

function text(x, y, value, opts = {}) {
  const {
    size = 16,
    weight = 400,
    fill = C.ink,
    family = SANS,
    anchor = "start",
    spacing = 0,
    opacity = 1,
    style = "",
  } = opts;
  return `<text x="${x}" y="${y}" font-family="${family}" font-size="${size}" font-weight="${weight}" fill="${fill}" text-anchor="${anchor}"${spacing ? ` letter-spacing="${spacing}"` : ""}${opacity !== 1 ? ` opacity="${opacity}"` : ""}${style ? ` style="${style}"` : ""}>${esc(value)}</text>`;
}

function rect(x, y, w, h, opts = {}) {
  const {
    fill = C.white,
    stroke = "none",
    width = 1,
    rx = 18,
    opacity = 1,
    dash = "",
  } = opts;
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="${width}"${dash ? ` stroke-dasharray="${dash}"` : ""}${opacity !== 1 ? ` opacity="${opacity}"` : ""}/>`;
}

function path(d, opts = {}) {
  const {
    stroke = C.accent,
    width = 2,
    fill = "none",
    dash = "",
    markerEnd = "",
    markerStart = "",
    opacity = 1,
    cap = "round",
  } = opts;
  return `<path d="${d}" fill="${fill}" stroke="${stroke}" stroke-width="${width}" stroke-linecap="${cap}" stroke-linejoin="round"${dash ? ` stroke-dasharray="${dash}"` : ""}${markerStart ? ` marker-start="url(#${markerStart})"` : ""}${markerEnd ? ` marker-end="url(#${markerEnd})"` : ""}${opacity !== 1 ? ` opacity="${opacity}"` : ""}/>`;
}

function chip(x, y, w, h, label, opts = {}) {
  const {
    fill = C.accentWash,
    stroke = C.accentLine,
    color = C.accentStrong,
    size = 10,
    family = MONO,
    weight = 500,
    rx = 8,
    spacing = 0,
  } = opts;
  return `${rect(x, y, w, h, { fill, stroke, width: 1, rx })}${text(x + w / 2, y + h / 2 + size * 0.35, label, { size, weight, fill: color, family, anchor: "middle", spacing })}`;
}

function sectionLabel(x, y, label) {
  return `${text(x, y, label, { size: 10.5, weight: 600, fill: C.accent, family: MONO, spacing: 1.35 })}<line x1="${x}" y1="${y + 10}" x2="${x + 34}" y2="${y + 10}" stroke="${C.signal}" stroke-width="2.5"/>`;
}

function stageNode(x, y, w, h, stage) {
  const [num, label, code, meta, kind] = stage;
  let fill = C.warningWash;
  let stroke = C.warningLine;
  let ink = C.ink;
  let codeInk = C.warningInk;
  let metaFill = C.warningInk;

  if (kind === "tool") {
    fill = C.accentWash;
    stroke = C.accentLine;
    codeInk = C.accentStrong;
    metaFill = C.accentStrong;
  } else if (kind === "rule") {
    fill = C.neutralWash;
    stroke = C.neutralLine;
    codeInk = C.muted;
    metaFill = C.muted;
  } else if (kind === "final") {
    fill = C.forest;
    stroke = C.forest;
    ink = C.forestMuted;
    codeInk = C.forestMuted;
    metaFill = C.forestMuted;
  }

  return [
    rect(x, y, w, h, { fill, stroke, width: 1, rx: 14 }),
    text(x + 12, y + 17, num, { size: 8.5, weight: 600, fill: kind === "final" ? C.forestMuted : C.muted, family: MONO, spacing: 0.8 }),
    text(x + w - 10, y + 17, meta, { size: 7.5, weight: 600, fill: metaFill, family: MONO, anchor: "end", spacing: 0.45 }),
    text(x + 12, y + 42, label, { size: kind === "final" ? 13.5 : 13, weight: 650, fill: kind === "final" ? C.white : ink }),
    text(x + 12, y + 61, code, { size: 9.5, weight: 500, fill: codeInk, family: MONO }),
  ].join("\n");
}

function render(lang) {
  const t = copy[lang];
  const p = [];
  const statX = [1160, 1304, 1436];
  const stageBoxX = 432;
  const stageBoxY = 318;
  const stageW = 104;
  const stageGap = 15;
  const stageXs = [448, 567, 686, 805, 924];

  p.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-labelledby="title desc" lang="${t.lang}">`);
  p.push(`<title id="title">${esc(t.title)}</title>`);
  p.push(`<desc id="desc">${esc(t.desc)}</desc>`);
  p.push(`<defs>
    <pattern id="dotGrid" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1.2" cy="1.2" r="1" fill="${C.rule}" opacity=".42"/></pattern>
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${C.accent}"/></marker>
    <marker id="arrowMuted" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${C.quiet}"/></marker>
    <marker id="arrowAmber" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${C.signal}"/></marker>
  </defs>`);
  p.push(rect(0, 0, W, H, { fill: C.paper, rx: 0 }));
  p.push(rect(0, 0, W, H, { fill: "url(#dotGrid)", rx: 0 }));

  // Header
  p.push(rect(64, 42, 28, 4, { fill: C.signal, rx: 2 }));
  p.push(text(64, 70, "SEARCH AGENT HARNESS / SYSTEM MAP", { size: 10.5, weight: 650, fill: C.accent, family: MONO, spacing: 1.7 }));
  p.push(text(64, 116, t.title, { size: 43, weight: 650, fill: C.ink }));
  p.push(text(64, 146, t.subtitle, { size: 16, weight: 400, fill: C.muted }));

  t.stats.forEach(([value, label], index) => {
    const x = statX[index];
    if (index > 0) p.push(`<line x1="${x - 28}" y1="72" x2="${x - 28}" y2="116" stroke="${C.rule}" stroke-width="1"/>`);
    p.push(text(x, 92, value, { size: 23, weight: 650, fill: C.ink }));
    p.push(text(x, 112, label, { size: 8.5, weight: 650, fill: C.muted, family: MONO, spacing: 1.05 }));
  });
  p.push(`<line x1="64" y1="164" x2="1536" y2="164" stroke="${C.rule}" stroke-width="1"/>`);

  // Left: request path
  p.push(sectionLabel(64, 200, t.sections.request));
  p.push(rect(64, 216, 320, 160, { fill: C.forest, stroke: C.forest, rx: 20 }));
  p.push(`<circle cx="338" cy="242" r="3" fill="${C.signal}"/><circle cx="350" cy="242" r="3" fill="${C.accentLine}"/><circle cx="362" cy="242" r="3" fill="${C.forestMuted}" opacity=".55"/>`);
  p.push(text(84, 254, t.web.title, { size: 22, weight: 650, fill: C.white }));
  p.push(text(84, 278, t.web.subtitle, { size: 12, weight: 400, fill: C.forestMuted }));
  const modeWidths = [62, 78, 82];
  let modeX = 84;
  t.web.modes.forEach((mode, index) => {
    const w = modeWidths[index];
    p.push(chip(modeX, 296, w, 25, mode, { fill: "none", stroke: C.forestLine, color: C.forestMuted, size: 9, rx: 7 }));
    modeX += w + 8;
  });
  p.push(text(84, 353, t.web.footer, { size: 9.5, weight: 500, fill: C.forestMuted, family: MONO }));

  p.push(path("M 224 376 V 404", { markerEnd: "arrow", width: 1.8 }));

  p.push(rect(64, 404, 320, 298, { fill: C.surface, stroke: C.rule, rx: 18 }));
  p.push(text(84, 432, t.requestCard.title, { size: 17, weight: 650, fill: C.ink }));
  p.push(chip(300, 416, 60, 22, t.requestCard.tag, { fill: C.accentWash, stroke: C.accentLine, color: C.accentStrong, size: 8, rx: 6 }));
  const endpointX = [84, 208, 84, 208];
  const endpointY = [446, 446, 480, 480];
  const endpointW = [116, 156, 116, 156];
  t.requestCard.endpoints.forEach((endpoint, index) => {
    p.push(chip(endpointX[index], endpointY[index], endpointW[index], 24, endpoint, { fill: C.neutralWash, stroke: C.neutralLine, color: C.ink, size: 8.5, rx: 6 }));
  });
  p.push(text(84, 518, t.requestCard.footer, { size: 8.5, weight: 500, fill: C.muted, family: MONO }));
  p.push(`<line x1="84" y1="532" x2="364" y2="532" stroke="${C.ruleSoft}" stroke-width="1"/>`);
  p.push(text(84, 556, t.requestCard.routerTitle, { size: 16, weight: 650, fill: C.ink }));
  p.push(text(84, 575, t.requestCard.routerSub, { size: 9, weight: 500, fill: C.muted, family: MONO }));

  const branchColors = [
    [C.warningWash, C.warningLine, C.warningInk],
    [C.accentWash, C.accentLine, C.accentStrong],
    [C.infoWash, C.infoLine, C.infoInk],
  ];
  const branchY = [588, 620, 652];
  t.requestCard.branches.forEach(([tag, value], index) => {
    const [fill, stroke, color] = branchColors[index];
    p.push(chip(84, branchY[index], index === 0 ? 58 : 66, 24, tag, { fill, stroke, color, size: 8.2, rx: 6 }));
    p.push(text(154, branchY[index] + 16, value, { size: 9.5, weight: 500, fill: C.muted }));
  });
  p.push(text(84, 689, t.requestCard.routerFooter, { size: 8.2, weight: 500, fill: C.quiet, family: MONO }));

  p.push(rect(64, 728, 320, 132, { fill: C.surface, stroke: C.rule, rx: 18 }));
  p.push(text(84, 756, t.scope.title, { size: 15, weight: 650, fill: C.ink }));
  p.push(text(84, 775, t.scope.subtitle, { size: 9.5, weight: 400, fill: C.muted }));
  const scopeChips = [
    [84, 792, 58],
    [150, 792, 72],
    [230, 792, 70],
    [84, 826, 86],
  ];
  t.scope.chips.forEach((scope, index) => {
    p.push(chip(scopeChips[index][0], scopeChips[index][1], scopeChips[index][2], 24, scope, { fill: C.infoWash, stroke: C.infoLine, color: C.infoInk, size: 8.2, rx: 6 }));
  });
  p.push(text(178, 844, t.scope.footer, { size: 8.2, weight: 500, fill: C.quiet, family: MONO }));

  // Center: orchestration
  p.push(rect(408, 184, 668, 676, { fill: C.surface, stroke: C.rule, rx: 24 }));
  // Router -> harness entry rail (drawn after the panel so the arrow stays visible)
  p.push(path("M 384 555 H 394 Q 400 555 400 549 V 398 Q 400 392 406 392 H 430", { markerEnd: "arrow", width: 1.8 }));
  p.push(sectionLabel(432, 208, t.sections.orchestration));
  p.push(text(432, 240, t.orchestration.title, { size: 25, weight: 650, fill: C.ink }));
  p.push(text(432, 261, t.orchestration.subtitle, { size: 11.5, weight: 400, fill: C.muted }));

  const routeWidths = [110, 110, 138];
  const routeX = [432, 550, 668];
  t.orchestration.routes.forEach((route, index) => {
    const isReflective = index === 2;
    p.push(chip(routeX[index], 273, routeWidths[index], 28, route, {
      fill: isReflective ? C.forest : C.neutralWash,
      stroke: isReflective ? C.forest : C.neutralLine,
      color: isReflective ? C.white : C.muted,
      size: 8.7,
      rx: 8,
      spacing: 0.45,
    }));
  });
  p.push(text(820, 291, t.orchestration.routeNote, { size: 8.5, weight: 500, fill: C.quiet, family: MONO }));

  p.push(rect(stageBoxX, stageBoxY, 620, 286, { fill: C.paper, stroke: C.ruleSoft, rx: 16 }));
  p.push(text(448, 340, t.orchestration.stageLabel, { size: 8.6, weight: 650, fill: C.muted, family: MONO, spacing: 1.1 }));
  p.push(text(1036, 340, t.orchestration.stageNote, { size: 8.2, weight: 400, fill: C.quiet, anchor: "end" }));

  // Stage nodes
  p.push(stageNode(stageXs[0], 354, stageW, 76, t.stages[0]));
  p.push(path(`M ${stageXs[0] + stageW} 392 H ${stageXs[1]}`, { markerEnd: "arrowMuted", width: 1.5 }));
  p.push(stageNode(stageXs[1], 354, stageW, 76, t.stages[1]));
  p.push(path(`M ${stageXs[1] + stageW} 392 H ${stageXs[2]}`, { markerEnd: "arrowMuted", width: 1.5 }));
  p.push(stageNode(stageXs[2], 354, stageW, 76, t.stages[2]));
  p.push(path(`M ${stageXs[2] + stageW} 392 H ${stageXs[3]}`, { markerEnd: "arrowMuted", width: 1.5 }));
  p.push(stageNode(stageXs[3], 354, stageW, 76, t.stages[3]));
  p.push(path(`M ${stageXs[3] + stageW} 392 H ${stageXs[4]}`, { markerEnd: "arrowMuted", width: 1.5 }));
  p.push(stageNode(stageXs[4], 354, stageW, 76, t.stages[4]));

  // Loop branch: evaluate -> reflect -> revise -> execute; evaluate -> finalize
  p.push(stageNode(805, 498, stageW, 76, t.stages[5]));
  p.push(stageNode(686, 498, stageW, 76, t.stages[6]));
  p.push(stageNode(924, 498, stageW, 76, t.stages[7]));
  p.push(path("M 924 392 C 892 408 857 442 857 496", { markerEnd: "arrowMuted", width: 1.45 }));
  p.push(text(881, 453, t.labels.continueRoute, { size: 7.8, weight: 500, fill: C.quiet, family: MONO, anchor: "middle" }));
  p.push(path("M 805 536 H 792", { markerEnd: "arrowMuted", width: 1.45 }));
  p.push(path("M 738 498 V 432", { markerEnd: "arrowMuted", width: 1.45 }));
  p.push(text(748, 469, t.labels.nextRound, { size: 7.8, weight: 500, fill: C.quiet, family: MONO }));
  p.push(path("M 976 430 V 496", { markerEnd: "arrow", width: 1.7 }));
  p.push(text(987, 468, t.labels.converged, { size: 7.8, weight: 500, fill: C.accent, family: MONO }));
  p.push(text(448, 592, t.orchestration.stageFooter, { size: 8.4, weight: 500, fill: C.quiet, family: MONO }));

  // Control plane
  p.push(rect(432, 620, 620, 214, { fill: C.forest, stroke: C.forest, rx: 16 }));
  p.push(text(448, 646, t.orchestration.controlLabel, { size: 8.7, weight: 650, fill: C.forestMuted, family: MONO, spacing: 0.9 }));
  const controlItems = t.orchestration.control;
  const controlLayout = [
    [448, 660, 188, 64],
    [648, 660, 188, 64],
    [848, 660, 188, 64],
    [448, 736, 278, 64],
    [738, 736, 298, 64],
  ];
  controlItems.forEach(([title, detail], index) => {
    const [x, y, w, h] = controlLayout[index];
    p.push(rect(x, y, w, h, { fill: C.forestSoft, stroke: C.forestLine, rx: 10 }));
    p.push(`<circle cx="${x + 12}" cy="${y + 17}" r="3" fill="${index === 0 ? C.signal : C.accentLine}"/>`);
    p.push(text(x + 22, y + 21, title, { size: 10.8, weight: 650, fill: C.white }));
    p.push(text(x + 12, y + 44, detail, { size: 8.4, weight: 400, fill: C.forestMuted }));
  });

  // Right: tools & models
  p.push(sectionLabel(1100, 200, t.sections.retrieval));
  p.push(rect(1100, 216, 436, 154, { fill: C.surface, stroke: C.rule, rx: 18 }));
  p.push(rect(1124, 238, 6, 26, { fill: C.signal, rx: 3 }));
  p.push(text(1140, 252, t.gateway.title, { size: 18, weight: 650, fill: C.ink }));
  p.push(text(1512, 249, t.gateway.tag, { size: 7.6, weight: 650, fill: C.quiet, family: MONO, anchor: "end", spacing: 0.6 }));
  p.push(text(1140, 273, t.gateway.subtitle, { size: 9.8, weight: 400, fill: C.muted }));
  const gatewayChips = [
    [1124, 288, 66],
    [1198, 288, 38],
    [1244, 288, 42],
    [1294, 288, 52],
    [1354, 288, 58],
  ];
  t.gateway.chips.forEach((label, index) => {
    p.push(chip(gatewayChips[index][0], gatewayChips[index][1], gatewayChips[index][2], 23, label, { fill: C.warningWash, stroke: C.warningLine, color: C.warningInk, size: 8, rx: 6 }));
  });
  p.push(chip(1124, 322, 64, 23, t.gateway.parser, { fill: C.warningWash, stroke: C.warningLine, color: C.warningInk, size: 8, rx: 6 }));
  p.push(text(1198, 337, t.gateway.note, { size: 8.2, weight: 500, fill: C.quiet, family: MONO }));

  p.push(path("M 1076 293 H 1098", { markerEnd: "arrowAmber", markerStart: "arrowAmber", width: 1.7, stroke: C.signal }));
  p.push(path("M 1076 467 H 1098", { markerEnd: "arrow", width: 1.7 }));

  p.push(rect(1100, 394, 436, 146, { fill: C.accentWash, stroke: C.accentLine, rx: 18 }));
  p.push(text(1124, 424, t.tool.title, { size: 18, weight: 650, fill: C.ink }));
  p.push(text(1512, 421, t.tool.tag, { size: 7.6, weight: 650, fill: C.accentStrong, family: MONO, anchor: "end", spacing: 0.6 }));
  p.push(text(1124, 445, t.tool.subtitle, { size: 9.8, weight: 400, fill: C.muted }));
  p.push(chip(1124, 458, 176, 30, t.tool.methods[0], { fill: C.white, stroke: C.accentLine, color: C.accentStrong, size: 9, rx: 7 }));
  p.push(chip(1312, 458, 130, 30, t.tool.methods[1], { fill: C.white, stroke: C.accentLine, color: C.accentStrong, size: 9, rx: 7 }));
  p.push(text(1124, 518, t.tool.footer, { size: 8.5, weight: 500, fill: C.accentStrong, family: MONO }));

  p.push(path("M 1318 540 V 562", { markerEnd: "arrow", width: 1.7 }));

  p.push(rect(1100, 564, 436, 238, { fill: C.surface, stroke: C.rule, rx: 18 }));
  p.push(text(1124, 594, t.retrieval.title, { size: 18, weight: 650, fill: C.ink }));
  p.push(chip(1428, 575, 84, 22, t.retrieval.tag, { fill: C.accentWash, stroke: C.accentLine, color: C.accentStrong, size: 7.5, rx: 6 }));
  p.push(text(1124, 615, t.retrieval.subtitle, { size: 9.8, weight: 400, fill: C.muted }));

  const pipelineLayout = [
    [1124, 630, 188, 50],
    [1324, 630, 188, 50],
    [1124, 688, 188, 50],
    [1324, 688, 188, 50],
    [1124, 746, 188, 50],
    [1324, 746, 188, 50],
  ];
  t.retrieval.pipeline.forEach(([num, title, detail], index) => {
    const [x, y, w, h] = pipelineLayout[index];
    p.push(rect(x, y, w, h, { fill: C.neutralWash, stroke: C.neutralLine, rx: 9 }));
    p.push(text(x + 10, y + 15, num, { size: 7.5, weight: 650, fill: C.accent, family: MONO }));
    p.push(text(x + 30, y + 15, title, { size: 9.8, weight: 650, fill: C.ink }));
    p.push(text(x + 10, y + 36, detail, { size: 7.7, weight: 500, fill: C.muted, family: MONO }));
  });
  p.push(text(1124, 790, t.retrieval.footer, { size: 8.2, weight: 500, fill: C.quiet, family: MONO }));

  p.push(rect(1100, 814, 436, 46, { fill: C.surface, stroke: C.rule, rx: 12 }));
  p.push(text(1120, 842, "BACKENDS", { size: 8.2, weight: 650, fill: C.muted, family: MONO, spacing: 0.8 }));
  const backendX = [1200, 1262, 1322];
  const backendW = [52, 50, 92];
  t.backends.forEach((backend, index) => {
    p.push(chip(backendX[index], 824, backendW[index], 25, backend, { fill: C.accentWash, stroke: C.accentLine, color: C.accentStrong, size: 8, rx: 6 }));
  });

  // Harness -> evidence rail
  p.push(path("M 742 860 V 890", { stroke: C.quiet, width: 1.4, dash: "4 5", markerEnd: "arrowMuted" }));
  p.push(text(752, 879, t.labels.events, { size: 7.8, weight: 500, fill: C.quiet, family: MONO }));

  // Bottom cross-cutting band
  p.push(rect(64, 892, 1472, 100, { fill: C.surface, stroke: C.rule, rx: 20 }));
  p.push(text(84, 915, t.sections.evidence, { size: 8.8, weight: 650, fill: C.accent, family: MONO, spacing: 1.0 }));
  const evidenceX = [84, 570, 1050];
  const evidenceColors = [C.accent, C.signal, C.infoInk];
  t.evidence.forEach(([title, line1, line2], index) => {
    const x = evidenceX[index];
    if (index > 0) p.push(`<line x1="${x - 28}" y1="932" x2="${x - 28}" y2="972" stroke="${C.ruleSoft}" stroke-width="1"/>`);
    p.push(rect(x, 932, 4, 40, { fill: evidenceColors[index], rx: 2 }));
    p.push(text(x + 14, 944, title, { size: 12.8, weight: 650, fill: C.ink }));
    p.push(text(x + 14, 960, line1, { size: 8.8, weight: 400, fill: C.muted }));
    p.push(text(x + 14, 976, line2, { size: 8.8, weight: 400, fill: C.muted }));
  });

  p.push(text(64, 1018, t.footer, { size: 10, weight: 500, fill: C.muted, family: MONO }));
  p.push(text(1536, 1018, t.version, { size: 9.2, weight: 500, fill: C.quiet, family: MONO, anchor: "end" }));
  p.push("</svg>");
  return p.join("\n");
}

fs.mkdirSync(docsDir, { recursive: true });
for (const lang of ["zh", "en"]) {
  const output = nodePath.join(docsDir, `architecture.${lang}.svg`);
  fs.writeFileSync(output, render(lang), "utf8");
  console.log(`wrote ${nodePath.relative(repoRoot, output)}`);
}