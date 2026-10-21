"""Prompt + schema builders for the four Agent LLM roles.

Kept minimal for Phase 0; richer rules land with prompt tuning. Each builder
returns (system_prompt, json_schema) so stages stay schema-agnostic.
"""

from __future__ import annotations

from typing import Any

_AGENT_BASE = (
    "你是招聘搜索 Agent 的一部分。所有时间相关推理必须以输入中的 currentTime 为唯一基准，"
    "禁止使用模型自身训练截止时间。只输出严格 JSON，不要输出任何额外文本。"
)

_UNDERSTAND_RULES = '你的角色：岗位分析师。把查询拆成岗位能力点 + 硬约束 + 难度判定 + 初始人才画像。\n必须严格按以下 JSON 结构输出（不要改字段名，不要增减字段）：\n{\n  "raw_query": "原查询",\n  "title": "岗位标题",\n  "must_abilities": [{"name": "能力名", "level": "must", "evidence_hints": ["佐证词"]}],\n  "nice_abilities": [{"name": "能力名", "level": "nice", "evidence_hints": []}],\n  "hard_constraints": [\n    {"field": "skill", "operator": "any", "values": ["Python"], "required": true, "weight": 1.0, "softText": null},\n    {"field": "work_years", "operator": "gte", "values": [3], "required": true, "weight": 1.0, "softText": null}\n  ],\n  "subjective_difficulty": "low|mid|high",\n  "difficulty_reason": "判定理由",\n  "needs_clarification": false,\n  "clarify_questions": [],\n  "talent_profile": {\n    "source_hypotheses": [{"id": "s1", "kind": "company", "value": "某公司", "rationale": "理由", "transfer_from": []}],\n    "must_keywords": ["Python"],\n    "soft_signals": [{"text": "分布式系统经验", "fields": ["work_vector", "project_vector"], "weight": 1.0}],\n    "risk_filters": []\n  }\n}\n规则：\n- must_abilities / nice_abilities 的 level 只能是 "must" 或 "nice"。\n- hard_constraints.field 必须是合法字段名，常用：name, skill, company, position, school, major, degree, city, work_years, age, graduation_year, talent_tag, certificate, language, latest_company, intention_position, intention_city, live_city, expected_salary, work_state。\n- hard_constraints.operator 只能是：eq, any, all, not, gte, lte, range。\n- 数值字段（work_years/age/graduation_year 等）用 gte/lte/range + 数字 values；文本字段（skill/company/position 等）用 any/all + 字符串 values。\n- hard_constraints 可放两类约束，由你根据 query 性质判断：(1) 结构化约束（work_years/age/degree/graduation_year 等数值或学历类，可由岗位推理得出）；(2) 技能领域硬条件（skill），当 query 描述的能力直接指向某个技术领域时，应提取该领域的核心技能作为 skill 硬条件以缩小搜索域，例如「微服务架构演进」应提取 skill any=[微服务,Spring Cloud,Docker]，「高可用系统设计」应提取 skill any=[高可用,负载均衡,容灾]。但当 query 描述的是职业轨迹或通用软能力（如「技术骨干转管理」「跨团队协作」），不提取技能硬条件，只放结构化约束 + soft_signals。position/company 等字段只放 query 里明确写出的具体值，不要凭空补职位名（如「架构师」），那些应进 source_hypotheses。\n- talent_profile.soft_signals.fields 必须是以下向量字段之一或多个：work_vector, project_vector, education_vector, self_evaluation_vector, skills_vector。\n- talent_profile.source_hypotheses.kind 只能是：company, industry, role, product, project, skill_combo, transferable。\n- weight 取值 0.1~5.0。\n- 难度用于成本路由，不等于硬条件数量：简单、字段明确、单一路径即可完成=low；条件明确但存在多来源/多路径空间=mid；目标抽象、依赖可迁移能力、存在明显漏召风险或需要换向搜索=high。\n- needs_clarification 仅当查询严重歧义无法解析时才为 true，否则 false。\n\n多轮对话规则（如果提供了 conversation_history）：\n- 当前查询是在上一轮基础上的追加或细化。\n- “最好有XX”/“优先XX” = 追加偏好条件。\n- “不要XX”/“排除XX” = 排除条件。\n- “换个方向” = 替换部分历史条件。\n- 如果与历史完全无关，忽略历史按全新查询处理。\n- 输出必须包含合并后的完整条件。'


