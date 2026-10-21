"""NL to IR parser for Hybrid Search V2.

This is the standalone port of the original parser. It depends only on the
injected ``LlmClient``; provider selection, retries, fallback and token
accounting stay in the agent runtime.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from search_agent.core.cancellation import SearchCancelled
from search_agent.llm.client import LlmClient
from search_agent.models.ir import SOFT_VECTOR_FIELDS, CandidateSearchIR

from .work_tenure_intent import classify_work_tenure_intent

logger = logging.getLogger(__name__)
_MAX_PARSE_ATTEMPTS = 2
_DEGREE_LEVELS: tuple[tuple[str, str], ...] = (
    ("中专", "中专"),
    ("高中", "高中"),
    ("大专", "大专"),
    ("本科", "本科"),
    ("硕士", "硕士"),
    ("博士", "博士"),
    ("mba", "MBA"),
)
_DEGREE_SKIP_HINTS = ("不限", "不限制", "无要求", "无需", "非必须")
# Word-boundary style patterns; avoid 本科室 / 高级中学 / 专科医生 false positives.
_DEGREE_QUERY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"中专"), "中专"),
    (re.compile(r"(?<!高级)高中(?!中|更)"), "高中"),
    (re.compile(r"大专"), "大专"),
    (re.compile(r"(?<!专)专科(?!医生|医院|医师|科)"), "大专"),
    (re.compile(r"本科(?!室|院|部|生|点|科)"), "本科"),
    (re.compile(r"硕士"), "硕士"),
    (re.compile(r"研究生"), "硕士"),
    (re.compile(r"博士"), "博士"),
    (re.compile(r"mba", re.I), "MBA"),
)
_UNDERGRAD_STAGE_PATTERN = r"(?:第一学历|统招本科|全日制本科|本科)"
_EDUCATION_TIER_TOKEN_PATTERN = r"(?:985|211)(?:\s*[/、和或,，]\s*(?:985|211))*"
_EDUCATION_TIER_AFTER_STAGE_RE = re.compile(
    rf"{_UNDERGRAD_STAGE_PATTERN}(?:学历|阶段|期间)?[^，。；,;\n]{{0,8}}?"
    rf"({_EDUCATION_TIER_TOKEN_PATTERN})"
)
_EDUCATION_TIER_BEFORE_STAGE_RE = re.compile(
    rf"({_EDUCATION_TIER_TOKEN_PATTERN})(?:院校|大学)?[^，。；,;\n]{{0,6}}?"
    rf"{_UNDERGRAD_STAGE_PATTERN}"
)
_EDUCATION_TIER_NEGATION = ("不要", "排除", "不是", "非", "不考虑", "不要求")

IR_FIELD_ENUM = [
    "name",
    "skill",
    "company",
    "internship_company",
    "position",
    "internship_job",
    "intention_position",
    "school",
    "major",
    "degree",
    "school_tier",
    "city",
    "intention_city",
    "live_city",
    "work_years",
    "work_segment_count",
    "employment_gap",
    "latest_company",
    "latest_work_job",
    "recent_tenure_1",
    "max_tenure",
    "avg_tenure",
    "short_job_count",
    "job_stability",
    "inter_segment_gap",
    "max_inter_segment_gap",
    "career_discontinuity",
    "role_tenure",
    "graduation_year",
    "age",
    "birth_year",
    "gender",
    "work_state",
    "expected_salary",
    "talent_tag",
    "certificate",
    "language",
]

IR_OPERATOR_ENUM = ["eq", "any", "all", "not", "gte", "lte", "range"]

HYBRID_V2_IR_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "where": {"$ref": "#/definitions/whereNode"},
        "semantic": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "minLength": 1},
                    "fields": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "enum": sorted(SOFT_VECTOR_FIELDS)},
                    },
                    "weight": {"type": "number", "minimum": 0.1, "maximum": 5.0},
                },
                "required": ["text", "fields", "weight"],
                "additionalProperties": False,
            },
        },
        "sort": {"type": "array", "items": {"type": "object"}},
        "meta": {
            "type": "object",
            "properties": {
                "needClarification": {"type": "boolean"},
                "clarifyQuestions": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "array", "items": {"type": "string"}},
                "tenureIntent": {
                    "type": "string",
                    "enum": ["generic", "title", "recent"],
                },
                "expandScope": {"type": "string", "enum": ["peers"]},
                "peerHint": {
                    "type": "string",
                    "enum": ["industry_peers", "famous_background"],
                },
            },
            "required": ["needClarification", "clarifyQuestions", "notes"],
            "additionalProperties": False,
        },
    },
    "required": ["where", "semantic", "meta"],
    "additionalProperties": False,
    "definitions": {
        "structuredCondition": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": IR_FIELD_ENUM},
                "operator": {"type": "string", "enum": IR_OPERATOR_ENUM},
                "values": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "number"},
                            {"type": "boolean"},
                        ]
                    },
                },
                "required": {"type": "boolean"},
                "weight": {"type": "number", "minimum": 0.1, "maximum": 5.0},
                "softText": {"type": "string"},
            },
            "required": ["field", "operator", "values", "required", "weight"],
            "additionalProperties": False,
        },
        "whereNode": {
            "oneOf": [
                {"$ref": "#/definitions/whereAnd"},
                {"$ref": "#/definitions/whereOr"},
                {"$ref": "#/definitions/whereNot"},
                {"$ref": "#/definitions/whereLeaf"},
            ]
        },
        "whereAnd": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["and"]},
                "children": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"$ref": "#/definitions/whereNode"},
                },
            },
            "required": ["type", "children"],
            "additionalProperties": False,
        },
        "whereOr": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["or"]},
                "children": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"$ref": "#/definitions/whereNode"},
                },
                "minimumShouldMatch": {"type": "integer", "minimum": 1},
            },
            "required": ["type", "children"],
            "additionalProperties": False,
        },
        "whereNot": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["not"]},
                "child": {"$ref": "#/definitions/whereNode"},
            },
            "required": ["type", "child"],
            "additionalProperties": False,
        },
        "whereLeaf": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["leaf"]},
                "condition": {"$ref": "#/definitions/structuredCondition"},
            },
            "required": ["type", "condition"],
            "additionalProperties": False,
        },
    },
}

HYBRID_V2_SYSTEM_PROMPT_BODY = """你是招聘搜索意图解析器（Hybrid Search V2），同时以深耕招聘场景的资深 HR 视角理解 HR 的自然语言。你熟悉快消、零售、互联网、制造等主流行业的用人术语与筛选习惯。在招聘语境下，行业背景、业务经验、岗位名称、雇主品牌是不同维度——你的任务是把上述描述准确解析为 IR。
只输出满足 JSON Schema 的 JSON，不输出解释。

