"""Deterministic recruiter-facing diffs between conversation turns."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from search_agent.models.artifacts import JobProfile

_CONDITION_LABELS = {
    "name": "姓名",
    "skill": "技能",
    "company": "公司",
    "position": "岗位",
    "city": "城市",
    "intention_city": "意向城市",
    "live_city": "现居城市",
    "work_years": "工作年限",
    "degree": "学历",
    "school": "学校",
    "major": "专业",
    "expected_salary": "期望薪资",
    "age": "年龄",
    "language": "语言",
    "certificate": "证书",
}


def build_job_profile_summary(job: JobProfile | None) -> dict[str, Any]:
    """Return the stable, compact profile snapshot persisted with each turn."""
    if job is None:
        return {}
    return {
        "title": (job.title or "").strip(),
        "must_abilities": _unique_text(ability.name for ability in job.must_abilities or []),
        "nice_abilities": _unique_text(ability.name for ability in job.nice_abilities or []),
        "hard_constraints": [condition.model_dump() for condition in (job.hard_constraints or [])],
        "difficulty": job.subjective_difficulty,
    }


def build_requirement_changes(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare two persisted profiles without inferring anything beyond their data."""
    changes: dict[str, Any] = {
        "hasChanges": False,
        "retained": [],
        "raised": [],
        "lowered": [],
        "adjusted": [],
        "warnings": [],
    }
    if (
        not isinstance(previous, dict)
        or not previous
        or not isinstance(current, dict)
        or not current
    ):
        return changes

    _compare_conditions(previous, current, changes)
    _compare_abilities(previous, current, changes)
    previous_title = str(previous.get("title") or "").strip()
    current_title = str(current.get("title") or "").strip()
    if previous_title and current_title and previous_title != current_title:
        _append(
            changes["adjusted"],
            {"label": "岗位方向", "from": previous_title, "to": current_title},
        )

    changes["retained"] = _dedupe(changes["retained"])
    for key in ("raised", "lowered", "adjusted", "warnings"):
        changes[key] = _dedupe(changes[key])
    changes["hasChanges"] = any(
        changes[key] for key in ("raised", "lowered", "adjusted", "warnings")
    )
    return changes


def _compare_conditions(
    previous: dict[str, Any],
    current: dict[str, Any],
    changes: dict[str, Any],
) -> None:
    previous_items = _normalise_conditions(previous.get("hard_constraints"))
    current_items = _normalise_conditions(current.get("hard_constraints"))
    previous_remaining = list(previous_items)
    current_remaining = list(current_items)

    previous_by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    current_by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for item in previous_remaining:
        previous_by_key[_condition_key(item)].append(item)
    for item in current_remaining:
        current_by_key[_condition_key(item)].append(item)

    for key in previous_by_key:
        if key not in current_by_key:
            continue
        previous_group = previous_by_key[key]
        current_group = current_by_key[key]
        for previous_item, current_item in zip(previous_group, current_group, strict=False):
            _remove_once(previous_remaining, previous_item)
            _remove_once(current_remaining, current_item)
            if previous_item["required"] and current_item["required"]:
                _append(
                    changes["retained"],
                    {"label": "硬条件", "text": _condition_text(current_item)},
                )
            elif previous_item["required"] and not current_item["required"]:
                _append(
                    changes["warnings"],
                    {
                        "label": "原硬条件已降为偏好",
                        "text": _condition_text(previous_item),
                    },
                )
            elif not previous_item["required"] and current_item["required"]:
                _append(
                    changes["raised"],
                    {
                        "label": "偏好升为硬条件",
                        "text": _condition_text(current_item),
                    },
                )

    previous_by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
    current_by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in previous_remaining:
        previous_by_field[item["field"]].append(item)
    for item in current_remaining:
        current_by_field[item["field"]].append(item)

    for field in previous_by_field:
        if field not in current_by_field:
            continue
        for previous_item, current_item in zip(
            previous_by_field[field], current_by_field[field], strict=False
        ):
            _remove_once(previous_remaining, previous_item)
            _remove_once(current_remaining, current_item)
            _append(
                changes["adjusted"],
                {
                    "label": (
                        "硬条件调整"
                        if previous_item["required"] or current_item["required"]
                        else "偏好调整"
                    ),
                    "text": _condition_label(field),
                    "from": _condition_change_value(previous_item),
                    "to": _condition_change_value(current_item),
                },
            )
            if previous_item["required"] and not current_item["required"]:
                _append(
                    changes["warnings"],
                    {
                        "label": "原硬条件已降为偏好",
                        "text": _condition_label(field),
                    },
                )

    for item in previous_remaining:
        if item["required"]:
            _append(
                changes["warnings"],
                {"label": "原硬条件未保留", "text": _condition_text(item)},
            )
        else:
            _append(
                changes["lowered"],
                {"label": "移除偏好条件", "text": _condition_text(item)},
            )
    for item in current_remaining:
        _append(
            changes["raised"],
            {
                "label": "新增硬条件" if item["required"] else "新增偏好条件",
                "text": _condition_text(item),
            },
        )