_UNDERSTAND_GUARD = """

【硬约束与难度护栏（优先于上方任何冲突规则）】
1. hard_constraints 只放明确不可放宽的资格门槛：年限、地点、学历、薪资、年龄、在职状态、明确排除项；技术技能默认不作为硬过滤。
2. “熟悉/掌握/了解/做过/有……经验”描述的是能力或偏好，不是硬过滤。技术栈与能力默认放入 must_abilities 或 nice_abilities，并写入 soft_signals。
3. “最好/优先/加分/有则更好”一律是 nice_ability，不得进入 hard_constraints，也不得进入 must_abilities。
4. 抽象能力（高并发、稳定性、架构、可迁移）通常应作为 must_abilities；具体技术实现栈只有在原文明确“必须/只要/仅限/硬性要求”时才是 must。
5. low 仅限所有条件都是明确结构化字段且单一路径即可完成；只要含抽象能力、能力组合、偏好或需要换向搜索，至少为 mid；存在能力可迁移或明显漏召风险时为 high。
6. 985/211 明确修饰“本科/第一学历/统招本科/全日制本科”时，hard_constraints 必须同时写 degree=本科 与 school_tier=985/211，禁止写 talent_tag；单独出现的 985/211 人才标签才使用 talent_tag。
"""


def understand_prompt() -> tuple[str, dict[str, Any]]:
    sys = _AGENT_BASE + _UNDERSTAND_RULES + _UNDERSTAND_GUARD
    schema = {
        "type": "object",
        "properties": {
            "raw_query": {"type": "string"},
            "title": {"type": "string"},
            "must_abilities": {"type": "array", "items": {"type": "object"}},
            "nice_abilities": {"type": "array", "items": {"type": "object"}},
            "hard_constraints": {"type": "array", "items": {"type": "object"}},
            "subjective_difficulty": {"type": "string", "enum": ["low", "mid", "high"]},
            "difficulty_reason": {"type": "string"},
            "needs_clarification": {"type": "boolean"},
            "clarify_questions": {"type": "array", "items": {"type": "string"}},
            "talent_profile": {"type": "object"},
        },
        "required": ["raw_query", "must_abilities", "needs_clarification", "talent_profile"],
        "additionalProperties": False,
    }
    return sys, schema


_PLAN_RULES = '你的角色：搜索路径规划。根据岗位画像 + 人才画像，产出 1~N 条搜索路径（N 不超过 max_paths），每条是一个不同的召回意图。\n必须严格按以下 JSON 结构输出（不要输出 paths 以外的顶层字段）：\n{\n  "paths": [\n    {\n      "rationale": "这条路径的意图说明",\n      "source_refs": ["s1"],\n      "expected_yield": "high",\n      "intent": {\n        "where": {\n          "type": "and",\n          "children": [\n            {"type": "leaf", "condition": {"field": "skill", "operator": "any", "values": ["Python"], "required": true, "weight": 1.0, "softText": null}},\n            {"type": "leaf", "condition": {"field": "work_years", "operator": "gte", "values": [3], "required": true, "weight": 1.0, "softText": null}}\n          ]\n        },\n        "semantic": [\n          {"text": "分布式系统经验", "fields": ["work_vector", "project_vector"], "weight": 1.5}\n        ],\n        "meta": {}\n      }\n    }\n  ]\n}\n规则：\n- intent.where 是布尔树。type 取值：and（children 数组）/ or（children 数组 + 可选 minimumShouldMatch）/ not（单个 child）/ leaf（单个 condition）。\n- leaf.condition 即结构化硬条件：{field, operator, values, required, weight, softText}。field/operator 取值与 understand 相同；数值字段（work_years/age/graduation_year）用 gte/lte/range + 数字 values，文本字段（skill/company/position 等）用 any/all + 字符串 values。\n- intent.semantic 是语义条件数组：每条为 {text, fields, weight}（必用 text，不要用 query）。fields 必须是 work_vector/project_vector/education_vector/self_evaluation_vector/skills_vector 之一或多个。\n- intent.meta 可为空对象 {}。\n- expected_yield 只能是：high, mid, low, exploratory。\n- source_refs 引用人才画像里的 source id（如 s1），没有就空数组 []。\n- 多条路径必须沿不同维度搜索（Wide Talent Search），不要所有路径都是同一维度的精度调整或语义放宽。\n- 维度参考：岗位名(position含上下游/相似岗位)、公司(company/latest_company)、行业(semantic on work_vector)、产品类型(semantic on project_vector)、项目经验、技能组合、可迁移背景、相似业务场景。\n- 当 max_paths >= 2 时，至少一条路径必须是来源维度路径：用 company/position 硬条件或行业/产品类语义信号召回，source_refs 引用对应 source hypothesis。\n- 其余路径可走精准硬条件或语义放宽，但各路径召回意图必须不同。\n- 单条叶子条件也可直接作为 where（type=leaf）。\n- strategyPriors（可选）：历史同类岗位的策略收益先验 [{strategy, runs, avg_qualified}]。runs >= 2 时优先复刻 avg_qualified 高的策略；样本不足则忽略。'


