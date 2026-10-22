const CACHE_PREFIX = "search-agent:candidate:";
const SESSION_PAGE_LIMIT = 100;
const MAX_SESSION_PAGES = 50;

const params = new URLSearchParams(location.search);
const docId = (params.get("docId") || "").trim();
const queryToken = (params.get("token") || "").trim();
const rankFromUrl = normalizeRank(params.get("rank"));
const returnTo = safeReturnTo(params.get("returnTo"));

const $ = (id) => document.getElementById(id);
const loadingState = $("resume-loading");
const errorState = $("resume-error");
const errorTitle = $("resume-error-title");
const errorCopy = $("resume-error-copy");
const retryButton = $("resume-retry");
const printButton = $("print-resume");
const sheet = $("resume-sheet");
const detailNotice = $("resume-detail-notice");

let loading = false;

for (const link of document.querySelectorAll("[data-back-link]")) {
  link.href = returnTo;
}

printButton.addEventListener("click", () => window.print());
retryButton.addEventListener("click", () => void loadFromSession());

if (!docId) {
  showError(
    "缺少候选人信息",
    "当前链接没有包含可识别的候选人编号，请返回检索结果后重新打开。",
  );
} else {
  const cached = readCachedCandidate();
  const token = queryToken || cached?.token || "";
  if (cached && hasResumeDetails(cached.item)) {
    renderResume(cached.item, cached.rank);
    if (token) void loadFromSession({ fallback: cached, background: true });
  } else if (token) {
    void loadFromSession({ fallback: cached });
  } else if (cached) {
    renderResume(cached.item, cached.rank);
    showDetailNotice(
      "当前浏览器只缓存了候选人概要，工作、项目和教育经历可能没有加载完整。请返回检索页，重新打开该候选人。",
    );
  } else {
    showError(
      "简历详情已过期",
      "浏览器中没有这份简历的临时快照，检索会话也可能已经过期。请返回检索页，重新打开该候选人。",
    );
  }
}

async function loadFromSession({ fallback = null, background = false } = {}) {
  if (loading) return;
  const token = queryToken || fallback?.token || "";
  if (!token) {
    if (fallback) {
      renderResume(fallback.item, fallback.rank, { preserveScroll: background });
      showDetailNotice(
        "当前浏览器只缓存了候选人概要，工作、项目和教育经历可能没有加载完整。请返回检索页，重新打开该候选人。",
      );
      return;
    }
    showError(
      "简历详情已过期",
      "检索会话已失效。请返回检索页，重新打开该候选人。",
    );
    return;
  }

  loading = true;
  if (!background) {
    retryButton.hidden = true;
    loadingState.hidden = false;
    errorState.hidden = true;
    sheet.hidden = true;
    printButton.hidden = true;
  }

  try {
    const item = await fetchSessionCandidate(token);
    if (item) {
      const rank = fallback?.rank ?? rankFromUrl;
      renderResume(item, rank, { preserveScroll: background });
      hideDetailNotice();
      rememberCandidate(item, rank, token);
      return;
    }

    throw resumeError(
      "missing",
      "没有找到这份简历",
      "本次检索结果中没有找到该候选人，结果可能已经更新。请返回检索页后重新打开。",
    );
  } catch (err) {
    if (fallback) {
      renderResume(fallback.item, fallback.rank, { preserveScroll: background });
      if (background && hasResumeDetails(fallback.item)) return;
      showDetailNotice(
        err?.kind === "expired"
          ? "检索会话已过期，完整经历未能同步。当前仅展示浏览器缓存中的概要，请返回检索页重新搜索后打开。"
          : "完整经历暂时无法加载，当前展示浏览器缓存中的概要。请稍后重试，或返回检索页重新打开。",
      );
      return;
    }
    if (err?.kind === "expired" || err?.kind === "missing") {
      showError(err.message, err.detail);
    } else {
      showError(
        "简历暂时无法加载",
        err?.message || "网络连接中断，请稍后重试。",
        { retry: true },
      );
    }
  } finally {
    loading = false;
  }
}

