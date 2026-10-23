#!/usr/bin/env node
// Record a deterministic 1080p Web UI demo through the Chrome DevTools Protocol.
//
// Usage:
//   node scripts/record-demo.mjs
//
// Environment overrides:
//   DEMO_URL      Web console URL (default: http://127.0.0.1:8000)
//   CHROME_PATH   Chrome/Edge executable
//   FFMPEG_PATH   ffmpeg executable (otherwise auto-detected)
//   DEMO_WIDTH    Viewport width (default: 1920)
//   DEMO_HEIGHT   Viewport height (default: 1080)

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const baseUrl = process.env.DEMO_URL || "http://127.0.0.1:8000";
const viewportWidth = Number(process.env.DEMO_WIDTH || 1920);
const viewportHeight = Number(process.env.DEMO_HEIGHT || 1080);
const outputPath = path.join(repoRoot, "docs", "demo.mp4");
const posterPath = path.join(repoRoot, "docs", "demo-poster.png");
const framesDir = fs.mkdtempSync(path.join(os.tmpdir(), "search-agent-demo-frames-"));
const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), "search-agent-demo-profile-"));
const query =
  "帮我找 5 年以上后端经验，必须有高并发和分布式系统经验，上海或杭州优先，排除只有运维经历的候选人。";

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
    "--hide-scrollbars",
    "--no-first-run",
    "--no-default-browser-check",
    "--force-device-scale-factor=1",
    `--window-size=${viewportWidth},${viewportHeight}`,
    "--remote-debugging-pipe",
    `--user-data-dir=${profileDir}`,
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"] },
);

let nextId = 1;
let buffer = Buffer.alloc(0);
const pending = new Map();
const eventHandlers = new Map();