你的任务不是写 Elasticsearch DSL，而是输出 IR：
1. where：结构化条件的布尔表达式树（and/or/not/leaf）。
2. semantic：语义排序条件（向量检索）。
3. 禁止输出顶层 structured 字段（v2 已升级为 where AST-only）。

structured.field 只能取：
- name：候选人姓名；用户明确说「姓名/名字/候选人叫/名字完全是/姓名等于 + X」时，values 按完整姓名字面提取；「姓/姓氏 + X」表示姓氏查询，values 只写姓氏本身（如 杨、欧阳）；「名字中包含/含有 X」values 只写 X；「名字以 X 结尾」values 只写 X；若用户显式用 "_" 表示一个字，占位符可保留在 value 中（如 王_）
- skill：明确、原子化技能，如 Python、K8s、Java、ERP；英文短语经验不要放 skill
- company：明确公司经历，如 字节跳动、星巴克
- internship_company：明确实习经历中的公司/单位（实习过、实习于、实习单位、实习经历等），如 玛氏、仟寻
- position：仅表示工作经历中的岗位/职位（做过、担任过、职位为、曾任等），如 高级销售、采购经理
- internship_job：仅表示实习经历中的岗位/职位（实习做、实习担任、实习岗位、实习职位等），如 产品经理、测试工程师
- intention_position：仅表示求职期望职能/意向岗位（期望职能、意向岗位、想做、希望从事等），如 品牌顾问、高级销售
- school：明确学校，如 北京大学、北大、清华
- major：明确就读/所学专业，如 市场营销、计算机科学；与 position 区分，不要把专业名放进 position
- degree：学历，values 使用 学历名或代码，如 本科、硕士、5、6
- school_tier：教育经历级院校层次，values 使用 985 或 211；仅当 985/211 明确修饰「本科/第一学历/统招本科/全日制本科」时使用，必须与 degree 条件并列；禁止写成候选人级 talent_tag
- city：城市（兼容旧字段，优先改用 intention_city / live_city）
- intention_city：期望/意向工作城市
- live_city：现居/常驻城市
- work_years：工作年限（累计年数；泛岗龄 / 泛经验口径。像「团队管理」「带队」「领导力」「管理经验」「行业经验」这类能力/方向描述，优先放这里或 semantic，不要硬塞到 role_tenure）
- work_segment_count：工作经历段数（用户说「超过 N 段经历/工作经历」时用 gte，禁止映射到 work_years）
- employment_gap：尾部空窗期（月数或预设 trailing_gt_6m/trailing_le_6m/approx_12m；空窗超过 N 年/月）
- latest_company：最近一份工作的公司（用户说「最近一份在 X」时用此字段，不要用 company）
- latest_work_job：最近一段职位/岗位（用户说「最近职位是 X」时用此字段，不要用 position）
- recent_tenure_1：最近一段任职月数（用户说「最近一份工作任职 N 年以上」→ gte 月数，如 2 年 → 24）
- job_stability：稳定性强/高（eq strong）
- inter_segment_gap：段间间断空档次数（gte/lte）
- max_inter_segment_gap：段间最大空窗月数（gte/lte；生涯中间空窗、段间无大空档）
- career_discontinuity：就业履历断层（eq true）
- role_tenure：岗位累计年限（gte 月数，softText 写岗位/职位标题，如 厨师长、运营经理；只接受明确 job/title，不接受团队管理、带队、领导力、管理经验这类泛岗龄描述）
- graduation_year：毕业年份（四位数公历年；用于 应届/校招/春招/秋招/XX届/今年毕业 等表述，eq 年份；无明确年份的 应届/校招 用当前年份）
- age：年龄（周岁整数；仅用于显式年龄表述，如 25岁、25-35岁、30岁以上；禁止用于出生年）
- birth_year：出生年份（四位公历年；用于 N年出生、N年的、90后/00后 等）
- gender：性别
- work_state：求职状态（values 写简称或字典全称，如 离职、离职正在找工作、在职急寻新工作；compiler 会映射到 intention.work_state.code）
- expected_salary：期望薪资
- talent_tag：候选人级人才标签，如 名企背景、25QS200；单独出现的 985/211 人才标签仍可用，但明确修饰本科/第一学历院校层次时必须改用 school_tier
- certificate：证书/资格证（含语言类证书与专业技能证书），如 IELTS、雅思、CET-4、CPA、PMP、注册会计师、驾驶证
- language：外语/语言能力（简历「语言能力」区块），如 英语、法语、雅思、IELTS、英语四级、熟练英语

证书 vs 语言 vs skill（必须区分）：
- 雅思/IELTS/托福/CET/TEM/专四/专八等考试：**禁止**放进 skill；语言考试须同时写 certificate 与 language（见下方 OR 例子），专业技能证书（CPA/PMP/驾照等）只写 certificate
- skill 仅用于可复用职场技能（Python、销售、财务分析等），不是证书名

