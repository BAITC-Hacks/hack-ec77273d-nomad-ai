"""Shared API contract. Employee skills are effective levels, never import snapshots."""
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

Grade = Literal['Junior', 'Middle', 'Senior', 'Lead']
Level = Annotated[StrictInt, Field(ge=0, le=5)]
Percentage = Annotated[float, Field(ge=0, le=100)]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Target(Contract):
    role: str
    grade: Grade
    source: Literal['career_goal', 'next_grade', 'current_grade']


class CareerGoal(Contract):
    target_role: str
    target_grade: Grade


class History(Contract):
    record_id: str
    employee_id: str
    event_id: str
    date: str
    due_date: str | None
    status: Literal['completed', 'in_progress', 'dropped', 'no_show', 'declined', 'overdue']
    completion_pct: Annotated[StrictInt, Field(ge=0, le=100)]
    score: Annotated[StrictInt, Field(ge=0, le=100)] | None
    feedback_rating: Annotated[StrictInt, Field(ge=1, le=5)] | None
    assigned_by: Literal['self', 'manager', 'hr']

    @field_validator('date', 'due_date')
    @classmethod
    def iso_date(cls, value):
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError('Expected ISO date YYYY-MM-DD')
        return value


class Employee(Contract):
    employee_id: str
    full_name: str
    department: str
    role: str
    grade: Grade
    tenure_months: Annotated[StrictInt, Field(ge=0)]
    work_format: str
    preferred_language: str
    career_goal: CareerGoal | None
    last_review_date: str
    skills: dict[str, Level]
    target: Target
    completed_event_ids: list[str]
    revision: Annotated[StrictInt, Field(ge=0)]
    as_of_date: str


class SkillGap(Contract):
    skill_id: str
    name: str
    current: Level
    required: Level
    gap: Level
    critical: bool


class Trajectory(Contract):
    employee_id: str
    revision: int
    target: Target
    coverage: Percentage | None
    critical_gap_units: int
    critical_blockers: int
    gaps: list[SkillGap]
    status: Literal['active', 'requirements_met', 'no_requirements', 'goal_needed']


class Impact(Contract):
    skill_id: str
    before: Level
    after: Level
    gain_applied: Level
    max_level: Level
    required: Level | None


class Factor(Contract):
    fact_id: str
    category: Literal['grade', 'target', 'gap', 'history', 'eligibility', 'impact']
    text: str


class Step(Contract):
    event_id: str
    title: str
    format: str
    duration_hours: Annotated[float, Field(gt=0)]
    planned_start: str
    planned_end: str
    impacts: list[Impact]
    coverage_after: Percentage | None
    critical_gap_units_after: int


class Recommendation(Contract):
    route_id: str
    event_id: str
    title: str
    action: Literal['start', 'continue']
    score: float
    reason: str
    factors: list[Factor]
    impacts: list[Impact]
    progress_before: Percentage | None
    progress_after: Percentage | None
    critical_gap_before: int
    critical_gap_after: int
    steps: Annotated[list[Step], Field(min_length=1, max_length=2)]
    unlocked_event_ids: list[str]


class Baseline(Contract):
    event_id: str
    skill_id: str
    reason: str


class RecommendationResponse(Contract):
    employee_id: str
    revision: int
    mode: Literal['llm', 'fallback']
    fallback_reason: str | None
    recommendations: Annotated[list[Recommendation], Field(max_length=3)]
    baseline: Baseline | None
    no_step_reason: str | None


class HRSkillGap(Contract):
    skill_id: str
    name: str
    affected_count: int
    eligible_population: int
    mean_gap: float


class NoNextStep(Contract):
    employee_id: str
    reason: str


class HRActivity(Contract):
    event_id: str
    title: str
    mandatory: bool
    unique_participants: int
    status_counts: dict[str, int]
    completed_records: int
    total_records: int
    completion_rate: Percentage | None


class HROverview(Contract):
    as_of_date: str
    total_employees: int
    skill_gaps: list[HRSkillGap]
    no_next_step: list[NoNextStep]
    activities: list[HRActivity]