child.stdio[4].on("data", (chunk) => {
  buffer = Buffer.concat([buffer, chunk]);
  let separator;
  while ((separator = buffer.indexOf(0)) >= 0) {
    const raw = buffer.subarray(0, separator).toString("utf8");
    buffer = buffer.subarray(separator + 1);
    if (!raw) continue;
    const message = JSON.parse(raw);
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

const frames = [];
let recording = false;
let screencastSessionId;

eventHandlers.set("Page.screencastFrame", async (params, sessionId) => {
  if (recording) {
    const index = String(frames.length).padStart(6, "0");
    const file = path.join(framesDir, `frame-${index}.jpg`);
    fs.writeFileSync(file, Buffer.from(params.data, "base64"));
    frames.push({ file, ts: Number(params.metadata?.timestamp || 0) });
  }
  await send("Page.screencastFrameAck", { sessionId: params.sessionId }, sessionId).catch(() => {});
});

const overlayScript = `
(() => {
  if (document.getElementById('demo-overlay')) return true;
  const style = document.createElement('style');
  style.textContent = \`
    #demo-overlay {
      position: fixed; inset: 0; z-index: 2147483646; display: flex; align-items: center;
      padding: 8vw; box-sizing: border-box; color: #f6f1e7; background: #0b2b26;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
      opacity: 0; transition: opacity .55s ease; pointer-events: none;
    }
    #demo-overlay.is-visible { opacity: 1; }
    #demo-overlay .demo-kicker {
      margin-bottom: 28px; color: #b9d7cc; font-size: 17px; font-weight: 700;
      letter-spacing: .18em; text-transform: uppercase;
    }
    #demo-overlay h1 {
      margin: 0; max-width: 1100px; font-size: 76px; line-height: 1.16; letter-spacing: -.04em;
    }
    #demo-overlay p { margin: 30px 0 0; color: #d7e4de; font-size: 27px; line-height: 1.7; }
    #demo-overlay .demo-tags { display: flex; gap: 14px; margin-top: 42px; flex-wrap: wrap; }
    #demo-overlay .demo-tags span {
      padding: 10px 16px; border: 1px solid rgba(214, 234, 226, .34); border-radius: 999px;
      color: #dce9e3; font-size: 17px;
    }
    #demo-overlay .demo-foot { margin-top: 48px; color: #8fb0a5; font-size: 15px; }
    #demo-pointer {
      position: fixed; top: 0; left: 0; z-index: 2147483645; width: 14px; height: 14px;
      margin: -7px 0 0 -7px; border: 2px solid #0b2b26; border-radius: 50%; background: #fffaf0;
      box-shadow: 0 0 0 7px rgba(11, 43, 38, .16); opacity: 0; pointer-events: none;
      transition: transform .42s cubic-bezier(.2,.7,.2,1), opacity .18s ease;
    }
    #demo-pointer.is-visible { opacity: 1; }
  \`;
  document.head.append(style);
  const overlay = document.createElement('div');
  overlay.id = 'demo-overlay';
  document.body.append(overlay);
  const pointer = document.createElement('div');
  pointer.id = 'demo-pointer';
  document.body.append(pointer);

  window.__demoShowIntro = () => {
    overlay.innerHTML = \`
      <div>
        <div class="demo-kicker">Search Agent Harness</div>
        <h1>从一次查询，<br>到搜索策略闭环</h1>
        <p>不是检索不行，是“决定怎么搜”这件事一直没人做。</p>
        <div class="demo-tags"><span>8 阶段状态机</span><span>预算受控</span><span>证据可追溯</span></div>
        <div class="demo-foot">演示数据均为虚构 · 离线快速演示 · 无需 API Key</div>
      </div>\`;
    overlay.classList.add('is-visible');
  };
  window.__demoShowEnd = () => {
    overlay.innerHTML = \`
      <div>
        <div class="demo-kicker">一条需求如何变成名单</div>
        <h1>搜什么 · 从哪搜<br>够不够 · 要不要换</h1>
        <p>8 个阶段 · 1 轮反思 · 16 位候选人 · 全链路可观测</p>
        <div class="demo-tags"><span>策略规划</span><span>能力判断</span><span>覆盖检查</span><span>证据交付</span></div>
        <div class="demo-foot">Search Agent Harness · 可复现的搜索策略闭环</div>
      </div>\`;
    overlay.classList.add('is-visible');
  };
  window.__demoHideOverlay = () => overlay.classList.remove('is-visible');
  window.__demoPointer = (selector) => new Promise((resolve) => {
    const target = document.querySelector(selector);
    const dot = document.getElementById('demo-pointer');
    if (!target || !dot) return resolve(false);
    const rect = target.getBoundingClientRect();
    dot.style.transform = 'translate(' + (rect.left + rect.width / 2) + 'px,' + (rect.top + rect.height / 2) + 'px)';
    dot.classList.add('is-visible');
    setTimeout(() => resolve(true), 430);
  });
  window.__demoHidePointer = () => document.getElementById('demo-pointer')?.classList.remove('is-visible');
  window.__demoType = async (selector, text, delay) => {
    const input = document.querySelector(selector);
    if (!input) return false;
    input.focus();
    input.value = '';
    for (let index = 0; index < text.length; index += 1) {
      input.value = text.slice(0, index + 1);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  };
  window.__demoScrollBy = (selector, delta, duration) => new Promise((resolve) => {
    const element = document.querySelector(selector);
    if (!element) return resolve(false);
    element.style.scrollBehavior = 'auto';
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
    scroller.style.scrollBehavior = 'auto';
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
})()
`;

async function main() {
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
  await waitFor(sessionId, "document.readyState === 'complete' && !!document.getElementById('chat-query')", 15000);
  await evaluate(sessionId, overlayScript);
  await sleep(500);

  screencastSessionId = sessionId;
  await send(
    "Page.startScreencast",
    { format: "jpeg", quality: 92, maxWidth: viewportWidth, maxHeight: viewportHeight, everyNthFrame: 1 },
    sessionId,
  );
  recording = true;
  const startedAt = Date.now();
  const log = (message) => console.log(`[${((Date.now() - startedAt) / 1000).toFixed(1)}s] ${message}`);

  log("intro");
  await evaluate(sessionId, "window.__demoShowIntro(); true");
  await sleep(3200);
  await evaluate(sessionId, "window.__demoHideOverlay(); true");
  await sleep(850);

  log("sidebar");
  await evaluate(sessionId, "window.__demoPointer('#sidebar-collapse')");
  await evaluate(sessionId, "document.getElementById('sidebar-collapse').click(); window.__demoHidePointer(); true");
  await sleep(900);
  await evaluate(sessionId, "window.__demoPointer('#sidebar-rail-toggle')");
  await evaluate(sessionId, "document.getElementById('sidebar-rail-toggle').click(); window.__demoHidePointer(); true");
  await sleep(800);

  log("typing");
  await evaluate(sessionId, "window.__demoPointer('#chat-query')");
  await evaluate(sessionId, `window.__demoType('#chat-query', ${JSON.stringify(query)}, 58)`);
  await sleep(500);

  log("submit");
  await evaluate(sessionId, "window.__demoPointer('#chat-run')");
  await evaluate(sessionId, "document.getElementById('chat-form').requestSubmit(); window.__demoHidePointer(); true");

  log("waiting for delivery");
  await waitFor(sessionId, "document.body.innerText.includes('本次搜索已完成')", 30000);
  await sleep(1300);

  log("delivery");
  await evaluate(
    sessionId,
    "window.__demoScrollTo('#chat-scroll', '.chat-result-message.agent-delivery', 10, 1200)",
  );
  await sleep(1700);
  await evaluate(sessionId, "window.__demoScrollBy('#chat-scroll', 420, 5200)");
  await sleep(900);

  log("observability");
  await evaluate(sessionId, "window.__demoPointer('#view-console')");
  await evaluate(sessionId, "document.getElementById('view-console').click(); window.__demoHidePointer(); true");
  await sleep(1500);
  await waitFor(sessionId, "document.querySelectorAll('#results li').length > 0", 8000);
  await evaluate(
    sessionId,
    "(() => { const timeline = document.querySelector('.timeline-viewport'); const results = document.querySelector('.results-viewport'); if (timeline) timeline.scrollTop = 0; if (results) results.scrollTop = 0; window.scrollTo(0, 0); return true; })()",
  );
  await sleep(1200);
  await evaluate(sessionId, "window.__demoScrollBy('.timeline-viewport', 430, 5200)");
  await sleep(900);
  await evaluate(sessionId, "window.__demoScrollBy('.results-viewport', 360, 4200)");
  await sleep(1200);

  log("outro");
  await evaluate(sessionId, "window.__demoShowEnd(); true");
  await sleep(3600);

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
    const duration = next ? Math.max(0.001, next.ts - current.ts) : 1.4;
    totalDuration += duration;
    lines.push(`file '${current.file.replace(/\\/g, "/").replace(/'/g, "'\\''")}'`);
    lines.push(`duration ${duration.toFixed(6)}`);
  }
  lines.push(`file '${frames[frames.length - 1].file.replace(/\\/g, "/").replace(/'/g, "'\\''")}'`);
  fs.writeFileSync(concatPath, lines.join("\n"), "utf8");

  console.log(`Encoding ${totalDuration.toFixed(1)}s to ${outputPath}`);
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
      "19",
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
      outputPath,
    ],
    { stdio: "inherit" },
  );
  if (encode.status !== 0) throw new Error(`ffmpeg failed with exit code ${encode.status}`);

  const poster = spawnSync(
    ffmpegPath,
    ["-y", "-hide_banner", "-loglevel", "error", "-ss", "15.5", "-i", outputPath, "-frames:v", "1", posterPath],
    { stdio: "inherit" },
  );
  if (poster.status !== 0) console.warn("Poster frame was not generated.");

  console.log(`Saved ${outputPath}`);
  if (poster.status === 0) console.log(`Saved ${posterPath}`);
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
