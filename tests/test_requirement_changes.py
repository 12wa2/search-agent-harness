"""Tests for deterministic requirement-change explanations."""

from search_agent.models.artifacts import JobAbility, JobProfile
from search_agent.models.ir import StructuredConditionIR
from search_agent.requirement_changes import (
    build_job_profile_summary,
    build_requirement_changes,
)


def _condition(field, operator, values, *, required=True):
    return {
        "field": field,
        "operator": operator,
        "values": values,
        "required": required,
        "weight": 1.0,
    }


def test_profile_summary_includes_priority_abilities():
    job = JobProfile(
        raw_query="backend engineer",
        title="后端工程师",
        must_abilities=[JobAbility(name="Python", level="must")],
        nice_abilities=[JobAbility(name="支付系统", level="nice")],
        hard_constraints=[StructuredConditionIR(field="work_years", operator="gte", values=[5])],
        subjective_difficulty="high",
    )
    summary = build_job_profile_summary(job)

    assert summary["must_abilities"] == ["Python"]
    assert summary["nice_abilities"] == ["支付系统"]
    assert summary["hard_constraints"][0]["field"] == "work_years"


def test_requirement_changes_show_retention_priority_and_adjustment():
    previous = {
        "title": "后端工程师",
        "must_abilities": ["高并发", "分布式系统"],
        "nice_abilities": ["支付系统"],
        "hard_constraints": [
            _condition("work_years", "gte", [5]),
            _condition("city", "any", ["上海"]),
        ],
    }
    current = {
        "title": "后端工程师",
        "must_abilities": ["高并发", "分布式系统", "Go"],
        "nice_abilities": ["支付系统", "交易系统"],
        "hard_constraints": [
            _condition("work_years", "gte", [5]),
            _condition("city", "any", ["上海", "杭州"]),
            _condition("skill", "any", ["Kubernetes"]),
        ],
    }

    changes = build_requirement_changes(previous, current)

    assert changes["hasChanges"] is True
    assert any(item["text"] == "5 年以上经验" for item in changes["retained"])
    assert any(item["label"] == "新增必备能力" for item in changes["raised"])
    assert any(item["label"] == "新增硬条件" for item in changes["raised"])
    assert any(item["text"] == "交易系统" for item in changes["raised"])
    city_change = next(item for item in changes["adjusted"] if item["text"] == "城市")
    assert city_change["label"] == "硬条件调整"
    assert city_change["from"] == "上海"
    assert city_change["to"] == "上海 / 杭州"


def test_requirement_changes_warn_when_required_condition_is_removed():
    previous = {
        "title": "后端工程师",
        "must_abilities": [],
        "nice_abilities": [],
        "hard_constraints": [_condition("work_years", "gte", [5])],
    }
    current = {
        "title": "后端工程师",
        "must_abilities": [],
        "nice_abilities": [],
        "hard_constraints": [],
    }

    changes = build_requirement_changes(previous, current)

    assert changes["hasChanges"] is True
    assert changes["warnings"] == [{"label": "原硬条件未保留", "text": "5 年以上经验"}]


def test_work_years_adjustment_keeps_operator_context():
    previous = {
        "title": "后端工程师",
        "must_abilities": [],
        "nice_abilities": [],
        "hard_constraints": [_condition("work_years", "gte", [5])],
    }
    current = {
        **previous,
        "hard_constraints": [_condition("work_years", "gte", [3])],
    }

    changes = build_requirement_changes(previous, current)

    adjustment = next(item for item in changes["adjusted"] if item["text"] == "工作年限")
    assert adjustment["from"] == "5 年以上"
    assert adjustment["to"] == "3 年以上"


def test_requirement_changes_warn_when_required_condition_is_downgraded():
    previous = {
        "title": "后端工程师",
        "must_abilities": [],
        "nice_abilities": [],
        "hard_constraints": [_condition("work_years", "gte", [5])],
    }
    current = {
        "title": "后端工程师",
        "must_abilities": [],
        "nice_abilities": [],
        "hard_constraints": [_condition("work_years", "gte", [5], required=False)],
    }

    changes = build_requirement_changes(previous, current)

    assert changes["hasChanges"] is True
    assert any(item["label"] == "原硬条件已降为偏好" for item in changes["warnings"])


def test_legacy_summary_does_not_invent_priority_additions():
    previous = {
        "title": "后端工程师",
        "must_abilities": ["Python"],
        "hard_constraints": [_condition("work_years", "gte", [5])],
    }
    current = {
        "title": "后端工程师",
        "must_abilities": ["Python"],
        "nice_abilities": ["支付系统"],
        "hard_constraints": [_condition("work_years", "gte", [5])],
    }

    changes = build_requirement_changes(previous, current)

    assert changes["hasChanges"] is False
    assert changes["raised"] == []


def test_agent_soft_signal_drift_is_not_reported_as_requirement_change():
    summary = {
        "title": "后端工程师",
        "must_abilities": ["Python"],
        "nice_abilities": ["支付系统"],
        "hard_constraints": [_condition("work_years", "gte", [5])],
    }
    current = {
        **summary,
        "soft_signals": [
            {"text": "反思阶段补充的检索信号", "fields": ["project_vector"], "weight": 1.2}
        ],
    }

    changes = build_requirement_changes(summary, current)

    assert changes["hasChanges"] is False


def test_unchanged_profile_has_no_change_panel_data():
    summary = {
        "title": "后端工程师",
        "must_abilities": ["Python"],
        "nice_abilities": ["支付系统"],
        "hard_constraints": [_condition("work_years", "gte", [5])],
    }

    changes = build_requirement_changes(summary, summary)

    assert changes["hasChanges"] is False
    assert changes["retained"] == [{"label": "硬条件", "text": "5 年以上经验"}]
