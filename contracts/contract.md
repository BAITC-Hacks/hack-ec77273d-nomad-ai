# Career Quest — shared data contract

Authoritative executable models: `backend/app/contracts.py`. Example payloads:
`contracts/examples.json`. All API names are snake_case; dates are ISO `YYYY-MM-DD`;
percentages are numbers 0–100; unknown values are `null`; empty collections are `[]`.
Skill levels are integers 0–5. Changes to this shared contract must be coordinated
across the API, domain and frontend owners.

```typescript
type Grade = 'Junior'|'Middle'|'Senior'|'Lead';
type Target = {role:string; grade:Grade; source:'career_goal'|'next_grade'|'current_grade'};
type History = {record_id:string; employee_id:string; event_id:string;
 date:string; due_date:string|null;
 status:'completed'|'in_progress'|'dropped'|'no_show'|'declined'|'overdue';
 completion_pct:number; score:number|null; feedback_rating:number|null;
 assigned_by:'self'|'manager'|'hr'};
type Employee = {employee_id:string; full_name:string; department:string;
 role:string; grade:Grade; tenure_months:number; work_format:string;
 preferred_language:string; career_goal:{target_role:string;target_grade:Grade}|null;
 last_review_date:string; skills:Record<string,number>; target:Target;
 completed_event_ids:string[]; revision:number; as_of_date:string};
type SkillGap = {skill_id:string; name:string; current:number; required:number;
 gap:number; critical:boolean};
type Trajectory = {employee_id:string; revision:number; target:Target;
 coverage:number|null; critical_gap_units:number; critical_blockers:number;
 gaps:SkillGap[]; status:'active'|'requirements_met'|'no_requirements'|'goal_needed'};
type Impact = {skill_id:string; before:number; after:number; gain_applied:number;
 max_level:number; required:number|null};
type Factor = {fact_id:string; category:'grade'|'target'|'gap'|'history'|'eligibility'|'impact';
 text:string};
type Step = {event_id:string; title:string; format:string; duration_hours:number;
 planned_start:string; planned_end:string; impacts:Impact[];
 coverage_after:number|null; critical_gap_units_after:number};
type Recommendation = {route_id:string; event_id:string; title:string;
 action:'start'|'continue'; score:number; reason:string; factors:Factor[];
 impacts:Impact[]; progress_before:number|null; progress_after:number|null;
 critical_gap_before:number; critical_gap_after:number;
 steps:Step[]; unlocked_event_ids:string[]};
type RecommendationResponse = {employee_id:string; revision:number;
 mode:'llm'|'fallback'; fallback_reason:string|null;
 recommendations:Recommendation[];
 baseline:{event_id:string; skill_id:string; reason:string}|null;
 no_step_reason:string|null};
type HROverview = {as_of_date:string; total_employees:number;
 skill_gaps:{skill_id:string;name:string;affected_count:number;
 eligible_population:number;mean_gap:number}[];
 no_next_step:{employee_id:string;reason:string}[];
 activities:{event_id:string;title:string;mandatory:boolean;
 unique_participants:number;status_counts:Record<string,number>;
 completed_records:number;total_records:number;completion_rate:number|null}[]};
```

`Employee.skills` contains effective levels: the assessment snapshot plus eligible
completed history after `last_review_date` and confirmed demo completions, applied
once. Import uses the starter-kit raw Employee shape with `hire_date` and
`manager_id`; the public Employee shape is explicitly rejected during import.

`Recommendation.progress_after` and `impacts` describe the first step.
`steps[-1].coverage_after` describes the complete one- or two-step scenario. `score`
is utility, not probability. Impacts include actual growth outside the target
requirements (`required=null`). Coverage is weighted requirement fulfillment:
critical skills have weight 2, other requirements weight 1. It is not a promotion
probability and never changes the employee's grade.

Scenario dates assume two study hours per calendar day. They represent a planning
assumption, not a booked session or a promise. Scheduled activity start dates come
from the catalog. Self-paced activities need no scheduled session. Dataset history
provides enrollment/session dates rather than exact completion timestamps; these
dates are the available approximation when replaying post-review completions.

HR `eligible_population` is the number of employees whose selected target requires
the skill. `mean_gap` averages across that entire population, including zero gaps.
Activity completion rates use records, while `unique_participants` counts people.

Unified error response:

```json
{"error":{"code":"VALIDATION_ERROR","message":"Некорректный импорт","details":[{"path":"employees[0].skills.SK_SQL","message":"Expected integer 0..5"}]}}
```

HTTP: 401 no session; 403 insufficient role; 404 unknown ID; 409 revision conflict
or duplicate; 422 invalid data; 503 application unavailable. An unavailable or
invalid model produces a successful recommendation response with `mode=fallback`
and an explicit `fallback_reason`, never HTTP 503 by itself.

The domain returns `_candidates` (maximum five) and `_rerankable_route_ids` internally;
the API must strip these fields from RecommendationResponse. They are not additions
to the shared public contract.