where 输出规则：
- and/or/not/leaf 四种节点：
  - and: {"type":"and","children":[...]}
  - or: {"type":"or","children":[...],"minimumShouldMatch":1}
  - not: {"type":"not","child":{...}}
  - leaf: {"type":"leaf","condition":{field/operator/values/...}}
- 复杂语义必须保留分组，不要降平成线性列表。
- 顶层必须有 where，且 where 必须是合法 AST 节点。
- 输出中禁止出现 ES 字段路径、nested、bool、filter。
- 用户显式指定姓名时，必须保留 name 条件；禁止因为姓名看起来不像真实人名、像昵称、网名、饮品名、品牌名或误输入而丢弃、改写、纠错，或仅写入 meta.notes。
- 用户显式指定姓氏时，必须保留 name 条件，values 只写姓氏，不要写“姓杨的候选人”等整段文本。
- 用户显式指定姓名包含、姓名结尾、或 "_" 占位符模式时，必须保留 name 条件，values 只写核心字符或模式，不要写整句描述。
- 若同时有姓名和其他条件，where 中必须同时保留 name 与其他条件。
- 对明显不符合招聘搜索语境的错别字、近音词、形近词或输入法误选词，在不改变核心意图、不新增条件的前提下做轻量纠错；纠错后的业务描述写 semantic，明确岗位写 position，疑似错词不写入 where。
- 工作经历职位与期望职能必须拆分，禁止合并为一个 position：
  - “做过/职位为/担任/曾任 + 岗位” → position
  - “期望职能/意向岗位/想做/希望从事 + 岗位” → intention_position
  - 同句同时出现（如“做过高级销售，期望职能是高级销售”）→ where 中写两条 leaf，各用对应 field
- 禁止用 position 承载期望职能；禁止用 intention_position 承载工作经历职位
- 实习经历与工作经历必须拆分；明确「实习过 / 曾实习 / 实习经历 / 实习于 / 实习单位 / 实习做」时，where 里优先写 `internship_company` / `internship_job`，不要落到 `company` / `position`
- 「找/招/查 + 岗位/职能 + 的简历/的候选人/的人选」类整句（无“期望/意向/想做”等 cue）：
  - 表示按工作经历/现任岗位找人 → 只用 position（values 写岗位名，如 Java工程师），**禁止** intention_position
  - 技术岗可把核心技能单独写 skill（如 Java），但不要因此省略 position；不要仅用 skill + intention_position 表达“找 Java 工程师”
  - 此类 query 在业务侧常带「的简历」「的候选人」后缀，解析时保留岗位语义即可，后缀本身不必写进 values
- 英文职业分类/岗位族名称（如 SOC/O*NET 风格：First-Line Supervisors of X、Managers of X、Specialists、Representatives 等）应先理解为岗位族，再转换为招聘中常见职位名；position values 可以使用中英文常见等价岗位词，不要只照抄英文原句。

hard + soft 对齐原则：
- 原子化 skill（required=true）由 compiler 编译为 **soft 语义召回**（skills_vector / work_vector / self_evaluation_vector），**禁止**依赖 skills.name.keyword 硬过滤；LLM 须为对应 skill 输出 semantic（fields 含 skills_vector），对 skill 做 **2 到 4 个**同义词扩充（宁少勿多，不确定时只保留原词 + 1～2 个高置信别名）。扩充必须覆盖中英双语：英文技能补充中文翻译，中文技能补充英文翻译。
- skill 同义词扩充只允许：**同一技能的写法变体**（中英直译、常见缩写、版本/大小写变体、行业固定别名），如 Python→Py/Python3、K8s→Kubernetes。禁止扩成相关技能、上位概念或行业泛词（如 Python 禁止扩成「编程/软件开发/后端」；K8s 禁止扩成「云计算/DevOps/微服务」）。
- required company 须同时输出 semantic（fields 含 work_vector）：text = where 中的公司名 + **2 到 4 个**高置信别名（中英互译、常见缩写、法定中文名/英文官方名），宁少勿多。该 semantic 会用于 hard 别名补充与 knn；**禁止**扩成同行业其他公司、集团泛称或「快消/互联网」等行业词。
- company 同义词示例：Mondelēz→Mondelez/亿滋；字节跳动→ByteDance/抖音集团（仅当确为同一雇主常用写法时）。
- 其他 structured 字段（职位/学历/薪资/城市/标签/年限等）只写 where，不要为它们再写 semantic 复述
- 宽词/非硬性条件（required=false 或 compiler 会降级为 soft 的公司/职位）不要放进 where 硬字段，应直接写 semantic
- semantic 主要用于无法结构化的业务描述、行业方向、项目经验等；不重复 query
- 行业/背景/业务方向词，如 X行业经验、X背景、X经验、X领域经验，不要写入 position、skill、company；应写入 semantic。只有明确岗位名、职务名、职级名才写 position。
- **例外**：若 query 命中「可用人才标签字典」中的完整标签名（如「名企背景」「985」），必须写 talent_tag，values 用完整标签名，semantic=[]；不要把「名企背景」拆成「名企」或当作公司背景写 semantic。
- **大厂 → 名企背景（方案 B，必须遵守）**：若上表含「名企背景」，query 出现「大厂/互联网大厂/头部公司/知名公司/大公司」时，必须写 where.talent_tag values=["名企背景"] required=true；可与 position/skill 等并列 and；**不受「最好/优先/加分/偏好」影响**，禁止把大厂写进 semantic 或 company。
- **具名公司 + 等/类似/等公司（方案 T3，与方案 B 互斥）**：query 含具名雇主 +「等/类似/同类/等公司」时，必须写 company operator=any（锚点 + 同类 peer，可仅写锚点由服务端图扩）；meta.expandScope="peers"，meta.peerHint 取 industry_peers（默认）或 famous_background（query 显式含名企背景/名企/头部公司）；**禁止**写 talent_tag=名企背景；可与 position 等 and。required company 的 semantic 仍只写锚点别名，**禁止**在 semantic 里列同行业其他公司。
- 对 position 进行 5 到 10 个中英文同义词的扩充。
- 对 非skill的semantic 业务描述，进行 5 到 10 个中英文同义词的扩充。