async function fetchSessionCandidate(token) {
  let offset = 0;
  for (let page = 0; page < MAX_SESSION_PAGES; page += 1) {
    const response = await fetch(
      `/sessions/${encodeURIComponent(token)}?offset=${offset}&limit=${SESSION_PAGE_LIMIT}&companyId=1`,
      { cache: "no-store" },
    );
    if (response.status === 404) {
      throw resumeError(
        "expired",
        "简历详情已过期",
        "检索会话仅保留 1 小时。请返回检索页，重新运行搜索后打开候选人简历。",
      );
    }
    if (!response.ok) {
      throw resumeError(
        "network",
        "简历暂时无法加载",
        `读取检索会话失败（HTTP ${response.status}）。请稍后重试。`,
      );
    }

    const payload = await response.json();
    const items = Array.isArray(payload.items) ? payload.items : [];
    const item = items.find((row) => String(row?.docId || "") === docId);
    if (item) return item;
    if (!payload.hasMore || !items.length) break;
    const nextOffset = Number(payload.nextOffset);
    if (!Number.isFinite(nextOffset) || nextOffset <= offset) break;
    offset = nextOffset;
  }
  return null;
}

function resumeError(kind, message, detail) {
  const error = new Error(message);
  error.kind = kind;
  error.detail = detail;
  return error;
}

function showError(title, copy, { retry = false } = {}) {
  loadingState.hidden = true;
  sheet.hidden = true;
  printButton.hidden = true;
  hideDetailNotice();
  errorTitle.textContent = title;
  errorCopy.textContent = copy;
  retryButton.hidden = !retry;
  errorState.hidden = false;
}

