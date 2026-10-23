#!/usr/bin/env node
// Record a full-online Web UI demo through the Chrome DevTools Protocol.
//
// Start the app in full-online mode first:
//   .\scripts\start-dev.cmd -StartOnline
//   node scripts/record-demo.mjs
//
// The script refuses to record unless /healthz reports online mode with the
// Hybrid V2 backend. It also verifies that the run visits all 8 reflective
// stages before replacing docs/demo.mp4.
//
// Environment overrides:
//   DEMO_URL               Web console URL (default: http://127.0.0.1:8000)
//   DEMO_QUERY              Search query (default: payment-system capability search)
//   DEMO_TOP_K              Delivery target shown in the UI (default: 50; high enough to exercise reflect/revise)
//   DEMO_SPEED              Playback speed for the model/retrieval wait (default: 4)
//   DEMO_FRAME_EVERY_NTH    Capture every Nth rendered frame (default: 12)
//   DEMO_RUN_TIMEOUT_MS     Maximum real time to wait for one search (default: 300000)
//   DEMO_REQUIRE_FULL_STAGES Set to 0 to allow a run that skips reflect/revise
//   DEMO_OUTPUT / DEMO_POSTER  Output paths (default: docs/demo.mp4 and docs/demo-poster.png)
//   DEMO_WIDTH / DEMO_HEIGHT Viewport size (default: 1440x810)
//   CHROME_PATH             Chrome/Edge executable
//   FFMPEG_PATH             ffmpeg executable (otherwise auto-detected)

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const baseUrl = process.env.DEMO_URL || "http://127.0.0.1:8000";
const viewportWidth = Number(process.env.DEMO_WIDTH || 1440);
const viewportHeight = Number(process.env.DEMO_HEIGHT || 810);
const query =
  process.env.DEMO_QUERY ||
  "招聘 5 年以上支付系统后端工程师，必须做过高并发和分布式事务，上海或杭州优先；可迁移经验也可以，排除只有运维经历的候选人。";
const topK = Math.max(1, Math.min(50, Number(process.env.DEMO_TOP_K || 50)));
const waitPlaybackSpeed = Math.max(1, Number(process.env.DEMO_SPEED || 4));
const frameEveryNth = Math.max(1, Number(process.env.DEMO_FRAME_EVERY_NTH || 12));
const runTimeoutMs = Math.max(30000, Number(process.env.DEMO_RUN_TIMEOUT_MS || 300000));
const requireFullStages = process.env.DEMO_REQUIRE_FULL_STAGES !== "0";

const outputPath = path.resolve(repoRoot, process.env.DEMO_OUTPUT || path.join("docs", "demo.mp4"));
const posterPath = path.resolve(repoRoot, process.env.DEMO_POSTER || path.join("docs", "demo-poster.png"));
const tempOutputPath = path.join(repoRoot, ".tmp-demo-online.mp4");
const tempPosterPath = path.join(repoRoot, ".tmp-demo-online-poster.png");
const framesDir = fs.mkdtempSync(path.join(os.tmpdir(), "search-agent-demo-frames-"));
const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), "search-agent-demo-profile-"));

function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new Error("Chrome/Edge not found. Set CHROME_PATH to the browser executable.");
}

function findFfmpeg() {
  const candidates = [process.env.FFMPEG_PATH].filter(Boolean);
  const bundledDir = path.join(repoRoot, ".tmp-video-tools", "imageio_ffmpeg", "binaries");
  if (fs.existsSync(bundledDir)) {
    for (const name of fs.readdirSync(bundledDir)) {
      if (/^ffmpeg.*\.exe$/i.test(name)) candidates.push(path.join(bundledDir, name));
    }
  }
  const probe = spawnSync("ffmpeg", ["-version"], { stdio: "ignore" });
  if (probe.status === 0) candidates.push("ffmpeg");
  for (const candidate of candidates) {
    if (candidate === "ffmpeg" || fs.existsSync(candidate)) return candidate;
  }
  throw new Error(
    "ffmpeg not found. Install ffmpeg, or run: python -m pip install --target .tmp-video-tools imageio-ffmpeg",
  );
}

