"""Reproduce two real starter-kit scenarios locally without API/model/DB writes."""

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.engine.data_loader import load_dataset
from backend.app.engine.navigator import employee_state, eligibility, hr_overview, recommendations
from backend.app.engine.planner import apply_effects, coverage, friction


def measure(function, repeat=5):
    durations = []
    result = None
    for _ in range(repeat):
        started = perf_counter()
        result = function()
        durations.append((perf_counter() - started) * 1000)
    return result, {"runs": repeat, "median_ms": round(median(durations), 3),
                    "max_ms": round(max(durations), 3)}


def scenario(dataset, employee_id):
    state, profile_timing = measure(lambda: employee_state(dataset, employee_id))
    response, recommendation_timing = measure(lambda: recommendations(dataset, employee_id))
    rows = response["recommendations"]
    employee = state["employee"]
    return {
        "employee_id": employee_id, "role": employee["role"], "grade": employee["grade"],
        "target": employee["target"], "effective_skills": employee["skills"],
        "coverage_before": state["trajectory"]["coverage"],
        "critical_gap_units_before": state["trajectory"]["critical_gap_units"],
        "routes": [{"route_id": row["route_id"], "first_event_id": row["event_id"],
                    "title": row["title"], "action": row["action"], "score": row["score"],
                    "coverage_after_first": row["progress_after"],
                    "critical_gap_units_after_first": row["critical_gap_after"],
                    "coverage_after_route": row["steps"][-1]["coverage_after"],
                    "impacts": row["impacts"], "steps": row["steps"],
                    "evidence": row["factors"]} for row in rows],
        "baseline": response["baseline"], "timings": {
            "profile": profile_timing, "recommendations": recommendation_timing},
    }, state, response


def build_report(dataset, simple_id="E0015", complex_id="E0137"):
    simple, before, first_response = scenario(dataset, simple_id)
    if not first_response["recommendations"]:
        raise ValueError("Simple demo profile has no step; choose --simple-id for this dataset/date")
    first = first_response["recommendations"][0]
    event = dataset.events_by_id[first["event_id"]]
    admission = eligibility(dataset, simple_id, event["event_id"])
    if event["format"] != "self_paced" or not admission["eligible"]:
        raise ValueError("Simple demo must start with an eligible self_paced activity")
    completion = {"event_id": event["event_id"], "completed_at": dataset.as_of_date.isoformat(),
                  "event_session_key": admission["event_session_key"],
                  "continued_record_id": admission["continued_record_id"]}
    after = employee_state(dataset, simple_id, completions=[completion], revision=1)
    repeated = employee_state(dataset, simple_id, completions=[completion, completion], revision=1)
    updated = recommendations(dataset, simple_id, completions=[completion], revision=1)
    if after["employee"]["skills"] != repeated["employee"]["skills"]:
        raise AssertionError("Duplicate completion applied a skill effect twice")
    if after["trajectory"]["coverage"] != first["progress_after"]:
        raise AssertionError("First-step forecast disagrees with completion")
    if after["employee"]["grade"] != before["employee"]["grade"]:
        raise AssertionError("Completion unexpectedly changed the employee's grade")
    simple["completion_check"] = {
        "event_id": event["event_id"], "coverage_after": after["trajectory"]["coverage"],
        "critical_gap_units_after": after["trajectory"]["critical_gap_units"],
        "effective_skills_after": after["employee"]["skills"], "grade_after": after["employee"]["grade"],
        "duplicate_changes_skills": False,
        "next_route_ids": [r["route_id"] for r in updated["recommendations"]],
    }

    complex_case, state, response = scenario(dataset, complex_id)
    first = response["recommendations"][0] if response["recommendations"] else None
    baseline = response["baseline"]
    if first is None or baseline is None or baseline["event_id"] == first["event_id"]:
        raise ValueError("Complex demo must differ from the lowest-skill baseline; choose --complex-id")
    naive_event = dataset.events_by_id[baseline["event_id"]]
    target = state["employee"]["target"]
    target_profile = dataset.role_profiles_by_key[(target["role"], target["grade"])]
    naive_after = coverage(apply_effects(state["employee"]["skills"], naive_event), target_profile)
    complex_case["baseline"].update(
        title=naive_event["title"], format=naive_event["format"],
        coverage_after=round(naive_after["coverage"], 2),
        critical_gap_units_after=naive_after["critical_gap_units"],
    )
    complex_case["first_format_history"] = friction(dataset.events_by_id[first["event_id"]],
        state["history"], dataset.events_by_id, dataset.as_of_date)["format_counts"]
    overview, overview_timing = measure(lambda: hr_overview(dataset), repeat=1)
    return {
        "as_of_date": dataset.as_of_date.isoformat(), "dataset_counts": dataset.counts,
        "model_called": False, "database_modified": False,
        "simple": simple, "complex": complex_case,
        "hr": {"total_employees": overview["total_employees"],
               "employees_without_step": len(overview["no_next_step"]),
               "activities": len(overview["activities"]), "timings": overview_timing},
        "timing_scope": "Direct local functions; API, browser and LLM latency are measured separately.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "backend" / "Dataset")
    parser.add_argument("--as-of-date", help="ISO date; omitted means dataset snapshot")
    parser.add_argument("--simple-id", default="E0015")
    parser.add_argument("--complex-id", default="E0137")
    parser.add_argument("--output", type=Path, help="Optional JSON report path, e.g. var/demo_report.json")
    args = parser.parse_args()
    dataset = load_dataset(args.data_dir, as_of_override=args.as_of_date)
    report = build_report(dataset, args.simple_id, args.complex_id)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Report saved: {args.output}")
        print(json.dumps({"as_of_date": report["as_of_date"],
                          "profiles": [args.simple_id, args.complex_id],
                          "completion_check": "passed", "model_called": False,
                          "hr_timing_ms": report["hr"]["timings"]["max_ms"]}))
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(rendered)


if __name__ == "__main__":
    main()