function readCachedCandidate() {
  try {
    const raw = localStorage.getItem(`${CACHE_PREFIX}${docId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    const item = parsed?.item && typeof parsed.item === "object" ? parsed.item : parsed;
    if (!item || String(item.docId || "") !== docId) return null;
    return {
      item,
      rank: normalizeRank(parsed?.rank) ?? rankFromUrl,
      token: typeof parsed?.token === "string" ? parsed.token : "",
    };
  } catch (err) {
    return null;
  }
}

function rememberCandidate(item, rank, token) {
  try {
    localStorage.setItem(
      `${CACHE_PREFIX}${docId}`,
      JSON.stringify({
        version: 2,
        item,
        rank,
        token: token || null,
        cachedAt: Date.now(),
      }),
    );
  } catch (err) {
    // Session refresh still works when browser storage is unavailable.
  }
}

function hasResumeDetails(item) {
  const snapshot = objectValue(item.profileSnapshot);
  return (
    hasRows(item.workExperiences, snapshot.workExperiences) ||
    hasRows(item.internshipExperiences, snapshot.internshipExperiences) ||
    hasRows(item.projectExperiences, snapshot.projectExperiences) ||
    hasRows(item.educations, snapshot.educations)
  );
}

function hasRows(...groups) {
  return groups.some(
    (group) => Array.isArray(group) && group.some((row) => isRecord(row)),
  );
}

function showDetailNotice(message) {
  detailNotice.textContent = message;
  detailNotice.hidden = false;
}

function hideDetailNotice() {
  if (!detailNotice) return;
  detailNotice.textContent = "";
  detailNotice.hidden = true;
}

function renderResume(item, rank, { preserveScroll = false } = {}) {
  const snapshot = objectValue(item.profileSnapshot);
  const overview = objectValue(snapshot.overview);
  const intention = objectValue(snapshot.intention);
  const name = firstText(item.name, overview.name, overview.candidateName) || "候选人";
  const workYears = firstText(item.workYears, overview.workYears, overview.work_years);
  const age = firstText(item.age, overview.age);
  const city = firstText(
    item.currentResidence,
    overview.currentResidence,
    overview.current_residence,
    firstText(overview.city),
  );
  const workState = firstText(item.workState, intention.work_state, intention.workState);
  const summary = firstText(item.summary, overview.self_evaluation, item.summaryEn);
  const tags = uniqueNames(item.tags, snapshot.tags);
  const skills = uniqueNames(item.skills, snapshot.skills);
  const certificates = uniqueNames(item.certificates);
  const languages = uniqueNames(item.languages);
  const expectedPositions = uniqueNames(item.expectedPositions, intention.positions);
  const expectedCities = uniqueNames(item.expectedCities, intention.cities);
  const expectedIndustries = uniqueNames(item.expectedIndustries, intention.industries);
  const expectedSalary = firstText(item.expectedSalary, intention.expected_salary);
  const expectedWorkType = firstText(item.expectedWorkType, intention.work_type, intention.workType);
  const work = sortRecent(
    rowsOf(item.workExperiences, snapshot.workExperiences).map(normalizeWorkRow).filter(hasHistory),
  );
  const projects = sortRecent(
    rowsOf(item.projectExperiences, snapshot.projectExperiences)
      .map(normalizeProjectRow)
      .filter(hasHistory),
  );
  const education = sortRecent(
    rowsOf(item.educations, snapshot.educations).map(normalizeEducationRow).filter(hasHistory),
  );
  const internships = sortRecent(
    rowsOf(item.internshipExperiences, snapshot.internshipExperiences)
      .map(normalizeWorkRow)
      .filter(hasHistory),
  );

  document.title = `${name} · 候选人简历`;
  $("resume-initial").textContent = Array.from(name)[0] || "候";
  $("resume-name").textContent = name;

  const latestWork = work[0];
  const currentRole = latestWork
    ? [latestWork.title, latestWork.org].filter(Boolean).join(" · ")
    : "";
  const roleNode = $("resume-current-role");
  roleNode.textContent = currentRole;
  roleNode.hidden = !currentRole;

  const summaryNode = $("resume-summary");
  summaryNode.textContent = summary;
  summaryNode.hidden = !summary;

  renderQuickFacts({ rank, workYears, city, age, workState });
  renderBasics({ workYears, city, age, workState, tags, docId });
  renderIntention({
    expectedPositions,
    expectedCities,
    expectedIndustries,
    expectedSalary,
    expectedWorkType,
  });
  renderSkills(skills);
  renderCredentials(certificates, languages);
  renderHistorySection("resume-work", work, "段");
  renderHistorySection("resume-projects", projects, "段");
  renderHistorySection("resume-education", education, "条");
  renderHistorySection("resume-internships", internships, "段");

  $("resume-body-empty").hidden = Boolean(
    work.length || projects.length || education.length || internships.length,
  );
  loadingState.hidden = true;
  errorState.hidden = true;
  sheet.hidden = false;
  printButton.hidden = false;
  if (!preserveScroll) window.scrollTo(0, 0);
}

function renderQuickFacts({ rank, workYears, city, age, workState }) {
  const host = $("resume-quick-facts");
  host.replaceChildren();
  appendQuickFact(host, "检索排序", rank == null ? "" : `第 ${rank} 位`);
  appendQuickFact(host, "工作年限", formatYears(workYears));
  appendQuickFact(host, "所在城市", city);
  appendQuickFact(host, "年龄", age ? `${age} 岁` : "");
  appendQuickFact(host, "当前状态", workState);
}

function appendQuickFact(host, label, value) {
  if (!value) return;
  const row = document.createElement("div");
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  row.append(term, detail);
  host.append(row);
}

function renderBasics({ workYears, city, age, workState, tags, candidateId }) {
  const host = $("resume-basics");
  host.replaceChildren();
  appendFact(host, "工作年限", formatYears(workYears));
  appendFact(host, "所在城市", city);
  appendFact(host, "年龄", age ? `${age} 岁` : "");
  appendFact(host, "当前状态", workState);
  appendFact(host, "人才标签", tags.join("、"));
  appendFact(host, "候选人 ID", candidateId, { mono: true });
  $("resume-basics-section").hidden = !host.childElementCount;
}

function renderIntention({
  expectedPositions,
  expectedCities,
  expectedIndustries,
  expectedSalary,
  expectedWorkType,
}) {
  const host = $("resume-intention");
  host.replaceChildren();
  appendFact(host, "期望职位", expectedPositions.join("、"));
  appendFact(host, "期望城市", expectedCities.join("、"));
  appendFact(host, "期望行业", expectedIndustries.join("、"));
  appendFact(host, "期望薪资", expectedSalary);
  appendFact(host, "工作类型", expectedWorkType);
  $("resume-intention-section").hidden = !host.childElementCount;
}

function appendFact(host, label, value, { mono = false } = {}) {
  if (!value) return;
  const row = document.createElement("div");
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  if (mono) detail.classList.add("is-mono");
  row.append(term, detail);
  host.append(row);
}

function renderSkills(skills) {
  const host = $("resume-skills");
  host.replaceChildren();
  for (const skill of skills) {
    const item = document.createElement("li");
    item.textContent = skill;
    host.append(item);
  }
  $("resume-skills-section").hidden = !skills.length;
}

function renderCredentials(certificates, languages) {
  const host = $("resume-credentials");
  host.replaceChildren();
  appendCredentialGroup(host, "证书", certificates);
  appendCredentialGroup(host, "语言", languages);
  $("resume-credentials-section").hidden = !host.childElementCount;
}

function appendCredentialGroup(host, label, values) {
  if (!values.length) return;
  const group = document.createElement("div");
  group.className = "resume-credential-group";
  const heading = document.createElement("p");
  heading.className = "resume-credential-label";
  heading.textContent = label;
  const list = document.createElement("ul");
  list.className = "resume-credential-list";
  for (const value of values) {
    const item = document.createElement("li");
    item.textContent = value;
    list.append(item);
  }
  group.append(heading, list);
  host.append(group);
}

function renderHistorySection(prefix, rows, unit) {
  const section = $(`${prefix}-section`);
  const list = $(`${prefix}-list`);
  const count = $(`${prefix}-count`);
  list.replaceChildren();
  if (!rows.length) {
    section.hidden = true;
    count.textContent = "";
    return;
  }

  for (const row of rows) {
    const item = document.createElement("li");
    if (!row.date) item.classList.add("is-undated");

    if (row.date) {
      const date = document.createElement("div");
      date.className = "resume-history-date";
      date.textContent = row.date;
      item.append(date);
    }

    const copy = document.createElement("div");
    copy.className = "resume-history-copy";
    const title = document.createElement("h3");
    title.className = "resume-history-title";
    title.textContent = row.title;
    copy.append(title);

    if (row.org) {
      const org = document.createElement("p");
      org.className = "resume-history-org";
      org.textContent = row.org;
      copy.append(org);
    }
    if (row.meta) {
      const meta = document.createElement("span");
      meta.className = "resume-history-meta";
      meta.textContent = row.meta;
      copy.append(meta);
    }
    if (row.description) {
      const description = document.createElement("p");
      description.className = "resume-history-description";
      description.textContent = row.description;
      copy.append(description);
    }

    item.append(copy);
    list.append(item);
  }

  count.textContent = `${rows.length} ${unit}`;
  section.hidden = false;
}

function normalizeWorkRow(raw) {
  const company = firstText(
    raw.company_name,
    raw.companyName,
    raw.company,
    raw.employer,
    raw.orgName,
    raw.org_name,
  );
  const jobTitle = firstText(
    raw.job_title,
    raw.jobTitle,
    raw.job,
    raw.position,
    raw.positionName,
    raw.title,
  );
  return {
    title: jobTitle || company || "工作经历",
    org: jobTitle && company ? company : "",
    description: firstText(raw.description, raw.job_description, raw.jobDescription),
    date: formatRowRange(raw),
    start: rowStart(raw),
    end: rowEnd(raw),
    meta: "",
  };
}

function normalizeProjectRow(raw) {
  const projectName = firstText(raw.project_name, raw.projectName, raw.name, raw.title);
  const role = firstText(raw.role, raw.job_title, raw.jobTitle, raw.position);
  return {
    title: projectName || role || "项目经历",
    org: projectName && role ? role : "",
    description: firstText(raw.description, raw.project_description, raw.projectDescription),
    date: formatRowRange(raw),
    start: rowStart(raw),
    end: rowEnd(raw),
    meta: "",
  };
}

function normalizeEducationRow(raw) {
  const school = firstText(
    firstText(raw.school, raw.school_name, raw.schoolName),
    firstText(raw.college),
  );
  const major = firstText(
    firstText(raw.major, raw.major_name, raw.majorName),
    firstText(raw.subject),
  );
  const degree = firstText(
    firstText(raw.degree, raw.degree_name, raw.degreeName),
    firstText(raw.educationLevel),
  );
  return {
    title: school || major || "教育经历",
    org: school ? [major, degree].filter(Boolean).join(" · ") : degree,
    description: firstText(raw.description),
    date: formatRowRange(raw),
    start: rowStart(raw),
    end: rowEnd(raw),
    meta: "",
  };
}

function rowStart(raw) {
  return firstText(raw.startDate, raw.start_date, raw.startTime, raw.start_time);
}

function rowEnd(raw) {
  return firstText(raw.endDate, raw.end_date, raw.endTime, raw.end_time);
}

function formatRowRange(raw) {
  return formatRange(rowStart(raw), rowEnd(raw));
}

function hasHistory(row) {
  return Boolean(row.title || row.org || row.description || row.date);
}

function sortRecent(rows) {
  return rows
    .map((row, index) => ({
      row,
      index,
      start: dateValue(row.start),
      end: row.end ? dateValue(row.end) : Number.POSITIVE_INFINITY,
    }))
    .sort((left, right) => {
      if (left.end !== right.end) return right.end - left.end;
      if (left.start !== right.start) return right.start - left.start;
      return left.index - right.index;
    })
    .map((entry) => entry.row);
}

function dateValue(value) {
  const text = firstText(value);
  const match = text.match(/(\d{4})(?:[-/.年](\d{1,2}))?/);
  if (match) {
    const year = Number(match[1]);
    const month = Number(match[2] || 1);
    return year * 12 + month;
  }
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatRange(start, end) {
  const startText = formatMonth(start);
  const endText = formatMonth(end);
  if (!startText && !endText) return "";
  if (startText && !endText) return `${startText} — 至今`;
  if (!startText) return endText;
  return `${startText} — ${endText}`;
}

function formatMonth(value) {
  const text = firstText(value);
  if (!text) return "";
  const match = text.match(/(\d{4})(?:[-/.年](\d{1,2}))?/);
  if (!match) return text;
  if (!match[2]) return match[1];
  return `${match[1]}.${String(Number(match[2])).padStart(2, "0")}`;
}

function formatYears(value) {
  const text = firstText(value);
  if (!text) return "";
  const number = Number(text);
  if (Number.isFinite(number)) return `${number} 年经验`;
  return text.includes("年") ? text : `${text} 年经验`;
}

function rowsOf(primary, fallback) {
  if (Array.isArray(primary) && primary.length) return primary.filter(isRecord);
  if (Array.isArray(fallback) && fallback.length) return fallback.filter(isRecord);
  return [];
}

function uniqueNames(...groups) {
  const names = [];
  const seen = new Set();
  for (const group of groups) {
    if (!Array.isArray(group)) continue;
    for (const raw of group) {
      const name = firstText(raw);
      const key = name.toLocaleLowerCase();
      if (!name || seen.has(key)) continue;
      seen.add(key);
      names.push(name);
    }
  }
  return names;
}

function firstText(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const name = firstText(
        value.name,
        value.name_not_analyzed,
        value.nameNotAnalyzed,
        value.label,
        value.title,
        value.value,
      );
      if (name) return name;
    }
  }
  return "";
}

function objectValue(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeRank(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 1 ? Math.trunc(number) : null;
}

function safeReturnTo(value) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/";
  try {
    const url = new URL(value, location.origin);
    if (url.origin !== location.origin || url.pathname === "/resume.html") return "/";
    return `${url.pathname}${url.search}${url.hash}`;
  } catch (err) {
    return "/";
  }
}
