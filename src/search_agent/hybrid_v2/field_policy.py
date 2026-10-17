"""Field policies for compiling Hybrid V2 IR into Hybrid V1 structures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .schemas import StructuredField

HardStrategy = Literal[
    "term", "text", "range", "birth_year", "work_state", "talent_tag", "cert_lang", "work_stats"
]

# Only these structured fields may receive compiler default soft supplements.
SEMANTIC_SUPPLEMENT_FIELDS: frozenset[StructuredField] = frozenset({"skill"})


@dataclass(frozen=True)
class FieldPolicy:
    """Compilation policy for a structured IR field."""

    field: StructuredField
    hard_strategy: HardStrategy
    es_field: str
    keyword_field: str | None = None
    nested_path: str | None = None
    soft_fields: tuple[str, ...] = ()
    soft_template: str | None = None
    precise: bool = True
    add_soft_by_default: bool = False

    @property
    def hard_field(self) -> str:
        return self.keyword_field or self.es_field


FIELD_POLICIES: dict[StructuredField, FieldPolicy] = {
    "name": FieldPolicy(
        field="name",
        hard_strategy="term",
        es_field="overview.name",
        keyword_field="overview.name.keyword",
        precise=True,
        add_soft_by_default=False,
    ),
    "skill": FieldPolicy(
        field="skill",
        hard_strategy="term",
        es_field="skills.name",
        keyword_field="skills.name.keyword",
        nested_path="skills",
        soft_fields=("skills_vector", "work_vector", "self_evaluation_vector"),
        soft_template="{values} 技能经验",
        add_soft_by_default=True,
    ),
    "company": FieldPolicy(
        field="company",
        hard_strategy="text",
        es_field="work_experiences.company",
        nested_path="work_experiences",
        soft_fields=("work_vector",),
        soft_template="{values} 工作经历",
        add_soft_by_default=False,
    ),
    "internship_company": FieldPolicy(
        field="internship_company",
        hard_strategy="text",
        es_field="internship_experiences.company",
        nested_path="internship_experiences",
        add_soft_by_default=False,
    ),
    "position": FieldPolicy(
        field="position",
        hard_strategy="text",
        es_field="work_experiences.job",
        nested_path="work_experiences",
        soft_fields=("work_vector",),
        soft_template="{values} 工作经历",
        add_soft_by_default=False,
    ),
    "internship_job": FieldPolicy(
        field="internship_job",
        hard_strategy="text",
        es_field="internship_experiences.job",
        nested_path="internship_experiences",
        add_soft_by_default=False,
    ),
    "intention_position": FieldPolicy(
        field="intention_position",
        hard_strategy="text",
        es_field="intention.positions.name",
        keyword_field="intention.positions.name.keyword",
        nested_path="intention.positions",
        precise=True,
        add_soft_by_default=False,
    ),
    "school": FieldPolicy(
        field="school",
        hard_strategy="term",
        es_field="educations.college.name",
        keyword_field="educations.college.name.keyword",
        nested_path="educations",
        soft_fields=("education_vector",),
        soft_template="{values} 教育背景",
        add_soft_by_default=False,
    ),
    "major": FieldPolicy(
        field="major",
        hard_strategy="term",
        es_field="educations.major.name",
        keyword_field="educations.major.name.keyword",
        nested_path="educations",
        soft_fields=("education_vector",),
        soft_template="{values} 就读专业",
        add_soft_by_default=False,
    ),
    "degree": FieldPolicy(
        field="degree",
        hard_strategy="range",
        es_field="educations.degree.code",
        nested_path="educations",
        soft_fields=("education_vector",),
        soft_template="{values}学历",
        precise=True,
        add_soft_by_default=False,
    ),
    "city": FieldPolicy(
        field="city",
        hard_strategy="term",
        es_field="overview.city.name",
        keyword_field="overview.city.name.keyword",
        soft_fields=("work_vector",),
        soft_template="城市{values}相关工作经历",
        precise=True,
        add_soft_by_default=False,
    ),
    "intention_city": FieldPolicy(
        field="intention_city",
        hard_strategy="term",
        es_field="intention.cities.name",
        keyword_field="intention.cities.name.keyword",
        nested_path="intention.cities",
        soft_fields=("work_vector",),
        soft_template="期望工作城市{values}",
        precise=True,
        add_soft_by_default=False,
    ),
    "live_city": FieldPolicy(
        field="live_city",
        hard_strategy="term",
        es_field="overview.city.name",
        keyword_field="overview.city.name.keyword",
        soft_fields=("work_vector",),
        soft_template="现居城市{values}相关工作经历",
        precise=True,
        add_soft_by_default=False,
    ),
    "work_years": FieldPolicy(
        field="work_years",
        hard_strategy="range",
        es_field="overview.work_years",
        precise=True,
    ),
    "work_segment_count": FieldPolicy(
        field="work_segment_count",
        hard_strategy="work_stats",
        es_field="work_stats.segment_count",
        precise=True,
    ),
    "employment_gap": FieldPolicy(
        field="employment_gap",
        hard_strategy="work_stats",
        es_field="work_stats.latest_work_end",
        precise=True,
    ),
    "latest_company": FieldPolicy(
        field="latest_company",
        hard_strategy="work_stats",
        es_field="work_stats.latest_work_company.keyword",
        precise=True,
    ),
    "latest_work_job": FieldPolicy(
        field="latest_work_job",
        hard_strategy="work_stats",
        es_field="work_stats.latest_work_job",
        keyword_field="work_stats.latest_work_job.keyword",
        precise=True,
    ),
    "recent_tenure_1": FieldPolicy(
        field="recent_tenure_1",
        hard_strategy="work_stats",
        es_field="work_stats.recent_tenure_1_months",
        precise=True,
    ),
    "max_tenure": FieldPolicy(
        field="max_tenure",
        hard_strategy="work_stats",
        es_field="work_stats.max_tenure_months",
        precise=True,
    ),
    "avg_tenure": FieldPolicy(
        field="avg_tenure",
        hard_strategy="work_stats",
        es_field="work_stats.avg_tenure_months",
        precise=True,
    ),
    "short_job_count": FieldPolicy(
        field="short_job_count",
        hard_strategy="work_stats",
        es_field="work_stats.short_job_count",
        precise=True,
    ),
    "job_stability": FieldPolicy(
        field="job_stability",
        hard_strategy="work_stats",
        es_field="work_stats.recent_tenure_1_months",
        precise=True,
    ),
    "inter_segment_gap": FieldPolicy(
        field="inter_segment_gap",
        hard_strategy="work_stats",
        es_field="work_stats.inter_segment_gap_count_gt_6m",
        precise=True,
    ),
    "max_inter_segment_gap": FieldPolicy(
        field="max_inter_segment_gap",
        hard_strategy="work_stats",
        es_field="work_stats.max_inter_segment_gap_months",
        precise=True,
    ),
    "career_discontinuity": FieldPolicy(
        field="career_discontinuity",
        hard_strategy="work_stats",
        es_field="work_stats.max_inter_segment_gap_months",
        precise=True,
    ),
    "role_tenure": FieldPolicy(
        field="role_tenure",
        hard_strategy="work_stats",
        es_field="work_stats.role_tenure.tenure_months",
        nested_path="work_stats.role_tenure",
        precise=True,
    ),
    "graduation_year": FieldPolicy(
        field="graduation_year",
        hard_strategy="birth_year",
        es_field="max_education.end_time",
        precise=True,
    ),
    "age": FieldPolicy(
        field="age",
        hard_strategy="range",
        es_field="overview.age",
        precise=True,
    ),
    "birth_year": FieldPolicy(
        field="birth_year",
        hard_strategy="birth_year",
        es_field="overview.birth",
        precise=True,
    ),
    "gender": FieldPolicy(
        field="gender",
        hard_strategy="term",
        es_field="overview.gender.name",
        keyword_field="overview.gender.name.keyword",
        precise=True,
    ),
    "work_state": FieldPolicy(
        field="work_state",
        hard_strategy="work_state",
        es_field="intention.work_state.code",
        precise=True,
    ),
    "expected_salary": FieldPolicy(
        field="expected_salary",
        hard_strategy="range",
        es_field="intention.expected_salary_code",
        precise=True,
    ),
    "talent_tag": FieldPolicy(
        field="talent_tag",
        hard_strategy="talent_tag",
        es_field="talent_tags.tag_id",
        nested_path="talent_tags",
        soft_fields=("education_vector",),
        soft_template="{values} 人才标签",
        precise=True,
        add_soft_by_default=False,
    ),
    "certificate": FieldPolicy(
        field="certificate",
        hard_strategy="cert_lang",
        es_field="certificates.name",
        keyword_field="certificates.name.keyword",
        nested_path="certificates",
        precise=True,
        add_soft_by_default=False,
    ),
    # 同 certificate；硬条件 DSL 与 skill 一致（term/terms + nested）。
    "language": FieldPolicy(
        field="language",
        hard_strategy="cert_lang",
        es_field="languages.name",
        keyword_field="languages.name.keyword",
        nested_path="languages",
        precise=True,
        add_soft_by_default=False,
    ),
}

DEGREE_CODE_BY_NAME: dict[str, int] = {
    "初中及以下": 1,
    "中专": 2,
    "高中": 3,
    "大专": 4,
    "专科": 4,
    "本科": 5,
    "学士": 5,
    "硕士": 6,
    "研究生": 6,
    "博士": 7,
    "博士以上": 8,
    "mba": 10,
    "MBA": 10,
}


def get_field_policy(field: StructuredField) -> FieldPolicy:
    """Return the policy for a structured IR field."""

    return FIELD_POLICIES[field]


__all__ = [
    "DEGREE_CODE_BY_NAME",
    "FIELD_POLICIES",
    "FieldPolicy",
    "HardStrategy",
    "SEMANTIC_SUPPLEMENT_FIELDS",
    "get_field_policy",
]