operator 规则：
- eq：单值等于。
- any：多个值任一满足，表达“或/或者/任一/都可以”。
- all：多个值都要满足。
- not：排除/不要/除外。
- gte/lte/range：数值或学历区间；证书分数门槛（如雅思 3.5 分以上）暂无法稳定结构化时，values 仍写考试名，分数要求写进 semantic 或 meta.notes，不要用 skill。

required 规则：
- “必须/硬性/要求/需要/找/查找”默认 required=true。
- “优先/最好/加分/偏好”可 required=false，并补 semantic。
- 结构化字段只表达业务意图，不要写 ES 字段路径、nested、bool、filter。
- 不要过度 structured：无法稳定对应简历结构化字段的方向、职能、业务描述、英文复合经验短语，优先 semantic。
- 技术岗位：岗位名进 position；原子技能（Java/Python）可另写 skill leaf；禁止把「Java工程师」整句只放进 intention_position 或只拆成 skill。

semantic.fields 只能取：
- work_vector, project_vector, education_vector, self_evaluation_vector, skills_vector

例子：
“会 Python 或 Java” ->
{
  "where": {
    "type": "leaf",
    "condition": {"field":"skill","operator":"any","values":["Python","Java"],"required":true,"weight":1.0}
  },
  "semantic": [{"text":"Python Py Python3 Java","fields":["skills_vector"],"weight":1.0}],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“帮我找一个候选人，姓名小青桔美式，性别男” ->
{
  "where": {"type":"and","children":[
    {"type":"leaf","condition":{"field":"name","operator":"eq","values":["小青桔美式"],"required":true,"weight":1.0}},
    {"type":"leaf","condition":{"field":"gender","operator":"eq","values":["男"],"required":true,"weight":1.0}}
  ]},
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“查找名字完全是张三的人” ->
{
  "where": {
    "type": "leaf",
    "condition": {"field":"name","operator":"eq","values":["张三"],"required":true,"weight":1.0}
  },
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“帮我找姓杨的候选人” ->
{
  "where": {
    "type": "leaf",
    "condition": {"field":"name","operator":"eq","values":["杨"],"required":true,"weight":1.0}
  },
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“查找名字中包含伟字的人” ->
{
  "where": {
    "type": "leaf",
    "condition": {"field":"name","operator":"eq","values":["伟"],"required":true,"weight":1.0}
  },
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“查找名字是以明字结尾的人” ->
{
  "where": {
    "type": "leaf",
    "condition": {"field":"name","operator":"eq","values":["明"],"required":true,"weight":1.0}
  },
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“不要 985 院校” ->
{
  "where": {
    "type":"not",
    "child":{"type":"leaf","condition":{"field":"talent_tag","operator":"any","values":["985"],"required":true,"weight":1.0}}
  },
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“期望工作城市是上海或者北京，本科学历” ->
{
  "where": {"type":"and","children":[
    {"type":"leaf","condition":{"field":"intention_city","operator":"any","values":["上海","北京"],"required":true,"weight":1.0}},
    {"type":"leaf","condition":{"field":"degree","operator":"eq","values":["本科"],"required":true,"weight":1.0}}
  ]},
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“做过高级销售，性别女，工作经验10年以上，期望职能是高级销售” ->
{
  "where": {"type":"and","children":[
    {"type":"leaf","condition":{"field":"position","operator":"eq","values":["高级销售"],"required":true,"weight":1.0}},
    {"type":"leaf","condition":{"field":"intention_position","operator":"eq","values":["高级销售"],"required":true,"weight":1.0}},
    {"type":"leaf","condition":{"field":"gender","operator":"eq","values":["女"],"required":true,"weight":1.0}},
    {"type":"leaf","condition":{"field":"work_years","operator":"gte","values":[10],"required":true,"weight":1.0}}
  ]},
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“3年以上团队管理经验的候选人” ->
{
  "where": {
    "type": "leaf",
    "condition": {"field":"work_years","operator":"gte","values":[3],"required":true,"weight":1.0}
  },
  "semantic": [{"text":"团队管理 带队 领导力","fields":["work_vector"],"weight":1.0}],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": [], "tenureIntent": "generic"}
}
“3年以上厨师长经验的候选人” ->
{
  "where": {
    "type": "leaf",
    "condition": {"field":"role_tenure","operator":"gte","values":[36],"required":true,"weight":1.0,"softText":"厨师长"}
  },
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": [], "tenureIntent": "title"}
}
“有 IELTS / 雅思成绩” ->
{
  "where": {
    "type": "or",
    "children": [
      {"type":"leaf","condition":{"field":"certificate","operator":"any","values":["IELTS","雅思"],"required":true,"weight":1.0}},
      {"type":"leaf","condition":{"field":"language","operator":"any","values":["IELTS","雅思"],"required":true,"weight":1.0}}
    ],
    "minimumShouldMatch": 1
  },
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“有 CPA 证书” ->
{
  "where": {"type":"leaf","condition":{"field":"certificate","operator":"any","values":["CPA","注册会计师"],"required":true,"weight":1.0}},
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“找Java工程师的简历” ->
{
  "where": {"type":"and","children":[
    {"type":"leaf","condition":{"field":"position","operator":"eq","values":["Java工程师"],"required":true,"weight":1.0}},
    {"type":"leaf","condition":{"field":"skill","operator":"eq","values":["Java"],"required":true,"weight":1.0}}
  ]},
  "semantic": [{"text":"Java工程师岗位与项目经验","fields":["work_vector","skills_vector"],"weight":1.0}],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“招做过采购经理的候选人” ->
{
  "where": {"type":"leaf","condition":{"field":"position","operator":"eq","values":["采购经理"],"required":true,"weight":1.0}},
  "semantic": [{"text":"采购经理工作经历","fields":["work_vector"],"weight":1.0}],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“查找期望职能是品牌顾问的候选人” ->
{
  "where": {"type":"leaf","condition":{"field":"intention_position","operator":"eq","values":["品牌顾问"],"required":true,"weight":1.0}},
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“在 Mondelēz 工作过的候选人” ->
{
  "where": {"type":"leaf","condition":{"field":"company","operator":"eq","values":["Mondelēz"],"required":true,"weight":1.0}},
  "semantic": [{"text":"Mondelēz Mondelez 亿滋","fields":["work_vector"],"weight":1.0}],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“在玛氏实习过的候选人” ->
{
  "where": {"type":"leaf","condition":{"field":"internship_company","operator":"eq","values":["玛氏"],"required":true,"weight":1.0}},
  "semantic": [{"text":"玛氏 Mars","fields":["work_vector"],"weight":1.0}],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“名企背景” ->
{
  "where": {"type":"leaf","condition":{"field":"talent_tag","operator":"any","values":["名企背景"],"required":true,"weight":1.0}},
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“名企背景标签” ->
{
  "where": {"type":"leaf","condition":{"field":"talent_tag","operator":"any","values":["名企背景"],"required":true,"weight":1.0}},
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“最好在大厂干过的销售” ->
{
  "where": {"type":"and","children":[
    {"type":"leaf","condition":{"field":"position","operator":"eq","values":["销售"],"required":true,"weight":1.0}},
    {"type":"leaf","condition":{"field":"talent_tag","operator":"any","values":["名企背景"],"required":true,"weight":1.0}}
  ]},
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“找有大厂背景的简历” ->
{
  "where": {"type":"leaf","condition":{"field":"talent_tag","operator":"any","values":["名企背景"],"required":true,"weight":1.0}},
  "semantic": [],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": []}
}
“在玛氏等头部快消工作过的销售” ->
{
  "where": {"type":"and","children":[
    {"type":"leaf","condition":{"field":"company","operator":"any","values":["玛氏"],"required":true,"weight":1.0}},
    {"type":"leaf","condition":{"field":"position","operator":"eq","values":["销售"],"required":true,"weight":1.0}}
  ]},
  "semantic": [{"text":"玛氏 Mars","fields":["work_vector"],"weight":1.0}],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": [], "expandScope": "peers", "peerHint": "industry_peers"}
}
“找玛氏等公司做过的销售” ->
{
  "where": {"type":"and","children":[
    {"type":"leaf","condition":{"field":"company","operator":"any","values":["玛氏"],"required":true,"weight":1.0}},
    {"type":"leaf","condition":{"field":"position","operator":"eq","values":["销售"],"required":true,"weight":1.0}}
  ]},
  "semantic": [{"text":"玛氏 Mars","fields":["work_vector"],"weight":1.0}],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": [], "expandScope": "peers", "peerHint": "industry_peers"}
}
“找玛氏等名企背景的简历” ->
{
  "where": {"type":"leaf","condition":{"field":"company","operator":"any","values":["玛氏"],"required":true,"weight":1.0}},
  "semantic": [{"text":"玛氏 Mars","fields":["work_vector"],"weight":1.0}],
  "meta": {"needClarification": false, "clarifyQuestions": [], "notes": [], "expandScope": "peers", "peerHint": "famous_background"}
}
"""


def _build_tag_prompt_fragment(
    tag_name_to_id: dict[str, int],
    *,
    max_tags: int = 120,
) -> str:
    """Format a tenant tag dictionary for injection into the parser prompt."""
    if not tag_name_to_id:
        return ""
    items = sorted(tag_name_to_id.items(), key=lambda pair: pair[0])
    if len(items) > max_tags:
        items = items[:max_tags]
    mapping = json.dumps(dict(items), ensure_ascii=False)
    return f"""

====================
可用人才标签字典（租户 HR 可见，name → tagId）
====================
{mapping}

规则（必须遵守）：
- 例外：985/211 明确修饰「本科/第一学历/统招本科/全日制本科」时，必须写 degree + school_tier，禁止写 talent_tag。
- 用户 query 命中上表标签名（如「名企背景」「985」）时，必须写 where.talent_tag，values 用**完整标签名**，禁止截断（「名企背景」不可写成「名企」）。
- 若上表含「名企背景」，query 出现「大厂/互联网大厂/头部公司/知名公司/大公司」→ talent_tag values=["名企背景"] required=true，可与 position 等 and；不受「最好/优先」影响；semantic=[]。
- 标签筛选时 semantic 应为空数组 []，禁止用 work_vector 扩写「大厂/500强/名企」等同义词。
- 「X背景」若 X 是上表中的标签名（如「名企背景」），走 talent_tag，不要当作行业背景写 semantic。
- 多标签「985 或 211」→ operator=any；「985 且 211」→ operator=all 或 and 两个 leaf。
- 若上表含「空白经历」，用户说「空挡/空档/履历空白/工作经历无空白期/无空窗」等：
  - 要找有空白经历 → talent_tag values=["空白经历"]；semantic=[]。
  - 要排除空白经历（如无空白期/无空窗/不要空白经历）→ where 写 not + talent_tag 子树，禁止仅用 work_vector semantic 代替。
- 若上表含「频繁跳槽」，用户说「跳槽频繁/换工作频繁」等 → talent_tag values=["频繁跳槽"] required=true；semantic=[]。
"""


def build_hybrid_v2_system_prompt(
    *,
    as_of: datetime | None = None,
    tag_name_to_id: dict[str, int] | None = None,
) -> str:
    """构建带「系统时间」上下文的 system prompt；时间为 NL 解析的唯一可靠基准。"""
    ref = as_of or datetime.now()
    ref_local = ref.strftime("%Y-%m-%d %H:%M:%S")
    today = ref.date().isoformat()
    year = ref.year
    example_birth_year = year - 23
    return (
        HYBRID_V2_SYSTEM_PROMPT_BODY
        + f"""

====================
系统时间（唯一解析基准，必须遵守）
====================
以下为服务端注入的**唯一可靠**「现在」；所有与时间相关的理解、换算、区间推断**只能**以此为准，**禁止**使用模型训练截止年、2024/2025 等臆测年份，也禁止自行假设「今天」。

- 系统时间（本地）：{ref_local}
- 日历日期：{today}
- 当前公历年：{year}

适用场景（不限于 age / birth_year）：
- 相对时间词：今年、去年、前年、最近/近 N 年（月）、过去 N 年、应届、刚毕业、届、校招季等 → 先换算为相对系统时间的区间或数值，再写入 where（gte/lte/range/eq）或 semantic；无法稳定结构化时写 semantic，并在 meta.notes 注明依据。
- 出生年（必须用 birth_year，禁止换算为 age）：「N 年出生」「N 年的候选人」→ {{"field":"birth_year","operator":"eq","values":[N]}}。
  例：在 {today} 查「{example_birth_year}年出生」→ {{"field":"birth_year","operator":"eq","values":[{example_birth_year}]}}。
- 年代（birth_year range）：「90 后」→ range [1990,1999]；「00 后」→ range [2000,2009]；「95 后」→ gte 1995。
- 显式年龄（必须用 age，禁止用 birth_year）：「25 岁」「25-35 岁」「30 岁以上」→ age 的 eq/range/gte/lte。
- 求职状态（work_state）：「离职/离职状态/正在找工作」→ work_state eq「离职」；「在职看机会」→ eq「在职」或「看看新机会」；勿把求职状态写成 gender/semantic。
- 工作年限：用户说「N 年以上/以下经验」→ work_years 的 gte/lte/range；说「近 N 年工作经历/行业经验」等日历相对表述 → 优先 semantic（work_vector 等），notes 写明相对 {today} 的理解。
- 工作年限 / 岗龄意图要分开：
  - 「团队管理 / 带队 / 领导力 / 管理经验 / 行业经验」→ generic tenure，优先 work_years 或 semantic，**不要** role_tenure
  - 「3年以上厨师长 / 运营经理 / 销售经理经验」→ title tenure，才用 role_tenure
  - LLM 需要在 `meta.tenureIntent` 里标记 `generic` / `title` / `recent`；不确定时宁可保守到 `generic`
- 工作段数：用户说「工作经历超过 N 段/至少 N 段」→ work_segment_count gte；禁止映射 work_years。
- 空窗期：用户说「空窗超过 N 年/月、约一年空窗、短期/长期空窗」→ employment_gap；说「近 N 年内有工作经历」→ employment_gap lte 12；说「多段间断空档」→ inter_segment_gap；说「履历断层/就业履历有断层」→ career_discontinuity；说「段间无大空档/履历连续」→ max_inter_segment_gap lte 6 + inter_segment_gap lte 0；说「生涯中间有过 N 年以上空窗」→ max_inter_segment_gap gte 12。
- 最近公司：用户说「最近一份在 X 公司」→ latest_company，不要用 company。
- 最近职位：用户说「最近职位/岗位是 X」→ latest_work_job，不要用 position。
- 最近任职：用户说「最近一份工作任职 N 年以上」→ recent_tenure_1 gte（N*12 月）。
- 稳定性：用户说「稳定性高/强」→ job_stability eq strong。
- 岗龄：用户说「N 年以上 X 经验/岗位经历」→ 仅当 X 是明确岗位/职位标题时才写 role_tenure gte（values 写月数或年数*12，softText 写岗位/职位标题）；泛岗龄则回到 work_years。
- 毕业/就读时间：若仅能用自然语言描述（如「{year - 2} 届」「近两年毕业」），放 semantic（education_vector）或 notes；不要编造 ES 日期字段路径。

meta.notes：凡做了时间换算或存在歧义，须简要写明「按系统时间 {ref_local}」。
"""
        + _build_tag_prompt_fragment(tag_name_to_id or {})
    )


def _default_fallback_where(query: str) -> dict[str, Any]:
    text = (query or "").strip()
    if not text:
        text = "候选人"
    return {
        "type": "leaf",
        "condition": {
            "field": "skill",
            "operator": "any",
            "values": [text],
            "required": False,
            "weight": 1.0,
        },
    }


def _normalize_where_node(node: Any, *, query: str) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    ntype = str(node.get("type") or "").strip()
    if ntype == "leaf":
        cond = node.get("condition")
        if not isinstance(cond, dict):
            return None
        values = cond.get("values")
        if not isinstance(values, list):
            values = [v for v in [values] if v is not None]
        cleaned_values = [v for v in values if str(v).strip()]
        if not cleaned_values:
            return None
        return {
            "type": "leaf",
            "condition": {
                "field": cond.get("field"),
                "operator": cond.get("operator", "eq"),
                "values": cleaned_values,
                "required": bool(cond.get("required", True)),
                "weight": float(cond.get("weight", 1.0) or 1.0),
                **({"softText": cond.get("softText")} if cond.get("softText") else {}),
            },
        }
    if ntype in {"and", "or"}:
        children = node.get("children")
        if not isinstance(children, list):
            return None
        normalized_children = [
            c
            for c in (_normalize_where_node(item, query=query) for item in children)
            if c is not None
        ]
        if not normalized_children:
            return None
        out = {"type": ntype, "children": normalized_children}
        if ntype == "or":
            msm = node.get("minimumShouldMatch")
            try:
                out["minimumShouldMatch"] = max(1, int(msm)) if msm is not None else 1
            except (TypeError, ValueError):
                out["minimumShouldMatch"] = 1
        return out
    if ntype == "not":
        child = _normalize_where_node(node.get("child"), query=query)
        if child is None:
            return None
        return {"type": "not", "child": child}
    return None


def _has_field_leaf(node: dict[str, Any] | None, field: str) -> bool:
    if not isinstance(node, dict):
        return False
    ntype = node.get("type")
    if ntype == "leaf":
        cond = node.get("condition")
        return isinstance(cond, dict) and str(cond.get("field") or "") == field
    if ntype in {"and", "or"}:
        children = node.get("children")
        if isinstance(children, list):
            return any(
                _has_field_leaf(child, field) for child in children if isinstance(child, dict)
            )
        return False
    if ntype == "not":
        child = node.get("child")
        return _has_field_leaf(child, field) if isinstance(child, dict) else False
    return False


def _infer_degree_condition_from_query(query: str) -> dict[str, Any] | None:
    q = str(query or "").strip()
    if not q:
        return None
    if any(hint in q for hint in _DEGREE_SKIP_HINTS):
        return None
    degree_hits: list[str] = []
    for pattern, degree_value in _DEGREE_QUERY_PATTERNS:
        if pattern.search(q):
            degree_hits.append(degree_value)
    if not degree_hits:
        return None
    unique_values: list[str] = []
    for value in degree_hits:
        if value not in unique_values:
            unique_values.append(value)
    operator = "eq" if len(unique_values) == 1 else "any"
    single = unique_values[0]
    if len(unique_values) == 1:
        if re.search(rf"{re.escape(single)}\s*(及?以上|以上|至少)", q):
            operator = "gte"
        elif re.search(rf"{re.escape(single)}\s*(及?以下|以下|不高于|不超过)", q):
            operator = "lte"
    return {
        "field": "degree",
        "operator": operator,
        "values": unique_values,
        "required": True,
        "weight": 1.0,
    }


def _ensure_degree_condition(
    where: dict[str, Any], *, query: str, notes: list[str]
) -> dict[str, Any]:
    if _has_field_leaf(where, "degree"):
        return where
    inferred = _infer_degree_condition_from_query(query)
    if not inferred:
        return where
    degree_leaf = {"type": "leaf", "condition": inferred}
    if where.get("type") == "and" and isinstance(where.get("children"), list):
        where = dict(where)
        where["children"] = list(where["children"]) + [degree_leaf]
    else:
        where = {"type": "and", "children": [where, degree_leaf]}
    notes.append("auto_add_degree_from_query")
    return where


def _education_tier_values_from_query(query: str) -> list[str]:
    text = str(query or "").strip()
    if not text:
        return []
    matches = [
        *list(_EDUCATION_TIER_AFTER_STAGE_RE.finditer(text)),
        *list(_EDUCATION_TIER_BEFORE_STAGE_RE.finditer(text)),
    ]
    values: list[str] = []
    for match in matches:
        prefix = text[max(0, match.start() - 8) : match.start()]
        matched_text = match.group(0)
        if any(token in prefix or token in matched_text for token in _EDUCATION_TIER_NEGATION):
            continue
        for tier in re.findall(r"985|211", matched_text):
            if tier not in values:
                values.append(tier)
    return values


def _drop_positive_talent_tag_tiers(
    node: dict[str, Any] | None,
    tiers: list[str],
    *,
    negated: bool = False,
) -> tuple[dict[str, Any] | None, bool]:
    if not isinstance(node, dict):
        return node, False
    ntype = node.get("type")
    if ntype == "leaf":
        cond = node.get("condition")
        if negated or not isinstance(cond, dict) or cond.get("field") != "talent_tag":
            return node, False
        values = cond.get("values")
        if not isinstance(values, list):
            return node, False
        remaining = [value for value in values if str(value).strip() not in tiers]
        if len(remaining) == len(values):
            return node, False
        if not remaining:
            return None, True
        updated = dict(node)
        updated["condition"] = {**cond, "values": remaining}
        return updated, True
    if ntype == "and":
        children = node.get("children")
        if not isinstance(children, list):
            return node, False
        changed = False
        updated_children: list[dict[str, Any]] = []
        for child in children:
            updated, child_changed = _drop_positive_talent_tag_tiers(child, tiers, negated=negated)
            changed = changed or child_changed
            if updated is not None:
                updated_children.append(updated)
        if not changed:
            return node, False
        if not updated_children:
            return None, True
        updated_node = dict(node)
        updated_node["children"] = updated_children
        return updated_node, True
    return node, False


def _append_and_leaves(
    where: dict[str, Any] | None,
    leaves: list[dict[str, Any]],
) -> dict[str, Any]:
    if not leaves:
        if where is None:
            return _default_fallback_where("")
        return where
    if where is None:
        children: list[dict[str, Any]] = []
    elif where.get("type") == "and" and isinstance(where.get("children"), list):
        children = list(where["children"])
    else:
        children = [where]
    children.extend(leaves)
    if len(children) == 1:
        return children[0]
    return {"type": "and", "children": children}


def _ensure_school_tier_condition(
    where: dict[str, Any] | None,
    *,
    query: str,
    notes: list[str],
) -> dict[str, Any] | None:
    tiers = _education_tier_values_from_query(query)
    if not tiers:
        return where

    updated_where, removed_talent_tag = _drop_positive_talent_tag_tiers(where, tiers)
    if removed_talent_tag:
        notes.append("rewrite_talent_tag_to_school_tier")

    leaves: list[dict[str, Any]] = []
    if not _has_field_leaf(updated_where, "degree") and re.search(_UNDERGRAD_STAGE_PATTERN, query):
        leaves.append(
            {
                "type": "leaf",
                "condition": {
                    "field": "degree",
                    "operator": "eq",
                    "values": ["本科"],
                    "required": True,
                    "weight": 1.0,
                },
            }
        )
        if "auto_add_degree_from_query" not in notes:
            notes.append("auto_add_degree_from_query")
    if not _has_field_leaf(updated_where, "school_tier"):
        leaves.append(
            {
                "type": "leaf",
                "condition": {
                    "field": "school_tier",
                    "operator": "eq" if len(tiers) == 1 else "any",
                    "values": tiers,
                    "required": True,
                    "weight": 1.0,
                },
            }
        )
        notes.append("auto_add_school_tier_from_query")
    return _append_and_leaves(updated_where, leaves)


def _repair_ir_payload(payload: Any, *, query: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    where = _normalize_where_node(payload.get("where"), query=query)
    if where is None:
        where = _default_fallback_where(query)

    semantic_raw = payload.get("semantic")
    semantic: list[dict[str, Any]] = []
    if isinstance(semantic_raw, list):
        for item in semantic_raw:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            fields = item.get("fields")
            if not text or not isinstance(fields, list) or not fields:
                continue
            safe_fields = [f for f in fields if isinstance(f, str) and f in SOFT_VECTOR_FIELDS]
            if not safe_fields:
                continue
            try:
                weight = float(item.get("weight", 1.0) or 1.0)
            except (TypeError, ValueError):
                weight = 1.0
            semantic.append(
                {"text": text, "fields": safe_fields, "weight": max(0.1, min(5.0, weight))}
            )

    meta_raw = payload.get("meta")
    meta = {
        "needClarification": False,
        "clarifyQuestions": [],
        "notes": [],
    }
    if isinstance(meta_raw, dict):
        meta["needClarification"] = bool(meta_raw.get("needClarification", False))
        cq = meta_raw.get("clarifyQuestions")
        if isinstance(cq, list):
            meta["clarifyQuestions"] = [str(x) for x in cq if str(x).strip()]
        notes = meta_raw.get("notes")
        if isinstance(notes, list):
            meta["notes"] = [str(x) for x in notes if str(x).strip()]
        tenure_intent = str(meta_raw.get("tenureIntent") or "").strip()
        if tenure_intent in {"generic", "title", "recent"}:
            meta["tenureIntent"] = tenure_intent
        expand_scope = str(meta_raw.get("expandScope") or "").strip()
        if expand_scope in {"peers"}:
            meta["expandScope"] = expand_scope
        peer_hint = str(meta_raw.get("peerHint") or "").strip()
        if peer_hint in {"industry_peers", "famous_background"}:
            meta["peerHint"] = peer_hint
    if "tenureIntent" not in meta:
        meta["tenureIntent"] = classify_work_tenure_intent(query)
    where = _ensure_degree_condition(where, query=query, notes=meta["notes"])
    school_tier_where = _ensure_school_tier_condition(
        where,
        query=query,
        notes=meta["notes"],
    )
    if school_tier_where is not None:
        where = school_tier_where

    repaired = {"where": where, "semantic": semantic, "meta": meta}
    if isinstance(payload.get("sort"), list):
        repaired["sort"] = payload.get("sort")
    return repaired


def _build_parser_inputs(
    query: str,
    attempt: int,
    last_error: str | None,
    *,
    as_of: datetime | None = None,
    tag_name_to_id: dict[str, int] | None = None,
) -> tuple[str, str]:
    system = build_hybrid_v2_system_prompt(
        as_of=as_of,
        tag_name_to_id=tag_name_to_id,
    )
    user = query
    if attempt > 1:
        reason = (last_error or "schema validation failed").strip()
        user = (
            f"{query}\n\n"
            "上一次输出未通过 schema 校验，请严格输出合法 IR JSON。"
            f"错误摘要：{reason}。"
            "重点：where 里 and/or 节点 children 不可为空。"
        )
    return system, user


@dataclass(frozen=True)
class IrParseResult:
    """Result of one V2 NL-to-IR parse, including observability facts."""

    intent: CandidateSearchIR | None
    attempts: int
    error: str | None = None
    source: str = "hybrid_v2_parser"

    @property
    def ok(self) -> bool:
        return self.intent is not None


def parse_query_to_ir(
    query: str,
    *,
    llm_client: LlmClient,
    as_of: datetime | None = None,
    tag_name_to_id: dict[str, int] | None = None,
    max_attempts: int = _MAX_PARSE_ATTEMPTS,
) -> IrParseResult:
    """Parse natural language into Hybrid V2 IR through the injected LLM.

    The caller owns provider selection and metering. This function only owns
    the V2 prompt, schema, repairs and the bounded semantic retry.
    """
    query_text = str(query or "").strip()
    if not query_text:
        return IrParseResult(intent=None, attempts=0, error="empty_query")

    last_error: str | None = None
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        try:
            system, user = _build_parser_inputs(
                query_text,
                attempt,
                last_error,
                as_of=as_of,
                tag_name_to_id=tag_name_to_id,
            )
            payload = llm_client.complete_json(
                system=system,
                user=user,
                schema_name="hybrid_search_v2_ir",
                schema=HYBRID_V2_IR_JSON_SCHEMA,
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("where"), dict):
                last_error = "missing_where"
                raise ValueError("LLM IR payload is missing where")
            repaired = _repair_ir_payload(payload, query=query_text)
            if repaired is None:
                last_error = "payload_not_dict"
                raise ValueError("LLM IR payload is not an object")
            return IrParseResult(
                intent=CandidateSearchIR(**repaired),
                attempts=attempt,
            )
        except SearchCancelled:
            raise
        except Exception as exc:
            last_error = str(exc)
            if attempt < attempts:
                logger.warning(
                    "LLM hybrid v2 parse failed, retrying attempt=%s err=%s", attempt, exc
                )
                continue
            logger.exception("LLM hybrid v2 parse exception: %s", exc)
    return IrParseResult(intent=None, attempts=attempts, error=last_error)


__all__ = [
    "HYBRID_V2_IR_JSON_SCHEMA",
    "HYBRID_V2_SYSTEM_PROMPT_BODY",
    "IrParseResult",
    "build_hybrid_v2_system_prompt",
    "parse_query_to_ir",
]
