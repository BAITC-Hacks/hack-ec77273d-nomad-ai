from copy import deepcopy
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from backend.app.engine.planner import apply_effects, generate_plan
from backend.app.repository import Repository


def example_dataset():
    def event(eid, skill, prerequisite):
        return dict(event_id=eid, title=eid, mandatory=False, target_roles=["Engineer"],
                    target_grades=["Junior"], prerequisites=prerequisite,
                    develops_skills=[dict(skill_id=skill, gain=1, max_level=1)],
                    duration_hours=2, format="self_paced", upcoming_sessions=[])

    employee = dict(employee_id="E1", full_name="Example", role="Engineer", grade="Junior",
                    career_goal=dict(target_role="Engineer", target_grade="Middle"),
                    skills={}, last_review_date="2026-09-01")
    return SimpleNamespace(
        as_of_date=date(2026, 10, 1), employees_by_id={"E1": employee},
        events_by_id={e["event_id"]: e for e in [
            event("A", "foundation", {}), event("B", "intermediate", {"foundation": 1}),
            event("C", "target", {"intermediate": 1}),
        ]},
        skills_by_id={s: {"name": s} for s in ("foundation", "intermediate", "target")},
        role_profiles_by_key={("Engineer", "Middle"): {
            "required_skills": {"target": 1}, "critical_skills": ["target"]}},
        history_by_employee={},
    )


class PlannerTests(unittest.TestCase):
    def test_three_step_prerequisite_chain_without_mutating_source(self):
        data = example_dataset()
        original = deepcopy(data.employees_by_id)
        plan = generate_plan(data, "E1")
        self.assertEqual([s["event_id"] for s in plan["steps"]], ["A", "B", "C"])
        self.assertEqual(plan["after"]["coverage"], 100)
        self.assertEqual(data.employees_by_id, original)
        for first, second in zip(plan["steps"], plan["steps"][1:]):
            self.assertLessEqual(first["estimated_end_date"], second["start_date"])

    def test_closed_catalog_does_not_invent_route(self):
        data = example_dataset()
        data.events_by_id["C"]["target_roles"] = ["Other"]
        plan = generate_plan(data, "E1")
        self.assertEqual(plan["status"], "no_available_route")
        self.assertEqual(plan["steps"], [])

    def test_missing_sessions_and_mandatory_events_are_excluded(self):
        for change in ({"format": "offline"}, {"mandatory": True}):
            data = example_dataset()
            data.events_by_id["A"].update(change)
            self.assertFalse(generate_plan(data, "E1")["steps"])

    def test_completed_history_replayed_only_after_review(self):
        data = example_dataset()
        data.history_by_employee["E1"] = [
            dict(record_id="1", event_id="A", status="completed", date="2026-09-02"),
        ]
        self.assertEqual([s["event_id"] for s in generate_plan(data, "E1")["steps"]], ["B", "C"])
        data.history_by_employee["E1"][0]["date"] = "2026-09-01"
        self.assertFalse(generate_plan(data, "E1")["steps"])

    def test_effect_never_lowers_high_skill(self):
        event = example_dataset().events_by_id["A"]
        self.assertEqual(apply_effects({"foundation": 5}, event)["foundation"], 5)

    def test_saved_plan_survives_repository_reopen(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "plans.sqlite3"
            repository = Repository(path)
            repository.initialize()
            imported = repository.register_import(
                as_of_date="2026-10-01", manifest={"skills.json": "a" * 64},
                counts={"employees": 1},
            )
            plan = generate_plan(example_dataset(), "E1")
            saved = repository.save_plan(imported["import_id"], plan)
            reopened = Repository(path)
            reopened.initialize()
            self.assertEqual(reopened.get_plan(saved["plan_id"])["result"], plan)
            self.assertEqual(len(reopened.list_plans("E1", imported["import_id"])), 1)
            self.assertEqual(reopened.list_plans("E2", imported["import_id"]), [])