async function readHealth() {
  const response = await fetch(`${baseUrl}/healthz`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Health check failed: HTTP ${response.status}`);
  return response.json();
}

function demoOverlay(speed) {
  const waitPlaybackSpeed = Math.max(1, Number(speed) || 4);
  if (document.getElementById("demo-overlay")) return true;

  const stages = [
    ["understand", "读取岗位", "理解岗位、能力与硬条件"],
    ["plan", "制定计划", "生成互补的搜索路径"],
    ["execute", "调用工具", "并行召回候选人证据"],
    ["judge", "筛选候选人", "按能力与硬条件判断"],
    ["evaluate", "检查覆盖", "判断是否继续搜索"],
    ["reflect", "定位缺口", "总结共性与覆盖缺口"],
    ["revise", "改写策略", "放宽、收紧或新增路径"],
    ["finalize", "交付名单", "合并重排并输出结果"],
  ];

  const style = document.createElement("style");
  style.textContent = `
    #demo-overlay {
      position: fixed; inset: 0; z-index: 2147483646; display: flex; align-items: center; justify-content: center;
      padding: 54px 70px; box-sizing: border-box; color: #f5f1e8;
      background:
        radial-gradient(circle at 12% 14%, rgba(56, 121, 102, .52), transparent 34%),
        radial-gradient(circle at 88% 82%, rgba(196, 140, 62, .16), transparent 28%),
        #08231f;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
      opacity: 0; transition: opacity .45s ease; pointer-events: none; background-color: #08231f;
    }
    #demo-overlay.is-visible { opacity: 1 !important; }
    .demo-intro { width: 100%; max-width: 1260px; }
    .demo-kicker {
      display: flex; align-items: center; gap: 10px; margin-bottom: 22px;
      color: #b9d7cc; font-size: 16px; font-weight: 700; letter-spacing: .13em; text-transform: uppercase;
    }
    .demo-kicker .demo-dot { width: 9px; height: 9px; border-radius: 50%; background: #7fd4a8; box-shadow: 0 0 0 6px rgba(127, 212, 168, .13); }
    .demo-intro h1 { margin: 0; max-width: 1160px; font-size: 58px; line-height: 1.08; letter-spacing: -.035em; }
    .demo-intro h1 em { font-style: normal; color: #e8b45f; }
    .demo-intro p { margin: 22px 0 0; color: #d7e4de; font-size: 23px; line-height: 1.55; }
    .demo-stage-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 30px; }
    .demo-stage-card {
      min-height: 82px; padding: 12px 14px; border: 1px solid rgba(214, 234, 226, .22); border-radius: 10px;
      background: rgba(255, 255, 255, .045); box-sizing: border-box;
    }
    .demo-stage-card i { color: #e8b45f; font-size: 12px; font-style: normal; font-weight: 700; letter-spacing: .08em; }
    .demo-stage-card b { display: block; margin-top: 8px; color: #f5f1e8; font-size: 16px; }
    .demo-stage-card small { display: block; margin-top: 3px; color: #8fb0a5; font-size: 12px; }
    .demo-foot { margin-top: 26px; color: #8fb0a5; font-size: 14px; }

    #demo-livecard {
      position: fixed; left: 16px; top: 82px; z-index: 2147483645; width: 248px; box-sizing: border-box;
      padding: 14px 14px 13px; border: 1px solid rgba(201, 226, 216, .18); border-radius: 13px;
      background: rgba(6, 33, 29, .95); color: #e8f1ec; box-shadow: 0 18px 40px rgba(0, 0, 0, .22);
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
      opacity: 0; transform: translateY(-6px); transition: opacity .3s ease, transform .3s ease; pointer-events: none;
    }
    #demo-livecard.is-visible { opacity: 1; transform: translateY(0); }
    .demo-live-head { display: flex; align-items: center; gap: 8px; font-size: 14px; }
    .demo-live-head b { font-size: 15px; }
    .demo-live-dot { width: 8px; height: 8px; border-radius: 50%; background: #7fd4a8; box-shadow: 0 0 0 5px rgba(127, 212, 168, .12); }
    #demo-live-count { margin-left: auto; color: #e8b45f; font-size: 12px; font-weight: 700; }
    .demo-live-sub { margin-top: 6px; color: #8fb0a5; font-size: 11px; line-height: 1.45; }
    .demo-live-stages { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 3px; }
    .demo-live-stages li {
      display: flex; align-items: center; gap: 7px; min-height: 22px; padding: 2px 6px;
      border-radius: 5px; color: #78998d; font-size: 12px; transition: background .2s ease, color .2s ease;
    }
    .demo-live-stages li i { width: 18px; color: #63867a; font-size: 10px; font-style: normal; font-weight: 700; }
    .demo-live-stages li span { font-weight: 600; }
    .demo-live-stages li small { margin-left: auto; color: #53776a; font-size: 9px; }
    .demo-live-stages li.is-hit { color: #c5ddd3; background: rgba(127, 212, 168, .06); }
    .demo-live-stages li.is-hit i { color: #7fd4a8; }
    .demo-live-stages li.is-active { color: #102c26; background: #e8f3ed; }
    .demo-live-stages li.is-active i, .demo-live-stages li.is-active small { color: #4d7869; }
    .demo-live-active { margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(201, 226, 216, .14); color: #e8b45f; font-size: 11px; font-weight: 700; }

    #demo-pointer {
      position: fixed; top: 0; left: 0; z-index: 2147483647; width: 14px; height: 14px;
      margin: -7px 0 0 -7px; border: 2px solid #0b2b26; border-radius: 50%; background: #fffaf0;
      box-shadow: 0 0 0 7px rgba(11, 43, 38, .16); opacity: 0; pointer-events: none;
      transition: transform .42s cubic-bezier(.2,.7,.2,1), opacity .18s ease;
    }
    #demo-pointer.is-visible { opacity: 1; }
    .demo-focus { outline: 2px solid #e8b45f !important; outline-offset: 4px !important; box-shadow: 0 0 0 8px rgba(232, 180, 95, .14) !important; }

    .demo-end-card { width: 100%; max-width: 1200px; }
    .demo-end-card h1 { margin: 0; font-size: 58px; line-height: 1.1; letter-spacing: -.035em; }
    .demo-end-card h1 em { font-style: normal; color: #7fd4a8; }
    .demo-end-card p { margin: 22px 0 0; color: #d7e4de; font-size: 23px; line-height: 1.55; }
    .demo-stage-seq { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 28px; }
    .demo-stage-seq span {
      display: inline-flex; align-items: center; gap: 8px; padding: 9px 12px; border: 1px solid rgba(214, 234, 226, .24);
      border-radius: 999px; color: #dce9e3; font-size: 14px;
    }
    .demo-stage-seq i { color: #e8b45f; font-size: 11px; font-style: normal; font-weight: 700; }
    .demo-end-meta { margin-top: 26px; display: flex; gap: 12px; flex-wrap: wrap; }
    .demo-end-meta span { padding: 8px 12px; border-radius: 7px; background: rgba(255, 255, 255, .07); color: #cfe3da; font-size: 14px; }
  `;
  document.head.append(style);

  const overlay = document.createElement("div");
  overlay.id = "demo-overlay";
  document.body.append(overlay);

  const live = document.createElement("div");
  live.id = "demo-livecard";
  live.innerHTML = `
    <div class="demo-live-head"><span class="demo-live-dot"></span><b>完整在线</b><span id="demo-live-count">0 / 8</span></div>
    <div class="demo-live-sub">真实 LLM · Hybrid V2 · Elasticsearch</div>
    <ol class="demo-live-stages">
      ${stages.map(([key, label], index) => `<li data-stage="${key}"><i>${String(index + 1).padStart(2, "0")}</i><span>${label}</span><small>${key}</small></li>`).join("")}
    </ol>
    <div class="demo-live-active" id="demo-live-active">等待任务</div>
  `;
  document.body.append(live);

  const pointer = document.createElement("div");
  pointer.id = "demo-pointer";
  document.body.append(pointer);

  window.__demoShowIntro = () => {
    overlay.innerHTML = `
      <div class="demo-intro">
        <div class="demo-kicker"><span class="demo-dot"></span>Search Agent Harness · 完整在线</div>
        <h1>一条需求，<br><em>8 个阶段</em>走完搜索策略闭环</h1>
        <p>真实 LLM + Hybrid V2 + Elasticsearch。不是脚本化演示。</p>
        <div class="demo-stage-grid">
          ${stages.map(([, label, copy], index) => `<div class="demo-stage-card"><i>${String(index + 1).padStart(2, "0")}</i><b>${label}</b><small>${copy}</small></div>`).join("")}
        </div>
        <div class="demo-foot">真实在线链路 · 模型等待阶段 ${waitPlaybackSpeed}× 加速 · 演示数据为虚构</div>
      </div>`;
    overlay.classList.add("is-visible");
    live.classList.remove("is-visible");
  };

  window.__demoShowLive = () => live.classList.add("is-visible");
  window.__demoHideLive = () => live.classList.remove("is-visible");
  window.__demoHideOverlay = () => overlay.classList.remove("is-visible");

  window.__demoRefreshHud = () => {
    const railItems = [...document.querySelectorAll("#stage-rail .rail-item")];
    if (!railItems.length) return false;
    const visited = new Set(
      railItems.filter((item) => item.classList.contains("hit")).map((item) => item.dataset.stage),
    );
    const activeItem = railItems.find((item) => item.classList.contains("active"));
    const activeStage = activeItem?.dataset.stage || "";
    for (const row of document.querySelectorAll(".demo-live-stages li")) {
      row.classList.toggle("is-hit", visited.has(row.dataset.stage));
      row.classList.toggle("is-active", row.dataset.stage === activeStage);
    }
    const count = document.getElementById("demo-live-count");
    if (count) count.textContent = `${visited.size} / ${railItems.length}`;
    const active = document.getElementById("demo-live-active");
    if (active) {
      if (activeItem) {
        const index = railItems.indexOf(activeItem);
        const label = activeItem.querySelector("span")?.textContent?.trim() || activeStage;
        active.textContent = `当前 ${String(index + 1).padStart(2, "0")} · ${label}`;
      } else if (visited.size >= railItems.length) {
        active.textContent = `${railItems.length} / ${railItems.length} · 全部阶段完成`;
      } else {
        active.textContent = "等待阶段事件";
      }
    }
    const round = document.getElementById("agent-round")?.textContent?.trim();
    const sub = document.querySelector(".demo-live-sub");
    if (sub) sub.textContent = `真实 LLM · Hybrid V2 · Elasticsearch${Number(round) > 0 ? ` · 第 ${round} 轮` : ""}`;
    return true;
  };

  window.__demoShowEnd = (data = {}) => {
    const labels = (data.stageLabels || stages.map(([, label]) => label)).slice(0, 8);
    const stageChips = labels
      .map((label, index) => `<span><i>${String(index + 1).padStart(2, "0")}</i>${label}</span>`)
      .join("");
    overlay.innerHTML = `
      <div class="demo-end-card">
        <div class="demo-kicker"><span class="demo-dot"></span>完整在线 · 执行闭环</div>
        <h1><em>${data.visited || labels.length} / 8 阶段</em>全部走通</h1>
        <p>从岗位理解到反思改写，再到最终交付；这是真实模型与真实检索共同跑出的结果。</p>
        <div class="demo-stage-seq">${stageChips}</div>
        <div class="demo-end-meta">
          <span>${data.rounds || "0"} 轮检索</span>
          <span>${data.totalCount || data.total || "交付完成"}</span>
          <span>${data.elapsed ? `耗时 ${data.elapsed}` : "真实在线耗时"}</span>
        </div>
        <div class="demo-foot">完整在线 · 真实 LLM + Hybrid V2 + Elasticsearch · 模型等待阶段 ${waitPlaybackSpeed}× 加速</div>
      </div>`;
    overlay.classList.add("is-visible");
    live.classList.remove("is-visible");
  };

  window.__demoPointer = (selector) => new Promise((resolve) => {
    const target = document.querySelector(selector);
    const dot = document.getElementById("demo-pointer");
    if (!target || !dot) return resolve(false);
    const rect = target.getBoundingClientRect();
    dot.style.transform = `translate(${rect.left + rect.width / 2}px,${rect.top + rect.height / 2}px)`;
    dot.classList.add("is-visible");
    setTimeout(() => resolve(true), 430);
  });
  window.__demoHidePointer = () => document.getElementById("demo-pointer")?.classList.remove("is-visible");
  window.__demoFocus = (selector) => {
    const target = document.querySelector(selector);
    if (!target) return false;
    target.classList.add("demo-focus");
    setTimeout(() => target.classList.remove("demo-focus"), 1500);
    return true;
  };
  window.__demoType = async (selector, text, delay) => {
    const input = document.querySelector(selector);
    if (!input) return false;
    input.focus();
    input.value = "";
    for (let index = 0; index < text.length; index += 1) {
      input.value = text.slice(0, index + 1);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  };
  window.__demoScrollBy = (selector, delta, duration) => new Promise((resolve) => {
    const element = document.querySelector(selector);
    if (!element) return resolve(false);
    element.style.scrollBehavior = "auto";
    const start = element.scrollTop;
    const max = Math.max(0, element.scrollHeight - element.clientHeight);
    const end = Math.max(0, Math.min(max, start + delta));
    const started = performance.now();
    const tick = (now) => {
      const progress = Math.min(1, (now - started) / duration);
      const eased = progress < .5 ? 2 * progress * progress : 1 - Math.pow(-2 * progress + 2, 2) / 2;
      element.scrollTop = start + (end - start) * eased;
      if (progress < 1) requestAnimationFrame(tick);
      else resolve(true);
    };
    requestAnimationFrame(tick);
  });
  window.__demoScrollTo = (scrollerSelector, targetSelector, offset, duration) => new Promise((resolve) => {
    const scroller = document.querySelector(scrollerSelector);
    const target = document.querySelector(targetSelector);
    if (!scroller || !target) return resolve(false);
    scroller.style.scrollBehavior = "auto";
    const start = scroller.scrollTop;
    const rect = target.getBoundingClientRect();
    const scrollerRect = scroller.getBoundingClientRect();
    const destination = Math.max(0, start + rect.top - scrollerRect.top - offset);
    const started = performance.now();
    const tick = (now) => {
      const progress = Math.min(1, (now - started) / duration);
      const eased = progress < .5 ? 2 * progress * progress : 1 - Math.pow(-2 * progress + 2, 2) / 2;
      scroller.scrollTop = start + (destination - start) * eased;
      if (progress < 1) requestAnimationFrame(tick);
      else resolve(true);
    };
    requestAnimationFrame(tick);
  });

  return true;
}

const chromePath = findChrome();
const ffmpegPath = findFfmpeg();
console.log(`Chrome: ${chromePath}`);
console.log(`FFmpeg: ${ffmpegPath}`);
console.log(`Frames: ${framesDir}`);

const child = spawn(
  chromePath,
  [
    "--headless=new",
    "--disable-gpu",
    "--disable-gpu-compositing",
    "--disable-software-rasterizer",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--hide-scrollbars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--force-device-scale-factor=1",
    `--window-size=${viewportWidth},${viewportHeight}`,
    "--remote-debugging-pipe",
    `--user-data-dir=${profileDir}`,
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "inherit", "pipe", "pipe"] },
);

let nextId = 1;
let buffer = Buffer.alloc(0);
const pending = new Map();
const eventHandlers = new Map();

child.on("exit", (code, signal) => console.error(`Chrome exited: code=${code} signal=${signal}`));

child.stdio[4].on("data", (chunk) => {
  buffer = Buffer.concat([buffer, chunk]);
  let separator;
  while ((separator = buffer.indexOf(0)) >= 0) {
    const raw = buffer.subarray(0, separator).toString("utf8");
    buffer = buffer.subarray(separator + 1);
    if (!raw) continue;
    let message;
    try {
      message = JSON.parse(raw);
    } catch (error) {
      console.error(`Invalid CDP message: ${raw.slice(0, 240)}`);
      continue;
    }
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result || {});
      continue;
    }
    const handler = eventHandlers.get(message.method);
    if (handler) {
      Promise.resolve(handler(message.params || {}, message.sessionId)).catch((error) => {
        console.error(`event ${message.method}: ${error.message}`);
      });
    }
  }
});

function send(method, params = {}, sessionId) {
  const id = nextId++;
  const message = { id, method, params };
  if (sessionId) message.sessionId = sessionId;
  child.stdio[3].write(`${JSON.stringify(message)}\0`);
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    setTimeout(() => {
      if (!pending.has(id)) return;
      pending.delete(id);
      reject(new Error(`CDP timeout: ${method}`));
    }, 30000);
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function evaluate(sessionId, expression) {
  const result = await send(
    "Runtime.evaluate",
    { expression, awaitPromise: true, returnByValue: true },
    sessionId,
  );
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "Runtime evaluation failed");
  }
  return result.result.value;
}

async function waitFor(sessionId, expression, timeoutMs = 30000, intervalMs = 200) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(sessionId, expression)) return true;
    await sleep(intervalMs);
  }
  throw new Error(`Timed out waiting for: ${expression}`);
}

async function captureScreenshot(sessionId, file) {
  const result = await send("Page.captureScreenshot", { format: "png", fromSurface: true }, sessionId);
  fs.writeFileSync(file, Buffer.from(result.data, "base64"));
}

const frames = [];
let recording = false;
let frameSpeed = 1;
let screencastSessionId;

eventHandlers.set("Page.screencastFrame", async (params, sessionId) => {
  if (recording) {
    const index = String(frames.length).padStart(6, "0");
    const file = path.join(framesDir, `frame-${index}.jpg`);
    fs.writeFileSync(file, Buffer.from(params.data, "base64"));
    frames.push({
      file,
      ts: Number(params.metadata?.timestamp || Date.now() / 1000),
      speed: Math.max(1, frameSpeed),
    });
  }
  await send("Page.screencastFrameAck", { sessionId: params.sessionId }, sessionId).catch(() => {});
});

async function main() {
  const health = await readHealth();
  console.log(
    `Runtime: mode=${health.mode} backend=${health.backend} llm=${health.llm} onlineConfigured=${health.onlineConfigured}`,
  );
  if (health.mode !== "online" || !String(health.backend || "").includes("HybridV2")) {
    throw new Error(
      "This demo must run in full-online mode with the Hybrid V2 backend. " +
        "Start it with: .\\scripts\\start-dev.cmd -StartOnline",
    );
  }

  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Page.enable", {}, sessionId);
  await send("Runtime.enable", {}, sessionId);
  await send(
    "Emulation.setDeviceMetricsOverride",
    { width: viewportWidth, height: viewportHeight, deviceScaleFactor: 1, mobile: false },
    sessionId,
  );

  await send("Page.navigate", { url: `${baseUrl}/?view=agent` }, sessionId);
  await waitFor(
    sessionId,
    "document.readyState === 'complete' && !!document.getElementById('chat-query')",
    15000,
  );
  await evaluate(sessionId, `(${demoOverlay.toString()})(${waitPlaybackSpeed})`);
  await sleep(500);

  screencastSessionId = sessionId;
  await send(
    "Page.startScreencast",
    {
      format: "jpeg",
      quality: 88,
      maxWidth: viewportWidth,
      maxHeight: viewportHeight,
      everyNthFrame: frameEveryNth,
    },
    sessionId,
  );
  recording = true;
  const startedAt = Date.now();
  const log = (message) => console.log(`[${((Date.now() - startedAt) / 1000).toFixed(1)}s] ${message}`);

  log("intro");
  frameSpeed = 1;
  await evaluate(sessionId, "window.__demoShowIntro(); true");
  await sleep(4300);
  await evaluate(sessionId, "window.__demoHideOverlay(); true");
  await sleep(650);

  log("sidebar");
  await evaluate(sessionId, "window.__demoPointer('#sidebar-collapse')");
  await evaluate(sessionId, "document.getElementById('sidebar-collapse').click(); window.__demoHidePointer(); true");
  await sleep(800);

  log("online proof");
  await evaluate(sessionId, "window.__demoFocus('#mode-chip'); true");
  await sleep(1350);
  await evaluate(sessionId, "window.__demoFocus('#backend-chip'); true");
  await sleep(1350);

  log("typing");
  await evaluate(sessionId, "window.__demoPointer('#chat-query')");
  await evaluate(sessionId, `window.__demoType('#chat-query', ${JSON.stringify(query)}, 46)`);
  await sleep(450);
  await evaluate(sessionId, `document.getElementById('chat-top-k').value = '${topK}'; true`);
  await sleep(250);

  log("submit");
  await evaluate(sessionId, "window.__demoPointer('#chat-run')");
  await evaluate(sessionId, "document.getElementById('chat-form').requestSubmit(); window.__demoHidePointer(); true");
  await evaluate(sessionId, "window.__demoShowLive(); window.__demoRefreshHud(); true");

  frameSpeed = waitPlaybackSpeed;
  log(`waiting for full-online delivery (${waitPlaybackSpeed}x playback)`);
  let lastActive = "";
  let completed = false;
  const searchStarted = Date.now();
  while (Date.now() - searchStarted < runTimeoutMs) {
    const status = await evaluate(
      sessionId,
      `(() => {
        window.__demoRefreshHud();
        const active = document.querySelector('#stage-rail .rail-item.active')?.dataset.stage || '';
        const visited = document.querySelectorAll('#stage-rail .rail-item.hit').length;
        const failed = document.querySelector('.chat-message-status.is-failed');
        return {
          active,
          visited,
          done: document.body.innerText.includes('本次搜索已完成'),
          failed: failed ? failed.textContent.trim() : '',
        };
      })()`,
    );
    if (status.active && status.active !== lastActive) {
      lastActive = status.active;
      log(`stage ${status.active} (${status.visited}/8 visited)`);
    }
    if (status.failed) throw new Error(`Search failed: ${status.failed}`);
    if (status.done) {
      completed = true;
      break;
    }
    await sleep(400);
  }
  if (!completed) throw new Error(`Timed out waiting for search completion after ${runTimeoutMs}ms`);

  frameSpeed = 1;
  await sleep(1200);
  await evaluate(sessionId, "window.__demoRefreshHud(); true");
  const summary = await evaluate(
    sessionId,
    `(() => {
      const rail = [...document.querySelectorAll('#stage-rail .rail-item')];
      const visited = rail.filter((item) => item.classList.contains('hit'));
      return {
        visited: visited.length,
        stageLabels: visited.map((item) => item.querySelector('span')?.textContent?.trim() || item.dataset.stage),
        rounds: document.getElementById('agent-round')?.textContent?.trim() || '0',
        total: document.getElementById('results-hint')?.textContent?.trim() || '',
        elapsed: document.getElementById('m-elapsed')?.textContent?.trim() || '',
      };
    })()`,
  );
  const totalMatch = String(summary.total || "").match(/共\s*(\d+)\s*人/);
  summary.totalCount = totalMatch ? `${totalMatch[1]} 人` : summary.total;
  log(
    `completed: ${summary.visited}/8 stages, ${summary.rounds} round(s), ${summary.totalCount || "no result hint"}`,
  );
  if (requireFullStages && summary.visited < 8) {
    throw new Error(
      `The run visited ${summary.visited}/8 stages; reflect/revise was skipped. ` +
        "Keep the temporary recording for inspection, or rerun with another DEMO_QUERY.",
    );
  }

  await evaluate(sessionId, "window.__demoHideLive(); true");
  log("delivery");
  await evaluate(
    sessionId,
    "window.__demoScrollTo('#chat-scroll', '.chat-result-message.agent-delivery', 10, 1100)",
  );
  await sleep(1800);
  await evaluate(sessionId, "window.__demoScrollBy('#chat-scroll', 430, 4800)");
  await sleep(900);

  log("observability");
  await evaluate(sessionId, "window.__demoPointer('#view-console')");
  await evaluate(sessionId, "document.getElementById('view-console').click(); window.__demoHidePointer(); true");
  await sleep(1400);
  try {
    await waitFor(sessionId, "document.querySelectorAll('#results li').length > 0", 10000);
  } catch {
    log("no result cards (search returned zero)");
  }
  await evaluate(
    sessionId,
    "(() => { const timeline = document.querySelector('.timeline'); const results = document.querySelector('.results'); if (timeline) timeline.scrollTop = 0; if (results) results.scrollTop = 0; window.scrollTo(0, 0); return true; })()",
  );
  await sleep(1200);
  await evaluate(sessionId, "window.__demoFocus('#agent-stage-count'); true");
  await sleep(1200);
  await captureScreenshot(sessionId, tempPosterPath);

  log("reflection evidence");
  await evaluate(sessionId, "window.__demoPointer('.event-reflect')");
  await evaluate(
    sessionId,
    "window.__demoScrollTo('.timeline', '.event-reflect', 18, 950)",
  );
  await sleep(2600);
  await evaluate(sessionId, "window.__demoHidePointer(); window.__demoPointer('.event-revise')");
  await evaluate(
    sessionId,
    "window.__demoScrollTo('.timeline', '.event-revise', 18, 950)",
  );
  await sleep(2400);
  await evaluate(sessionId, "window.__demoHidePointer(); true");
  await evaluate(
    sessionId,
    "(() => { const timeline = document.querySelector('.timeline'); if (timeline) timeline.scrollTop = 0; return true; })()",
  );
  await sleep(900);

  log("outro");
  await evaluate(sessionId, `window.__demoShowEnd(${JSON.stringify(summary)}); true`);
  await sleep(4300);

  recording = false;
  await send("Page.stopScreencast", {}, screencastSessionId).catch(() => {});
  await sleep(400);
  log(`captured ${frames.length} frames`);

  if (frames.length === 0) throw new Error("No frames were captured.");

  const concatPath = path.join(framesDir, "frames.txt");
  const lines = [];
  let totalDuration = 0;
  for (let index = 0; index < frames.length; index += 1) {
    const current = frames[index];
    const next = frames[index + 1];
    const rawDuration = next ? Math.max(0.001, next.ts - current.ts) : 0.8;
    const duration = Math.max(0.001, rawDuration / Math.max(1, current.speed || 1));
    totalDuration += duration;
    lines.push(`file '${current.file.replace(/\\/g, "/").replace(/'/g, "'\\''")}'`);
    lines.push(`duration ${duration.toFixed(6)}`);
  }
  lines.push(`file '${frames[frames.length - 1].file.replace(/\\/g, "/").replace(/'/g, "'\\''")}'`);
  fs.writeFileSync(concatPath, lines.join("\n"), "utf8");

  console.log(`Encoding ${totalDuration.toFixed(1)}s to ${tempOutputPath}`);
  const encode = spawnSync(
    ffmpegPath,
    [
      "-y",
      "-hide_banner",
      "-loglevel",
      "error",
      "-f",
      "concat",
      "-safe",
      "0",
      "-i",
      concatPath,
      "-t",
      totalDuration.toFixed(3),
      "-vf",
      "fps=30,scale=in_range=full:out_range=tv:out_color_matrix=bt709,format=yuv420p",
      "-c:v",
      "libx264",
      "-preset",
      "medium",
      "-crf",
      "20",
      "-color_range",
      "tv",
      "-colorspace",
      "bt709",
      "-color_primaries",
      "bt709",
      "-color_trc",
      "bt709",
      "-movflags",
      "+faststart",
      "-an",
      tempOutputPath,
    ],
    { stdio: "inherit" },
  );
  if (encode.status !== 0) throw new Error(`ffmpeg failed with exit code ${encode.status}`);

  fs.copyFileSync(tempOutputPath, outputPath);
  if (fs.existsSync(tempPosterPath)) fs.copyFileSync(tempPosterPath, posterPath);

  console.log(`Saved ${outputPath}`);
  if (fs.existsSync(posterPath)) console.log(`Saved ${posterPath}`);
  await send("Browser.close").catch(() => {});
  await sleep(500);
}

main()
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(() => {
    setTimeout(() => {
      if (!child.killed) child.kill();
    }, 500);
  });