def _compare_abilities(
    previous: dict[str, Any],
    current: dict[str, Any],
    changes: dict[str, Any],
) -> None:
    previous_must = _text_map(previous.get("must_abilities"))
    current_must = _text_map(current.get("must_abilities"))
    previous_nice = _text_map(previous.get("nice_abilities"))
    current_nice = _text_map(current.get("nice_abilities"))

    for key in previous_must:
        if key in current_must:
            continue
        if key in current_nice:
            _append(
                changes["lowered"],
                {"label": "能力降级", "text": f"{previous_must[key]}（必备 → 加分）"},
            )
        else:
            _append(
                changes["lowered"],
                {"label": "移除必备能力", "text": previous_must[key]},
            )
    for key in current_must:
        if key in previous_must:
            continue
        if key in previous_nice:
            _append(
                changes["raised"],
                {"label": "能力升级", "text": f"{current_must[key]}（加分 → 必备）"},
            )
        else:
            _append(
                changes["raised"],
                {"label": "新增必备能力", "text": current_must[key]},
            )

    if "nice_abilities" not in previous:
        return
    for key in previous_nice:
        if key not in current_nice and key not in current_must:
            _append(
                changes["lowered"],
                {"label": "降低优先级", "text": previous_nice[key]},
            )
    for key in current_nice:
        if key not in previous_nice and key not in previous_must:
            _append(
                changes["raised"],
                {"label": "提高优先级", "text": current_nice[key]},
            )


def _normalise_conditions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalised: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        if not field:
            continue
        values = item.get("values")
        if not isinstance(values, list):
            values = [] if values is None else [values]
        normalised.append(
            {
                "field": field,
                "operator": str(item.get("operator") or "eq"),
                "values": list(values),
                "required": bool(item.get("required", True)),
            }
        )
    return normalised


def _condition_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["field"],
        item["operator"],
        tuple(str(value) for value in item["values"]),
    )


def _condition_label(field: str) -> str:
    return _CONDITION_LABELS.get(field, field or "条件")


def _condition_values(item: dict[str, Any]) -> str:
    return " / ".join(_format_value(value) for value in item["values"])


def _condition_change_value(item: dict[str, Any]) -> str:
    values = _condition_values(item)
    if item["operator"] == "not":
        return f"排除 {values}"
    if item["field"] == "work_years":
        if item["operator"] == "gte":
            return f"{values} 年以上"
        if item["operator"] == "lte":
            return f"{values} 年以内"
        if item["operator"] == "range":
            return f"{values} 年"
    return values


def _condition_text(item: dict[str, Any]) -> str:
    label = _condition_label(item["field"])
    values = _condition_values(item)
    if item["operator"] == "not":
        return f"排除 {values}"
    if item["field"] == "work_years":
        if item["operator"] == "gte":
            return f"{values} 年以上经验"
        if item["operator"] == "lte":
            return f"{values} 年以内经验"
        if item["operator"] == "range":
            return f"{values} 年经验"
    return f"{label}：{values}"


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _text_map(values: Iterable[Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or []:
        text = str(value or "").strip()
        if text:
            result.setdefault(text.casefold(), text)
    return result


def _unique_text(values: Iterable[Any]) -> list[str]:
    return list(_text_map(values).values())


def _remove_once(items: list[dict[str, Any]], target: dict[str, Any]) -> None:
    for index, item in enumerate(items):
        if item is target:
            items.pop(index)
            return


def _append(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
    key = tuple(sorted((name, str(value)) for name, value in item.items()))
    if all(
        tuple(sorted((name, str(value)) for name, value in existing.items())) != key
        for existing in items
    ):
        items.append(item)


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        _append(result, item)
    return result


__all__ = ["build_job_profile_summary", "build_requirement_changes"]