def plan_prompt() -> tuple[str, dict[str, Any]]:
    sys = (
        _AGENT_BASE
        + _PLAN_RULES
        + "\n- 若 job.hard_constraints 含 degree/school_tier，受其约束的每条路径都必须保留这两个教育硬条件，不能因放宽召回而丢弃。"
    )
    schema = {"type": "object", "properties": {"paths": {"type": "array"}}, "required": ["paths"]}
    return sys, schema


_JUDGE_RULES = '你的角色：能力级匹配判断。依据每个候选人的 hardMatches 事实 + 紧凑候选人 profile，判断其命中/缺失哪些岗位能力，不得编造经历。\n必须严格按以下 JSON 结构输出（不要输出 judgments 以外的顶层字段）：\n{\n  "judgments": [\n    {\n      "doc_id": "原样回填输入里该 candidate 的 docId",\n      "matched_abilities": ["命中的能力名"]\n    }\n  ]\n}\n规则：\n- 必须为输入中的每一个 candidate 都输出一条 judgment，数量与输入 candidates 一致。\n- doc_id 必须原样使用输入 candidates 里该候选人的 docId 值，禁止编造、改写，禁止用 name / candidateProfileId / 序号 代替。\n- matched_abilities 的取值必须来自输入的 abilities 列表；只输出命中的能力，未命中能力由程序自动补全，禁止输出 missing_abilities。\n- 只依据 hardMatches 事实和候选人 profile（workYears/skills/recentRoles/education/summary）判断，不得编造经历或技能。\n- 不要输出 summary、理由或额外字段；只返回能力判定，以减少输出 tokens。'


