"""Local, explainable three-step planning. Never changes source employee data."""

from calendar import monthrange
from datetime import date, timedelta
from math import ceil

from .data_loader import REPEATABLE_EVENT_IDS

GRADES = ("Junior", "Middle", "Senior", "Lead")
VERSION = "three-step-rules-v1"


def apply_effects(skills, event):
    result = dict(skills)
    for effect in event["develops_skills"]:
        before = result.get(effect["skill_id"], 0)
        result[effect["skill_id"]] = max(
            before, min(before + effect["gain"], effect["max_level"])
        )
    return result


def coverage(skills, profile):
    required = profile["required_skills"]
    critical = set(profile["critical_skills"])
    denominator = sum((2 if sid in critical else 1) * level for sid, level in required.items())
    numerator = sum((2 if sid in critical else 1) * min(skills.get(sid, 0), level)
                    for sid, level in required.items())
    gaps = {sid: max(level - skills.get(sid, 0), 0) for sid, level in required.items()}
    return {
        "coverage": 100 * numerator / denominator if denominator else None,
        "status": "ok" if denominator else "no_requirements",
        "critical_gap_units": sum(gaps.get(sid, 0) for sid in critical),
        "critical_blockers": sum(gaps.get(sid, 0) > 0 for sid in critical),
        "gaps": gaps,
    }


def friction(event, history, events, as_of):
    start = as_of.replace(year=as_of.year - 1,
                         day=min(as_of.day, monthrange(as_of.year - 1, as_of.month)[1]))
    rows = [r for r in history if start <= date.fromisoformat(r["date"]) <= as_of
            and not events[r["event_id"]]["mandatory"]]

    def penalty(group):
        counts = {status: sum(r["status"] == status for r in group)
                  for status in ("completed", "no_show", "declined", "dropped")}
        value = (counts["no_show"] + .5 * counts["declined"] + .75 * counts["dropped"])
        return value / (sum(counts.values()) + 3), counts

    event_penalty, event_counts = penalty([r for r in rows if r["event_id"] == event["event_id"]])
    format_penalty, format_counts = penalty([
        r for r in rows if events[r["event_id"]]["format"] == event["format"]
    ])
    return {
        "friction": .7 * event_penalty + .3 * format_penalty,
        "event_counts": event_counts,
        "format_counts": format_counts,
        "history_evidence": "available" if sum(format_counts.values()) else "insufficient",
    }


