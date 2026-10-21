"""Elasticsearch index lifecycle and candidate conversion for Hybrid V2."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from search_agent.school_tiers import school_tiers_for

from .catalogs import get_work_state_catalog, resolve_salary_codes_range_overlap
from .config import HybridV2Config
from .embedding import Embedder, build_embedder
from .es_client import ElasticsearchClient
from .field_policy import DEGREE_CODE_BY_NAME
from .work_experience_dates import work_row_start_end
from .work_stats_compute import enrich_profile_work_stats


class IndexingError(RuntimeError):
    """Raised when candidate data does not satisfy the index contract."""


@dataclass(frozen=True)
class IndexReport:
    index: str
    created: bool
    documents: int
    vector_mode: str
    embedding_dims: int
    refreshed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DoctorReport:
    index: str
    ok: bool
    checks: tuple[DoctorCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
        }


def _keyword() -> dict[str, Any]:
    return {"type": "keyword", "normalizer": "lowercase", "ignore_above": 512}


def _text_with_keyword() -> dict[str, Any]:
    return {"type": "text", "analyzer": "cjk", "fields": {"keyword": _keyword()}}


def _plain_text() -> dict[str, Any]:
    return {"type": "text"}


def _dense_vector(dims: int) -> dict[str, Any]:
    return {"type": "dense_vector", "dims": int(dims), "index": True, "similarity": "cosine"}


def _nested(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "nested", "properties": properties}


def build_candidate_index_mapping(
    *,
    embedding_dims: int,
    vector_mode: str = "flat",
    tenant_field: str = "company_id",
    entity_join_field: str = "entity_join",
    soft_delete_field: str = "deleted",
    number_of_shards: int = 1,
    number_of_replicas: int = 0,
) -> dict[str, Any]:
    """Build the Elasticsearch 8 mapping expected by the query compiler."""

    if vector_mode not in {"nested", "flat"}:
        raise ValueError("vector_mode must be 'nested' or 'flat'")
    if embedding_dims < 1:
        raise ValueError("embedding_dims must be positive")

    properties: dict[str, Any] = {
        "doc_id": _keyword(),
        "candidate_profile_id": _keyword(),
        "user_id": {"type": "long"},
        "company_id": {"type": "long"},
        "entity_join": _keyword(),
        "deleted": {"type": "boolean"},
        "profile_pool_id": {"type": "long"},
        "collaborator_id": {"type": "long"},
        "candidate_authority_time": {"type": "date"},
        "semantic_summary_text": _plain_text(),
        "semantic_summary_text_en": _plain_text(),
        "tags": _keyword(),
        "talent_pool": {"properties": {"newest_app_id": {"type": "long"}}},
        "payload": {"type": "object", "enabled": False},
        "overview": {
            "properties": {
                "name": _text_with_keyword(),
                "work_years": {"type": "float"},
                "age": {"type": "integer"},
                "birth": {"type": "date"},
                "self_evaluation": _plain_text(),
                "profilePictureUrl": {"type": "keyword", "index": False},
                "profile_picture_url": {"type": "keyword", "index": False},
                "wechatAvatar": {"type": "keyword", "index": False},
                "wechat_avatar": {"type": "keyword", "index": False},
                "city": {"properties": {"name": _text_with_keyword()}},
                "gender": {
                    "properties": {
                        "code": {"type": "integer"},
                        "name": _text_with_keyword(),
                    }
                },
            }
        },
        "skills": _nested({"name": _text_with_keyword(), "level": {"type": "float"}}),
        "work_experiences": _nested(
            {
                "company": _text_with_keyword(),
                "job": _text_with_keyword(),
                "description": _plain_text(),
                "start_time": {"type": "date"},
                "end_time": {"type": "date"},
                "seq": {"type": "integer"},
                "tenure_months": {"type": "integer"},
            }
        ),
        "internship_experiences": _nested(
            {
                "company": _text_with_keyword(),
                "job": _text_with_keyword(),
                "description": _plain_text(),
                "start_time": {"type": "date"},
                "end_time": {"type": "date"},
            }
        ),
        "project_experiences": _nested(
            {
                "project_name": _text_with_keyword(),
                "description": _plain_text(),
                "start_time": {"type": "date"},
                "end_time": {"type": "date"},
            }
        ),
        "educations": _nested(
            {
                "college": {"properties": {"name": _text_with_keyword()}},
                "major": {"properties": {"name": _text_with_keyword()}},
                "degree": {
                    "properties": {
                        "code": {"type": "integer"},
                        "name": _text_with_keyword(),
                    }
                },
                "school_tier": _keyword(),
                "start_time": {"type": "date"},
                "end_time": {"type": "date"},
            }
        ),
        "max_education": {
            "properties": {
                "code": {"type": "integer"},
                "name": _text_with_keyword(),
                "end_time": {"type": "date"},
            }
        },
        "intention": {
            "properties": {
                "positions": _nested({"name": _text_with_keyword()}),
                "industries": _nested({"name": _text_with_keyword()}),
                "cities": _nested({"name": _text_with_keyword()}),
                "expected_salary": _text_with_keyword(),
                "expected_salary_code": {"type": "integer"},
                "work_type": {
                    "properties": {
                        "code": {"type": "keyword"},
                        "name": _text_with_keyword(),
                    }
                },
                "work_state": {
                    "properties": {
                        "code": {"type": "keyword"},
                        "name": _text_with_keyword(),
                    }
                },
            }
        },
        "work_stats": {
            "properties": {
                "schema_version": {"type": "integer"},
                "has_work_rows": {"type": "boolean"},
                "segment_count": {"type": "integer"},
                "latest_work_company": _text_with_keyword(),
                "latest_work_job": _text_with_keyword(),
                "latest_work_start": {"type": "date"},
                "latest_work_end": {"type": "date"},
                "latest_work_is_current": {"type": "boolean"},
                "recent_tenure_months": {"type": "integer"},
                "recent_tenure_1_months": {"type": "integer"},
                "recent_tenure_2_months": {"type": "integer"},
                "avg_tenure_months": {"type": "integer"},
                "max_tenure_months": {"type": "integer"},
                "short_job_count": {"type": "integer"},
                "inter_segment_gap_months": {"type": "integer"},
                "max_inter_segment_gap_months": {"type": "integer"},
                "inter_segment_gap_count": {"type": "integer"},
                "inter_segment_gap_count_gt_6m": {"type": "integer"},
                "inter_segment_gap_count_gt_12m": {"type": "integer"},
                "role_tenure": _nested(
                    {
                        "role": _text_with_keyword(),
                        "tenure_months": {"type": "integer"},
                    }
                ),
            }
        },
        "talent_tags": _nested({"tag_id": {"type": "long"}, "name": _text_with_keyword()}),
        "certificates": _nested({"name": _text_with_keyword()}),
        "languages": _nested({"name": _text_with_keyword()}),
        "self_evaluation_vector": _dense_vector(embedding_dims),
        "skills_vector": _dense_vector(embedding_dims),
    }

    if vector_mode == "nested":
        properties["work_segments"] = _nested(
            {
                "segment_id": _keyword(),
                "company": _text_with_keyword(),
                "job": _text_with_keyword(),
                "description": _plain_text(),
                "start_time": {"type": "date"},
                "end_time": {"type": "date"},
                "vector": _dense_vector(embedding_dims),
            }
        )
        properties["project_segments"] = _nested(
            {
                "segment_id": _keyword(),
                "project_name": _text_with_keyword(),
                "description": _plain_text(),
                "vector": _dense_vector(embedding_dims),
            }
        )
        properties["education_segments"] = _nested(
            {
                "segment_id": _keyword(),
                "school": _text_with_keyword(),
                "major": _text_with_keyword(),
                "degree": _text_with_keyword(),
                "school_tier": _keyword(),
                "vector": _dense_vector(embedding_dims),
            }
        )
    else:
        properties["work_vector"] = _dense_vector(embedding_dims)
        properties["project_vector"] = _dense_vector(embedding_dims)
        properties["education_vector"] = _dense_vector(embedding_dims)

    for field_name in (tenant_field, entity_join_field, soft_delete_field):
        if field_name and field_name not in properties:
            raise ValueError(
                f"configured field {field_name!r} is not part of the bundled mapping contract"
            )

    return {
        "settings": {
            "index": {
                "number_of_shards": max(1, int(number_of_shards)),
                "number_of_replicas": max(0, int(number_of_replicas)),
            }
        },
        "mappings": {"dynamic": "strict", "properties": properties},
    }


def load_candidate_records(path: str | Path) -> list[dict[str, Any]]:
    """Load candidates from JSONL or a JSON array/object."""

    target = Path(path).expanduser()
    if not target.is_file():
        raise FileNotFoundError(f"candidate file not found: {target}")
    text = target.read_text(encoding="utf-8")
    if target.suffix.lower() == ".json":
        raw = json.loads(text)
        if isinstance(raw, dict):
            rows = raw.get("items") or raw.get("candidates") or raw.get("data")
            raw = rows if isinstance(rows, list) else [raw]
        if not isinstance(raw, list):
            raise ValueError(f"candidate JSON must be an array or object: {target}")
        return [row for row in raw if isinstance(row, dict)]

    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {target}:{line_number}: {exc}") from exc
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _get(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _as_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _label(value: Any) -> str:
    if isinstance(value, dict):
        return _first_text(
            value.get("name"),
            value.get("nameNotAnalyzed"),
            value.get("name_not_analyzed"),
            value.get("label"),
        )
    return _text(value)


def _names(value: Any) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in rows:
        name = _label(raw)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append({"name": name})
    return out


def _string_names(value: Any) -> list[str]:
    rows = value if isinstance(value, list) else []
    out: list[str] = []
    seen: set[str] = set()
    for raw in rows:
        name = _label(raw)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _normalise_date(value: Any, *, current_time: datetime) -> str | None:
    if value is None or value == "":
        return None
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if parsed is None:
        start, end = work_row_start_end({"value": value}, current_time=current_time)
        parsed = start or end
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _strip_empty(value: Any) -> Any:
    if isinstance(value, dict):
        out = {key: _strip_empty(raw) for key, raw in value.items()}
        return {key: raw for key, raw in out.items() if raw not in (None, "", [], {})}
    if isinstance(value, list):
        out = [_strip_empty(raw) for raw in value]
        return [raw for raw in out if raw not in (None, "", [], {})]
    return value


def _degree_code(name: str) -> int | None:
    key = name.strip().casefold()
    for label, code in DEGREE_CODE_BY_NAME.items():
        if label.casefold() == key:
            return code
    return None


def _school_tiers(value: Any, *, school: str = "") -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    found: set[str] = set()
    for raw in raw_values:
        text = _label(raw) or _text(raw)
        if "985" in text:
            found.add("985")
        if "211" in text:
            found.add("211")
    if "985" in found:
        found.add("211")
    if found:
        return [tier for tier in ("985", "211") if tier in found]
    return list(school_tiers_for(school))


def _gender(value: Any) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    text = _text(value)
    if text in {"1", "男", "男性", "male", "m"}:
        return {"code": 1, "name": "男"}
    if text in {"2", "女", "女性", "female", "f"}:
        return {"code": 2, "name": "女"}
    return {"name": text} if text else None


def _work_state(value: Any) -> dict[str, Any] | None:
    text = _label(value) or _text(value)
    if not text:
        return None
    catalog = get_work_state_catalog()
    for code, name in catalog:
        if text in {code, name} or text.casefold() == name.casefold():
            return {"code": code, "name": name}
    return {"name": text}


def _work_type(value: Any) -> dict[str, Any] | None:
    text = _label(value) or _text(value)
    if not text:
        return None
    row: dict[str, Any] = {"name": text}
    if isinstance(value, dict):
        code = _first_text(value.get("code"))
        if code:
            row["code"] = code
    return row


def _salary_code(value: str) -> int | None:
    if not value:
        return None
    codes = resolve_salary_codes_range_overlap(value)
    return codes[0] if codes else None


def _embedding_text(*parts: Any) -> str:
    return " ".join(_text(part) for part in parts if _text(part))


def _normalise_vector(vector: Any, *, dims: int) -> list[float]:
    if not isinstance(vector, list) or len(vector) != dims:
        actual = len(vector) if isinstance(vector, list) else "non-list"
        raise IndexingError(f"embedding dimension mismatch: expected {dims}, received {actual}")
    values: list[float] = []
    for raw in vector:
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise IndexingError("embedding contains a non-numeric value") from exc
        if not math.isfinite(value):
            raise IndexingError("embedding contains NaN or infinity")
        values.append(value)
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        return values
    return [value / norm for value in values]


def _average_vectors(vectors: list[list[float]], *, dims: int) -> list[float]:
    if not vectors:
        return []
    total = [0.0] * dims
    for vector in vectors:
        for index, value in enumerate(vector):
            total[index] += value
    return _normalise_vector([value / len(vectors) for value in total], dims=dims)


def _embed_texts(
    embedder: Embedder,
    texts: list[str],
    *,
    batch_size: int,
    dims: int,
) -> dict[str, list[float]]:
    unique = list(dict.fromkeys(text for text in texts if text.strip()))
    vectors: dict[str, list[float]] = {}
    for offset in range(0, len(unique), max(1, batch_size)):
        batch = unique[offset : offset + max(1, batch_size)]
        try:
            encoded = embedder.embed_many(batch)
        except Exception as exc:
            raise IndexingError(f"embedding request failed: {exc}") from exc
        if len(encoded) != len(batch):
            raise IndexingError(
                f"embedding provider returned {len(encoded)} vectors for {len(batch)} texts"
            )
        for text, vector in zip(batch, encoded, strict=True):
            vectors[text] = _normalise_vector(vector, dims=dims)
    return vectors


def _work_row(raw: dict[str, Any], *, current_time: datetime) -> dict[str, Any]:
    start, end = work_row_start_end(raw, current_time=current_time)
    row: dict[str, Any] = {
        "company": _first_text(_get(raw, "company"), _get(raw, "company_name", "companyName")),
        "job": _first_text(_get(raw, "job"), _get(raw, "job_title", "jobTitle")),
        "description": _first_text(_get(raw, "description", "job_description", "jobDescription")),
    }
    if start is not None:
        row["start_time"] = _normalise_date(start, current_time=current_time)
    if end is not None:
        row["end_time"] = _normalise_date(end, current_time=current_time)
    return _strip_empty(row)


def _project_row(raw: dict[str, Any], *, current_time: datetime) -> dict[str, Any]:
    row: dict[str, Any] = {
        "project_name": _first_text(_get(raw, "project_name", "projectName"), _get(raw, "name")),
        "description": _first_text(
            _get(raw, "description", "project_description", "projectDescription")
        ),
    }
    start = _normalise_date(
        _get(raw, "start_time", "startTime", "start_date", "startDate"),
        current_time=current_time,
    )
    end = _normalise_date(
        _get(raw, "end_time", "endTime", "end_date", "endDate"),
        current_time=current_time,
    )
    if start:
        row["start_time"] = start
    if end:
        row["end_time"] = end
    return _strip_empty(row)


def _education_row(raw: dict[str, Any], *, current_time: datetime) -> dict[str, Any]:
    school = _first_text(_get(raw, "school", "school_name", "schoolName"))
    major = _first_text(_get(raw, "major", "major_name", "majorName"))
    degree = _first_text(_get(raw, "degree", "degree_name", "degreeName"))
    tiers = _school_tiers(_get(raw, "schoolTier", "school_tier"), school=school)
    row: dict[str, Any] = {
        "college": {"name": school},
        "major": {"name": major},
        "degree": {"name": degree, "code": _degree_code(degree)},
    }
    if tiers:
        row["school_tier"] = tiers
    start = _normalise_date(
        _get(raw, "start_time", "startTime", "start_date", "startDate"),
        current_time=current_time,
    )
    end = _normalise_date(
        _get(raw, "end_time", "endTime", "end_date", "endDate"),
        current_time=current_time,
    )
    if start:
        row["start_time"] = start
    if end:
        row["end_time"] = end
    return _strip_empty(row)


def _candidate_id(record: dict[str, Any]) -> str:
    doc_id = _first_text(
        _get(record, "docId", "doc_id"),
        _get(record, "candidateProfileId", "candidate_profile_id"),
        _get(record, "id"),
    )
    if not doc_id:
        raise IndexingError("candidate record has no docId, candidateProfileId, or id")
    return doc_id


def _candidate_profile_id(record: dict[str, Any], doc_id: str) -> str:
    return _first_text(
        _get(record, "candidateProfileId", "candidate_profile_id"),
        doc_id,
    )


def _build_draft(
    record: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    doc_id = _candidate_id(record)
    profile_id = _candidate_profile_id(record, doc_id)
    overview_raw = record.get("overview") if isinstance(record.get("overview"), dict) else {}
    summary = _first_text(
        _get(record, "summary", "semantic_summary_text"),
        _get(overview_raw, "self_evaluation", "selfEvaluation"),
    )
    self_evaluation = _first_text(
        _get(overview_raw, "self_evaluation", "selfEvaluation"),
        _get(record, "selfEvaluation", "self_evaluation"),
        summary,
    )
    city = _first_text(
        _get(record, "currentResidence", "current_residence", "city"),
        _label(_get(overview_raw, "city")),
    )

    work_rows = [
        _work_row(row, current_time=now)
        for row in _as_rows(_get(record, "workExperiences", "work_experiences"))
    ]
    project_rows = [
        _project_row(row, current_time=now)
        for row in _as_rows(_get(record, "projectExperiences", "project_experiences"))
    ]
    internship_rows = [
        _work_row(row, current_time=now)
        for row in _as_rows(_get(record, "internshipExperiences", "internship_experiences"))
    ]
    education_rows = [
        _education_row(row, current_time=now)
        for row in _as_rows(_get(record, "educations", "educationExperiences"))
    ]

    enriched = enrich_profile_work_stats({"work_experiences": work_rows}, as_of=now)
    work_rows = enriched["work_experiences"]
    work_stats = enriched["work_stats"]

    skill_names = _string_names(_get(record, "skills"))
    certificates = _names(_get(record, "certificates"))
    languages = _names(_get(record, "languages"))
    tags = _string_names(_get(record, "tags"))
    talent_tags = _names(_get(record, "talentTags", "talent_tags"))

    intention_raw = record.get("intention") if isinstance(record.get("intention"), dict) else {}
    expected_salary = _first_text(
        _get(record, "expectedSalary", "expected_salary"),
        _label(_get(intention_raw, "expectedSalary")),
    )
    intention = {
        "positions": _names(
            _get(record, "expectedPositions", "expected_positions")
            or _get(intention_raw, "positions")
        ),
        "industries": _names(
            _get(record, "expectedIndustries", "expected_industries")
            or _get(intention_raw, "industries")
        ),
        "cities": _names(
            _get(record, "expectedCities", "expected_cities") or _get(intention_raw, "cities")
        ),
        "expected_salary": expected_salary,
        "expected_salary_code": _salary_code(expected_salary),
        "work_type": _work_type(
            _get(record, "expectedWorkType", "expected_work_type")
            or _get(intention_raw, "work_type", "workType")
        ),
        "work_state": _work_state(
            _get(record, "workState", "work_state")
            or _get(intention_raw, "work_state", "workState")
        ),
    }

    max_education = None
    if education_rows:
        chosen = max(
            education_rows,
            key=lambda row: (
                int(_get(_get(row, "degree"), "code") or 0),
                _text(_get(row, "end_time")),
            ),
        )
        max_education = {
            "code": _get(_get(chosen, "degree"), "code"),
            "name": _get(_get(chosen, "degree"), "name"),
            "end_time": _get(chosen, "end_time"),
        }

    authority_raw = _get(
        record,
        "candidateAuthorityTime",
        "candidate_authority_time",
        "updatedAt",
        "updated_at",
    )
    authority_time = _normalise_date(authority_raw, current_time=now) if authority_raw else None
    if not authority_time:
        authority_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    document = {
        "_id": doc_id,
        "doc_id": doc_id,
        "candidate_profile_id": profile_id,
        "user_id": _get(record, "userId", "user_id"),
        "company_id": 1,
        "entity_join": "profile",
        "deleted": False,
        "profile_pool_id": _get(record, "profilePoolId", "profile_pool_id"),
        "collaborator_id": _get(record, "collaboratorId", "collaborator_id"),
        "candidate_authority_time": authority_time,
        "semantic_summary_text": summary,
        "semantic_summary_text_en": _first_text(
            _get(record, "summaryEn", "summary_en"),
            _get(overview_raw, "summaryEn", "summary_en"),
        ),
        "tags": tags,
        "talent_tags": talent_tags,
        "overview": {
            "name": _first_text(
                _get(record, "name"), _get(overview_raw, "name", "candidate_name", "candidateName")
            ),
            "work_years": _get(record, "workYears", "work_years"),
            "age": _get(record, "age"),
            "birth": _normalise_date(_get(record, "birth"), current_time=now),
            "self_evaluation": self_evaluation,
            "city": {"name": city},
            "gender": _gender(_get(record, "gender", "sex")),
            "profilePictureUrl": _text(_get(record, "avatar")),
        },
        "skills": [{"name": name} for name in skill_names],
        "work_experiences": work_rows,
        "internship_experiences": internship_rows,
        "project_experiences": project_rows,
        "educations": education_rows,
        "max_education": max_education,
        "intention": intention,
        "work_stats": work_stats,
        "certificates": certificates,
        "languages": languages,
    }

    work_texts = [
        _embedding_text(row.get("company"), row.get("job"), row.get("description"))
        for row in work_rows
    ]
    project_texts = [
        _embedding_text(row.get("project_name"), row.get("description")) for row in project_rows
    ]
    education_texts = [
        _embedding_text(
            _get(_get(row, "college"), "name"),
            _get(_get(row, "major"), "name"),
            _get(_get(row, "degree"), "name"),
        )
        for row in education_rows
    ]
    job_titles = [row.get("job") for row in work_rows]
    return {
        "document": _strip_empty(document),
        "work_texts": work_texts,
        "project_texts": project_texts,
        "education_texts": education_texts,
        "self_evaluation_text": self_evaluation,
        "skills_text": _embedding_text("技能", *skill_names, *job_titles),
    }


def convert_candidates_to_documents(
    records: list[dict[str, Any]],
    *,
    company_id: int,
    config: HybridV2Config | None = None,
    embedder: Embedder | None = None,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    """Convert public candidate records into documents accepted by the mapping."""

    cfg = config or HybridV2Config.from_env()
    active_embedder = embedder or build_embedder(cfg)
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    drafts = [_build_draft(record, now=now) for record in records]
    all_texts: list[str] = []
    for draft in drafts:
        all_texts.extend(draft["work_texts"])
        all_texts.extend(draft["project_texts"])
        all_texts.extend(draft["education_texts"])
        all_texts.append(draft["self_evaluation_text"])
        all_texts.append(draft["skills_text"])
    vectors = _embed_texts(
        active_embedder,
        all_texts,
        batch_size=cfg.embedding_batch_size,
        dims=cfg.embedding_dims,
    )

    documents: list[dict[str, Any]] = []
    for draft in drafts:
        document = draft["document"]
        document["company_id"] = int(company_id)
        document["entity_join"] = cfg.entity_join_value
        work_vectors = [vectors[text] for text in draft["work_texts"] if text]
        project_vectors = [vectors[text] for text in draft["project_texts"] if text]
        education_vectors = [vectors[text] for text in draft["education_texts"] if text]
        if cfg.vector_mode == "nested":
            document["work_segments"] = [
                {
                    "segment_id": f"work-{index}",
                    "company": row.get("company"),
                    "job": row.get("job"),
                    "description": row.get("description"),
                    "start_time": row.get("start_time"),
                    "end_time": row.get("end_time"),
                    "vector": vectors[text],
                }
                for index, (row, text) in enumerate(
                    zip(document["work_experiences"], draft["work_texts"], strict=True)
                )
                if text
            ]
            document["project_segments"] = [
                {
                    "segment_id": f"project-{index}",
                    "project_name": row.get("project_name"),
                    "description": row.get("description"),
                    "vector": vectors[text],
                }
                for index, (row, text) in enumerate(
                    zip(document["project_experiences"], draft["project_texts"], strict=True)
                )
                if text
            ]
            document["education_segments"] = [
                {
                    "segment_id": f"education-{index}",
                    "school": _get(_get(row, "college"), "name"),
                    "major": _get(_get(row, "major"), "name"),
                    "degree": _get(_get(row, "degree"), "name"),
                    "school_tier": _get(row, "school_tier"),
                    "vector": vectors[text],
                }
                for index, (row, text) in enumerate(
                    zip(document["educations"], draft["education_texts"], strict=True)
                )
                if text
            ]
        else:
            if work_vectors:
                document["work_vector"] = _average_vectors(work_vectors, dims=cfg.embedding_dims)
            if project_vectors:
                document["project_vector"] = _average_vectors(
                    project_vectors, dims=cfg.embedding_dims
                )
            if education_vectors:
                document["education_vector"] = _average_vectors(
                    education_vectors, dims=cfg.embedding_dims
                )
        self_text = draft["self_evaluation_text"]
        if self_text and self_text in vectors:
            document["self_evaluation_vector"] = vectors[self_text]
        skills_text = draft["skills_text"]
        if skills_text and skills_text in vectors:
            document["skills_vector"] = vectors[skills_text]
        documents.append(_strip_empty(document))
    return documents


def _mapping_properties(mapping_response: Any, index: str) -> dict[str, Any]:
    payload = dict(mapping_response) if not isinstance(mapping_response, dict) else mapping_response
    selected = payload.get(index) if isinstance(payload.get(index), dict) else payload
    mappings = selected.get("mappings") if isinstance(selected, dict) else None
    properties = mappings.get("properties") if isinstance(mappings, dict) else None
    return properties if isinstance(properties, dict) else {}


def _resolve_property(properties: dict[str, Any], dotted: str) -> dict[str, Any] | None:
    current: Any = {"properties": properties}
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        candidates: list[dict[str, Any]] = []
        for key in ("properties", "fields"):
            values = current.get(key)
            if isinstance(values, dict):
                candidates.append(values)
        next_value = next((values[part] for values in candidates if part in values), None)
        if next_value is None:
            return None
        current = next_value
    return current if isinstance(current, dict) else None


def _configured_vector_fields(config: HybridV2Config) -> list[str]:
    if config.vector_mode == "nested":
        return [
            "work_segments.vector",
            "project_segments.vector",
            "education_segments.vector",
            "self_evaluation_vector",
            "skills_vector",
        ]
    return [
        "work_vector",
        "project_vector",
        "education_vector",
        "self_evaluation_vector",
        "skills_vector",
    ]


class HybridV2Indexer:
    """Create, populate, and diagnose the Hybrid V2 candidate index."""

    def __init__(
        self,
        config: HybridV2Config | None = None,
        *,
        client: ElasticsearchClient | Any | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self._config = config or HybridV2Config.from_env()
        self._client = client or ElasticsearchClient(self._config)
        self._embedder = embedder or build_embedder(self._config)

    @property
    def config(self) -> HybridV2Config:
        return self._config

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def create_index(
        self,
        *,
        recreate: bool = False,
        number_of_shards: int = 1,
        number_of_replicas: int = 0,
    ) -> IndexReport:
        mapping = build_candidate_index_mapping(
            embedding_dims=self._config.embedding_dims,
            vector_mode=self._config.vector_mode,
            tenant_field=self._config.tenant_field,
            entity_join_field=self._config.entity_join_field,
            soft_delete_field=self._config.soft_delete_field,
            number_of_shards=number_of_shards,
            number_of_replicas=number_of_replicas,
        )
        created = self._client.create_index(
            self._config.es_index,
            mapping,
            recreate=recreate,
        )
        return IndexReport(
            index=self._config.es_index,
            created=created,
            documents=0,
            vector_mode=self._config.vector_mode,
            embedding_dims=self._config.embedding_dims,
        )

    def index_records(
        self,
        records: list[dict[str, Any]],
        *,
        company_id: int = 1,
        recreate: bool = False,
        batch_size: int = 500,
        refresh: str | bool = "wait_for",
        number_of_shards: int = 1,
        number_of_replicas: int = 0,
    ) -> IndexReport:
        created = self._client.create_index(
            self._config.es_index,
            build_candidate_index_mapping(
                embedding_dims=self._config.embedding_dims,
                vector_mode=self._config.vector_mode,
                tenant_field=self._config.tenant_field,
                entity_join_field=self._config.entity_join_field,
                soft_delete_field=self._config.soft_delete_field,
                number_of_shards=number_of_shards,
                number_of_replicas=number_of_replicas,
            ),
            recreate=recreate,
        )
        documents = convert_candidates_to_documents(
            records,
            company_id=company_id,
            config=self._config,
            embedder=self._embedder,
        )
        if documents:
            self._client.index_documents(
                self._config.es_index,
                documents,
                batch_size=batch_size,
                refresh=refresh,
            )
        elif refresh:
            self._client.refresh_index(self._config.es_index)
        count = self._client.count_documents(self._config.es_index)
        return IndexReport(
            index=self._config.es_index,
            created=created,
            documents=count,
            vector_mode=self._config.vector_mode,
            embedding_dims=self._config.embedding_dims,
            refreshed=bool(refresh),
        )

    def index_file(
        self,
        path: str | Path,
        *,
        company_id: int = 1,
        recreate: bool = False,
        batch_size: int = 500,
        refresh: str | bool = "wait_for",
        number_of_shards: int = 1,
        number_of_replicas: int = 0,
    ) -> IndexReport:
        return self.index_records(
            load_candidate_records(path),
            company_id=company_id,
            recreate=recreate,
            batch_size=batch_size,
            refresh=refresh,
            number_of_shards=number_of_shards,
            number_of_replicas=number_of_replicas,
        )

    def doctor(self) -> DoctorReport:
        checks: list[DoctorCheck] = []
        try:
            health = self._client.health()
            checks.append(
                DoctorCheck(
                    "cluster",
                    True,
                    f"{health.get('version') or 'unknown'} at {self._config.es_url}",
                )
            )
        except Exception as exc:
            checks.append(DoctorCheck("cluster", False, str(exc)))
            return DoctorReport(self._config.es_index, False, tuple(checks))

        if not self._client.index_exists(self._config.es_index):
            checks.append(
                DoctorCheck("index", False, f"index does not exist: {self._config.es_index}")
            )
            return DoctorReport(self._config.es_index, False, tuple(checks))

        checks.append(DoctorCheck("index", True, self._config.es_index))
        properties = _mapping_properties(
            self._client.get_mapping(self._config.es_index),
            self._config.es_index,
        )
        required = [
            "candidate_profile_id",
            "overview.name.keyword",
            "overview.work_years",
            "skills.name.keyword",
            "work_experiences.company",
            "work_experiences.job",
            "project_experiences.project_name",
            "educations.college.name",
            "educations.major.name",
            "educations.degree.code",
            "intention.positions.name",
            "work_stats.segment_count",
            "talent_tags.tag_id",
        ]
        if self._config.tenant_field:
            required.append(self._config.tenant_field)
        missing = [path for path in required if _resolve_property(properties, path) is None]
        checks.append(
            DoctorCheck(
                "mapping",
                not missing,
                "all required fields are present"
                if not missing
                else f"missing fields: {', '.join(missing)}",
            )
        )

        vector_errors: list[str] = []
        for field in _configured_vector_fields(self._config):
            definition = _resolve_property(properties, field)
            if definition is None:
                vector_errors.append(f"{field} is missing")
                continue
            if definition.get("type") != "dense_vector":
                vector_errors.append(f"{field} is {definition.get('type')!r}, not dense_vector")
                continue
            dims = definition.get("dims")
            if dims != self._config.embedding_dims:
                vector_errors.append(f"{field} dims={dims}, expected {self._config.embedding_dims}")
        checks.append(
            DoctorCheck(
                "vectors",
                not vector_errors,
                f"{self._config.vector_mode} vectors match dims={self._config.embedding_dims}"
                if not vector_errors
                else "; ".join(vector_errors),
            )
        )

        try:
            count = self._client.count_documents(self._config.es_index)
            checks.append(DoctorCheck("documents", True, f"{count} documents"))
        except Exception as exc:
            checks.append(DoctorCheck("documents", False, str(exc)))
        return DoctorReport(
            index=self._config.es_index,
            ok=all(check.ok for check in checks),
            checks=tuple(checks),
        )


__all__ = [
    "DoctorCheck",
    "DoctorReport",
    "HybridV2Indexer",
    "IndexReport",
    "IndexingError",
    "build_candidate_index_mapping",
    "convert_candidates_to_documents",
    "load_candidate_records",
]