def judge_prompt() -> tuple[str, dict[str, Any]]:
    sys = _AGENT_BASE + _JUDGE_RULES
    schema = {
        "type": "object",
        "properties": {
            "judgments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "doc_id": {"type": "string"},
                        "matched_abilities": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "doc_id",
                        "matched_abilities",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["judgments"],
        "additionalProperties": False,
    }
    return sys, schema


_REFLECT_RULES = '你的角色：结果分析师。你会看到上一轮的 gaps、当前人才画像（soft_signals / source_hypotheses）、本轮路径结果与 top 判断。从好结果反推共同特征、识别能力缺口、给出 path_actions（加路径/放宽/收紧/丢弃/澄清）与 inferred_soft_signals（反推的语义信号，会沉淀进人才画像 soft_signals），让下一轮搜索更精准。\n必须严格按以下 JSON 结构输出（不要输出额外顶层字段）：\n{\n  "common_traits": ["好候选的共同特征"],\n  "gaps": ["当前结果的能力缺口"],\n  "inferred_soft_signals": [{"text": "金融科技", "fields": ["work_vector", "project_vector"], "weight": 1.5}],\n  "new_source_hints": [{"id": "s2", "kind": "company", "value": "某公司", "rationale": "理由", "transfer_from": []}],\n  "path_actions": [\n    {"kind": "add_path", "target_path_id": null, "rationale": "新增一条路径", "intent": {"where": {"type": "leaf", "condition": {"field": "skill", "operator": "any", "values": ["Go"], "required": true, "weight": 1.0, "softText": null}}, "semantic": [], "meta": {}}},\n    {"kind": "loosen", "target_path_id": "p1", "rationale": "放宽 p1 的硬条件", "intent": {"where": {"type": "leaf", "condition": {"field": "skill", "operator": "any", "values": ["Python", "Go"], "required": true, "weight": 1.0, "softText": null}}, "semantic": [], "meta": {}}},\n    {"kind": "drop", "target_path_id": "p2", "rationale": "丢弃无效路径", "intent": null}\n  ]\n}\n规则：\n- common_traits / gaps 是字符串数组。\n- inferred_soft_signals 是从好结果反推出的语义信号，每项 {text, fields, weight}；fields 只能是 work_vector/project_vector/education_vector/self_evaluation_vector/skills_vector；weight 0.1~5.0；不要与 talent_profile 已有 soft_signals 重复；没有就返回空数组 []。\n- prior_gaps 是上一轮识别的缺口，path_actions 应优先尝试弥补这些缺口。\n- new_source_hints 的 kind 只能是：company, industry, role, product, project, skill_combo, transferable。\n- new_source_hints 是 Wide Talent Search 的核心机制：从 top 候选人的真实公司/职位/项目背景中归纳可泛化的来源维度，用于扩大下一轮搜索的人才来源空间，找到关键词搜不到的可迁移人才。请认真分析 top 候选人的 company/title/skills，主动发现共同来源模式并输出以下维度：\n  · industry：候选人集中来自的行业类别（如「互联网/SaaS」「金融科技」「电商平台」），输出类别而非具体公司名\n  · role：能力可迁移的相似或上下游岗位（如「技术负责人」「基础架构工程师」）\n  · product：候选人参与过的产品/系统类型（如「高并发交易系统」「分布式中间件」），输出类型而非具体产品名\n  · skill_combo：多人共有的技能组合模式（如「Java+Spring Cloud+K8s」）\n  · company：仅限知名大厂/独角兽（如「字节跳动」「美团」），禁止单个冷门公司名\n  · transferable：可迁移背景（如「从传统行业转互联网」）\n每个 hint 须与岗位 must_abilities 相关且有归纳依据（来自候选人背景的共同模式）。首轮若候选人来源多样，仍应输出 industry 或 product 类别作为探索方向。确实无可泛化来源时才返回 []。\n- path_actions.kind 只能是：add_path, loosen, tighten, drop, clarify。\n- add_path / loosen / tighten 必须带 intent（一个完整的 CandidateSearchIR）；drop / clarify 的 intent 为 null。\n- intent 结构：{where: 布尔树, semantic: [语义条件], meta: {}}。where 用 type=and/or/not/leaf；leaf.condition={field,operator,values,required,weight,softText}（field/operator 同 understand；数值字段用 gte/lte/range+数字，文本字段用 any/all+字符串）。semantic 的 fields 必须是 work_vector/project_vector/education_vector/self_evaluation_vector/skills_vector 之一或多个。\n- target_path_id 引用现有路径 id（如 p1）；add_path 时为 null。\n- 没有改进空间时返回空 path_actions 数组 []，不要编造。\n- strategyPriors（可选）：历史同类岗位的策略收益先验 [{strategy, runs, avg_qualified}]，用于选择 path_actions 的 kind；样本不足（runs < 2）则忽略。'


def reflect_prompt() -> tuple[str, dict[str, Any]]:
    sys = _AGENT_BASE + _REFLECT_RULES
    schema = {
        "type": "object",
        "properties": {
            "common_traits": {"type": "array", "items": {"type": "string"}},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "inferred_soft_signals": {"type": "array"},
            "new_source_hints": {"type": "array"},
            "path_actions": {"type": "array"},
        },
        "required": ["common_traits", "gaps", "path_actions"],
        "additionalProperties": False,
    }
    return sys, schema


# ── Payload builders (information trimming) ──────────────────────────────
# Each stage calls these instead of json.dumps(state["xxx"].model_dump()).
# Trims large fields (profileSnapshot, evidence_hints, etc.) to cut tokens.


def build_understand_payload(
    query: str,
    current_time: str,
    conversation_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query, "currentTime": current_time}
    if conversation_history:
        payload["conversation_history"] = [
            {
                "turn": h.get("turn_index", 0),
                "query": h.get("query", ""),
                "conditions": h.get("job_profile_summary", {}),
                "result_count": h.get("result_count", 0),
            }
            for h in conversation_history[-3:]
        ]
    return payload


def build_plan_payload(
    job_profile: Any,
    talent_profile: Any,
    tried_signatures: list[str],
    max_paths: int,
    experience: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Trimmed plan input: only abilities + hard_constraints + sources.

    ``experience`` carries strategy priors from previous runs of the same job
    family (see ``core/experience.py``); the key is omitted when unknown.
    """
    payload: dict[str, Any] = {
        "job": {
            "title": job_profile.title,
            "must_abilities": [a.name for a in job_profile.must_abilities],
            "nice_abilities": [a.name for a in job_profile.nice_abilities],
            "hard_constraints": [c.model_dump() for c in job_profile.hard_constraints],
            "difficulty": job_profile.subjective_difficulty,
        },
        "talent": {
            "source_hypotheses": [s.model_dump() for s in talent_profile.source_hypotheses],
            "must_keywords": talent_profile.must_keywords,
            "soft_signals": [s.model_dump() for s in talent_profile.soft_signals],
        },
        "tried_signatures": tried_signatures,
        "max_paths": max_paths,
    }
    if experience:
        payload["strategyPriors"] = experience
    return payload


_MAX_JUDGE_SKILLS = 12
_MAX_JUDGE_ROLES = 2
_MAX_JUDGE_SUMMARY_CHARS = 160


def _compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _first_text(row: dict[str, Any], keys: tuple[str, ...], limit: int) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return _compact_text(value, limit)
    return ""


def _compact_experience(row: Any) -> dict[str, str] | None:
    if not isinstance(row, dict):
        return None
    company = _first_text(row, ("company_name", "companyName", "company"), 80)
    title = _first_text(row, ("job_title", "jobTitle", "positionName", "position"), 80)
    if not company and not title:
        return None
    start = _first_text(row, ("startDate", "start_date", "startTime", "start_time"), 20)
    end = _first_text(row, ("endDate", "end_date", "endTime", "end_time"), 20)
    compact: dict[str, str] = {}
    if company:
        compact["company"] = company
    if title:
        compact["title"] = title
    if start or end:
        compact["period"] = "~".join(part for part in (start, end) if part)
    return compact


def _experience_date(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    for key in (
        "endDate",
        "end_date",
        "endTime",
        "end_time",
        "startDate",
        "start_date",
        "startTime",
        "start_time",
    ):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def _compact_education(row: Any) -> dict[str, str] | None:
    if not isinstance(row, dict):
        return None
    degree = _first_text(row, ("degree", "degreeName"), 40)
    major = _first_text(row, ("major", "majorName"), 60)
    if not degree and not major:
        return None
    compact: dict[str, str] = {}
    if degree:
        compact["degree"] = degree
    if major:
        compact["major"] = major
    return compact


def _skill_names(rows: Any) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        if isinstance(row, dict):
            name = row.get("name") or row.get("skillName")
        else:
            name = row
        text = _compact_text(name, 60)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            names.append(text)
        if len(names) >= _MAX_JUDGE_SKILLS:
            break
    return names


def _judge_profile(item: Any) -> dict[str, Any]:
    """Return only the resume evidence needed for ability-level judging."""
    profile: dict[str, Any] = {
        "workYears": item.workYears,
        "skills": _skill_names(item.skills),
    }
    work_rows = sorted(
        (row for row in (item.workExperiences or []) if isinstance(row, dict)),
        key=_experience_date,
        reverse=True,
    )[:_MAX_JUDGE_ROLES]
    roles = [role for role in (_compact_experience(row) for row in work_rows) if role is not None]
    if roles:
        profile["recentRoles"] = roles
    education = [
        row
        for row in (_compact_education(row) for row in (item.educations or [])[:1])
        if row is not None
    ]
    if education:
        profile["education"] = education[0]
    summary = _compact_text(item.summary, _MAX_JUDGE_SUMMARY_CHARS)
    if summary:
        profile["summary"] = summary
    return profile


def build_judge_payload(
    query: str,
    abilities: list[str],
    candidates: list[Any],
) -> dict[str, Any]:
    """Compact judge input: hard-match facts plus key resume evidence only."""
    return {
        "query": query,
        "abilities": abilities,
        "candidates": [
            {
                "docId": c.doc_id,
                "hardMatches": c.hard_matches,
                "profile": _judge_profile(c.item),
            }
            for c in candidates
        ],
    }


def _where_summary(where_node: Any) -> list[str]:
    """Extract field=values from a WhereNode tree (compact for reflect)."""
    if where_node is None:
        return []
    out: list[str] = []
    d = where_node.model_dump() if hasattr(where_node, "model_dump") else where_node
    if isinstance(d, dict):
        if d.get("type") == "leaf":
            c = d.get("condition", {})
            out.append("%s%s=%s" % (c.get("field", ""), c.get("operator", ""), c.get("values", [])))
        for ch in d.get("children", []) or []:
            out.extend(_where_summary(ch))
    return out


def _candidate_brief(item: Any) -> dict[str, Any]:
    """Extract company/title/skills from a CandidateItem (compact for reflect)."""
    brief: dict[str, Any] = {}
    wes = getattr(item, "workExperiences", []) or []
    if wes and isinstance(wes[0], dict):
        brief["company"] = wes[0].get("company_name") or wes[0].get("companyName") or ""
        brief["title"] = (
            wes[0].get("job_title") or wes[0].get("positionName") or wes[0].get("position") or ""
        )
    brief["work_history"] = [
        {
            "company": we.get("company_name") or we.get("companyName") or "",
            "title": we.get("job_title") or we.get("positionName") or we.get("position") or "",
        }
        for we in wes[:3]
        if isinstance(we, dict)
    ]
    skills = getattr(item, "skills", []) or []
    brief["skills"] = [s.get("name", "") if isinstance(s, dict) else str(s) for s in skills[:5]]
    brief["workYears"] = getattr(item, "workYears", None)
    return brief


def build_reflect_payload(
    iteration: int,
    current_time: str,
    path_results: list[Any],
    top_judgments: list[Any],
    tried_signatures: list[str],
    prior_gaps: list[str] | None = None,
    talent_profile: Any = None,
    query: str = "",
    paths: list[Any] | None = None,
    candidate_lookup: dict[str, Any] | None = None,
    experience: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reflect input: query + path intents + candidate backgrounds.

    Includes the query, each path's rationale/where/semantic, and each top
    candidate's company/title/skills so the LLM can propose concrete
    path_actions and infer source dimensions from actual results.
    """
    path_map = {p.id: p for p in (paths or [])}
    pr_list: list[dict[str, Any]] = []
    for pr in path_results:
        sp = path_map.get(pr.path_id)
        entry: dict[str, Any] = {
            "path_id": pr.path_id,
            "quality": pr.quality,
            "recalled": pr.tool_result.total,
            "qualified": len(pr.judgments),
        }
        if sp is not None:
            entry["rationale"] = sp.rationale
            entry["where"] = _where_summary(sp.intent.where)
            entry["semantic"] = [s.text for s in (sp.intent.semantic or [])]
        pr_list.append(entry)

    tj_list: list[dict[str, Any]] = []
    for j in top_judgments:
        entry = {
            "doc_id": j.doc_id,
            "score": j.score,
            "matched_abilities": j.matched_abilities,
            "missing_abilities": j.missing_abilities,
            "summary": j.summary,
        }
        if candidate_lookup and j.doc_id in candidate_lookup:
            entry["candidate"] = _candidate_brief(candidate_lookup[j.doc_id])
        tj_list.append(entry)

    payload: dict[str, Any] = {
        "query": query,
        "iteration": iteration,
        "currentTime": current_time,
        "path_results": pr_list,
        "top_judgments": tj_list,
        "tried_signatures": tried_signatures,
        "prior_gaps": prior_gaps or [],
        "talent_profile": (
            {
                "soft_signals": [s.model_dump() for s in talent_profile.soft_signals],
                "source_hypotheses": [s.model_dump() for s in talent_profile.source_hypotheses],
            }
            if talent_profile is not None
            else None
        ),
    }
    if experience:
        payload["strategyPriors"] = experience
    return payload


__all__ = ["judge_prompt", "plan_prompt", "reflect_prompt", "understand_prompt"]