def generate_plan(dataset, employee_id):
    employee = dataset.employees_by_id[employee_id]
    goal = employee.get("career_goal")
    target = goal or {
        "target_role": employee["role"],
        "target_grade": GRADES[min(GRADES.index(employee["grade"]) + 1, len(GRADES) - 1)],
    }
    profile = dataset.role_profiles_by_key[(target["target_role"], target["target_grade"])]
    history = [r for r in dataset.history_by_employee.get(employee_id, [])
               if r["date"] <= dataset.as_of_date.isoformat()]
    skills = dict(employee["skills"])
    for row in sorted(history, key=lambda r: (r["date"], r["record_id"])):
        if row["status"] == "completed" and row["date"] > employee["last_review_date"]:
            skills = apply_effects(skills, dataset.events_by_id[row["event_id"]])

    completed = {r["event_id"] for r in history if r["status"] == "completed"}
    latest = {r["event_id"]: r for r in sorted(history, key=lambda r: (r["date"], r["record_id"]))}
    events = sorted(dataset.events_by_id.values(), key=lambda e: e["event_id"])
    signals = {e["event_id"]: friction(e, history, dataset.events_by_id, dataset.as_of_date)
               for e in events}
    before = coverage(skills, profile)
    best_steps, best_score = [], float("-inf")
    routes_by_first = {}

    def candidates(state, earliest, used):
        for event in events:
            eid = event["event_id"]
            if event["mandatory"] or eid in used:
                continue
            if employee["role"] not in event["target_roles"] or employee["grade"] not in event["target_grades"]:
                continue
            if any(state.get(s, 0) < level for s, level in event["prerequisites"].items()):
                continue
            if eid in completed and eid not in REPEATABLE_EVENT_IDS:
                continue
            continuing = latest.get(eid, {}).get("status") == "in_progress"
            used_sessions = {r["date"] for r in history
                             if r["event_id"] == eid and r["status"] == "completed"}
            if continuing or event["format"] == "self_paced":
                start = earliest
            else:
                sessions = sorted(date.fromisoformat(s) for s in event["upcoming_sessions"]
                                  if date.fromisoformat(s) >= earliest and s not in used_sessions)
                if not sessions:
                    continue
                start = sessions[0]
            after = apply_effects(state, event)
            if after == state or not any(after.get(s, 0) > state.get(s, 0) for s in after):
                continue
            end = start + timedelta(days=max(1, ceil(event["duration_hours"] / 2)))
            yield event, after, start, end, continuing

    def useful_route(steps):
        # Every step must close a target gap or raise a prerequisite required by
        # a later useful step. This excludes padding with unrelated activities.
        needed = {}
        for step in reversed(steps):
            changes = step["skill_changes"]
            closes_gap = any(c["gap_closed"] > 0 for c in changes)
            bridge = any(c["before"] < needed.get(c["skill_id"], 0) for c in changes)
            if not closes_gap and not bridge:
                return False
            for sid, level in dataset.events_by_id[step["event_id"]]["prerequisites"].items():
                needed[sid] = max(needed.get(sid, 0), level)
        return True

    def search(state, earliest, steps, score):
        nonlocal best_steps, best_score
        if steps and useful_route(steps):
            ids = tuple(s["event_id"] for s in steps)
            previous = routes_by_first.get(ids[0])
            if previous is None or (-score, ids) < (-previous["utility"], previous["ids"]):
                routes_by_first[ids[0]] = {"steps": steps, "utility": score, "ids": ids}
            best_ids = tuple(s["event_id"] for s in best_steps)
            if score > best_score or (score == best_score and ids < best_ids):
                best_steps, best_score = steps, score
        if len(steps) == 3:
            return
        used = {s["event_id"] for s in steps}
        initial = coverage(state, profile)
        for event, after, start, end, continuing in candidates(state, earliest, used):
            forecast = coverage(after, profile)
            gain = ((forecast["coverage"] or 0) - (initial["coverage"] or 0)) / 10
            utility = (2 * (initial["critical_gap_units"] - forecast["critical_gap_units"])
                       + gain - 2 * signals[event["event_id"]]["friction"]
                       - event["duration_hours"] / 40 + (.25 if continuing else 0))
            changes = [{
                "skill_id": sid, "name": dataset.skills_by_id[sid]["name"],
                "before": state.get(sid, 0), "after": level,
                "required": profile["required_skills"].get(sid, 0),
                "gap_closed": initial["gaps"].get(sid, 0) - forecast["gaps"].get(sid, 0),
            } for sid, level in after.items() if level > state.get(sid, 0)]
            step = {
                "step": len(steps) + 1, "event_id": event["event_id"], "title": event["title"],
                "action": "continue" if continuing else "start", "format": event["format"],
                "duration_hours": event["duration_hours"], "start_date": start.isoformat(),
                "estimated_end_date": end.isoformat(), "skill_changes": changes,
                "forecast": forecast, "utility": utility, "history": signals[event["event_id"]],
            }
            search(after, end, steps + [step], score + .7 ** len(steps) * utility)

    search(skills, dataset.as_of_date, [], 0)
    for step in best_steps:
        gains = "; ".join(f'{c["name"]}: {c["before"]} → {c["after"]} (target {c["required"]})'
                          for c in step["skill_changes"])
        purpose = ("closes target skill gaps" if any(c["gap_closed"] for c in step["skill_changes"])
                   else "builds prerequisites for a later step")
        step["explanation"] = f'This activity {purpose}. Expected skill changes: {gains}.'
    candidates_for_ai = sorted(routes_by_first.values(), key=lambda r: (-r["utility"], r["ids"]))[:5]
    return {
        "employee_id": employee_id, "employee_name": employee.get("full_name", employee_id),
        "current_role": employee["role"], "current_grade": employee["grade"],
        "target": target, "as_of_date": dataset.as_of_date.isoformat(),
        "planner_version": VERSION, "explanation_source": "local_rules",
        "before": before, "after": best_steps[-1]["forecast"] if best_steps else before,
        "steps": best_steps, "utility": best_score if best_steps else None,
        "_candidate_routes": [{"route_id": index, "steps": route["steps"], "utility": route["utility"]}
                              for index, route in enumerate(candidates_for_ai)],
        "status": "planned" if best_steps else "no_available_route",
        "limitation": (None if len(best_steps) == 3 else
                      "The catalog and scoring support fewer than three useful steps for this target."),
        "assumptions": [
            "Skill coverage is not a promotion probability and does not change the employee grade.",
            "Dates assume two study hours per calendar day; timing is a scenario, not a promise.",
            "Self-paced history uses assignment dates as an approximation; completion dates are unavailable.",
            "History friction is a soft signal, not a probability of completion.",
            "Three-step utility extends the two-step heuristic with a 0.7 discount per additional step.",
        ],
    }
