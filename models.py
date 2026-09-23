"""Dataset field names are preserved; missing skills are resolved by scoring."""
from datetime import date as Date
from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

Level = Annotated[int, Field(ge=0, le=5)]
Grade = Literal['Junior', 'Middle', 'Senior', 'Lead']

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Skill(Model):
    skill_id: str
    name: str
    type: Literal['hard', 'soft']
    category: str
    description: str

class RoleProfile(Model):
    role: str
    grade: Grade
    required_skills: dict[str, Level]
    critical_skills: list[str]

class SkillsFile(Model):
    meta: dict[str, Any]
    proficiency_scale: dict[str, str]
    skills: list[Skill]
    role_profiles: list[RoleProfile]

class CareerGoal(Model):
    target_role: str
    target_grade: Grade

class Employee(Model):
    employee_id: str
    full_name: str
    department: str
    role: str
    grade: Grade
    manager_id: str | None
    hire_date: Date
    tenure_months: Annotated[int, Field(ge=0)]
    work_format: Literal['office', 'hybrid', 'remote']
    preferred_language: Literal['kk', 'ru', 'en']
    career_goal: CareerGoal | None
    skills: dict[str, Level]
    last_review_date: Date

class EmployeesFile(Model):
    meta: dict[str, Any]
    employees: list[Employee]

class Development(Model):
    skill_id: str
    gain: Annotated[int, Field(ge=0, le=5)]
    max_level: Level

class Event(Model):
    event_id: str
    title: str
    description: str
    type: Literal['compliance', 'onboarding', 'course', 'workshop', 'mentoring', 'certification', 'meetup']
    format: Literal['online', 'offline', 'self_paced']
    duration_hours: Annotated[float, Field(ge=0)]
    mandatory: bool
    target_roles: list[str]
    target_grades: list[Grade]
    develops_skills: list[Development]
    prerequisites: dict[str, Level]
    upcoming_sessions: list[Date]

class EventsFile(Model):
    meta: dict[str, Any]
    events: list[Event]

class Activity(Model):
    record_id: str
    employee_id: str
    event_id: str
    date: Date
    due_date: Date | None
    status: Literal['completed', 'in_progress', 'dropped', 'no_show', 'declined', 'overdue']
    completion_pct: Annotated[int, Field(ge=0, le=100)]
    score: Annotated[float, Field(ge=0, le=100)] | None
    feedback_rating: Annotated[int, Field(ge=1, le=5)] | None
    assigned_by: Literal['self', 'manager', 'hr']

    @field_validator('due_date', 'score', 'feedback_rating', mode='before')
    @classmethod
    def blank_to_none(cls, value):
        return None if value == '' else value
