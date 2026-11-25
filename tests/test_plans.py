"""Tests for step 4: plan steps, their dependencies and `ready`.

Run: python3 -I ~/.agents/skills/pantheon/tests/run.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env import ROOT, Env

from pantheon import plans, store

PLAN = """## Goal
Ship it.

## Decisions
1. Python only. Depende de: 9 (not a step: this is the Decisions section).

## Steps

1. **Core.** The board. Depende de: nada.
2. **Sessions.** Who acts. Depende de: 1.
3. **Docs.** Words.
4. **Launch.** Run the agents, wrapped over
   two lines. Depende de: 2 y 3.
"""


class StepRules(unittest.TestCase):
    def test_steps_and_needs_come_from_the_steps_section(self):
        steps = plans.parse_steps(PLAN)
        self.assertEqual(list(steps), [1, 2, 3, 4])
        self.assertEqual(steps[1], ("Core", ()))
        self.assertEqual(steps[4], ("Launch", (2, 3)))
        self.assertEqual(plans.format_steps(steps), "1 2<1 3 4<2,3")
        self.assertEqual(plans.read_steps("1 2<1 3 4<2,3"), {1: (), 2: (1,), 3: (), 4: (2, 3)})

    def test_english_markers_work_too(self):
        steps = plans.parse_steps("## Steps\n1. A.\n2. B. Depends on: 1\n3. C. Needs: 1, 2\n")
        self.assertEqual([needs for _, needs in steps.values()], [(), (1,), (1, 2)])

    def test_inconsistent_dependencies_are_refused(self):
        for text, message in (
            ("## Steps\n1. A. Depende de: 1\n", "itself"),
            ("## Steps\n1. A. Depende de: 7\n", "does not have"),
            ("## Steps\n1. A. Depende de: 2\n2. B. Depende de: 3\n3. C. Depende de: 1\n", "circle"),
            ("## Steps\n1. A.\n1. B.\n", "twice"),
        ):
            with self.assertRaises(ValueError) as caught:
                plans.parse_steps(text)
            self.assertIn(message, str(caught.exception))

    def test_the_pantheon_plan_parses(self):
        with open(os.path.join(ROOT, "PLAN.md"), encoding="utf-8") as source:
            steps = plans.parse_steps(source.read())
        self.assertEqual(len(steps), 8)
        self.assertEqual(steps[5][1], (2, 3, 4))

    def test_states_follow_the_items(self):
        plan = {"item": "P", "steps": "1 2<1 3 4<2,3"}
        done = {"plan": "P", "step": "1", "next": "human", "verdict": "PASSED", "reason": ""}
        active = {"plan": "P", "step": "3", "next": "reviewer", "verdict": ""}
        rows = plans.step_states(plan, [("S-1", done), ("S-3", active)])
        self.assertEqual(
            [(n, item, state, detail) for n, _, item, state, detail in rows],
            [(1, "S-1", "done", ""), (2, "", "ready", ""), (3, "S-3", "active", "next reviewer"), (4, "", "blocked", "needs 2,3")],
        )

    def test_an_escalated_or_mid_flow_approval_is_not_done(self):
        self.assertFalse(plans.finished({"next": "human", "verdict": "APPROVED", "reason": "escalated"}))
        self.assertFalse(plans.finished({"next": "qa", "verdict": "APPROVED", "reason": ""}))
        self.assertTrue(plans.finished({"next": "human", "verdict": "APPROVED", "reason": ""}))


class StepCli(Env):
    def approved_plan(self, text=PLAN):
        self.ok(self.alpha, "init", "P", "--kind", "plan")
        path = os.path.join(self.tmp.name, "plan.md")
        with open(path, "w", encoding="utf-8") as target:
            target.write(text)
        planner = self.join(self.alpha, "planner", "P")
        self.ok(self.alpha, "plan", "P", "--session", planner, "--file", path)
        self.ok(self.alpha, "post", "P", "--role", "human", "--next", "human", "--verdict", "APPROVED", "-m", "go")
        return path

    def finish(self, item):
        """Run an item's whole default flow to PASSED."""
        coder = self.join(self.alpha, "coder", item, model="m-coder")
        self.ok(self.alpha, "post", item, "--session", coder, "--commit", "abc", "-m", "done")
        reviewer = self.join(self.alpha, "reviewer", item, agent="gentle", model="m-review")
        self.ok(self.alpha, "post", item, "--session", reviewer, "--verdict", "APPROVED", "-m", "ok")
        qa = self.join(self.alpha, "qa", item, agent="opencode", model="m-qa")
        self.ok(self.alpha, "post", item, "--session", qa, "--verdict", "PASSED", "-m", "works")

    def test_writing_the_plan_stores_its_steps_and_refuses_bad_ones(self):
        path = self.approved_plan()
        self.assertEqual(store.read("alpha", "P")[0]["steps"], "1 2<1 3 4<2,3")
        with open(path, "w", encoding="utf-8") as target:
            target.write("## Steps\n1. A. Depende de: 2\n2. B. Depende de: 1\n")
        bad = self.cli(self.alpha, "plan", "P", "--role", "human", "--file", path)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("circle", bad.stderr)
        self.assertEqual(store.read("alpha", "P")[0]["plan_version"], "1", "a refused plan changes nothing")

    def test_a_step_opens_only_when_what_it_needs_is_done(self):
        self.approved_plan()
        self.assertIn("no step 9", self.cli(self.alpha, "init", "S-9", "--plan", "P", "--step", "9").stderr)
        blocked = self.cli(self.alpha, "init", "S-2", "--plan", "P", "--step", "2")
        self.assertIn("needs step 1 done first", blocked.stderr)
        self.ok(self.alpha, "init", "S-1", "--plan", "P", "--step", "1")
        self.assertIn("already has an item", self.cli(self.alpha, "init", "S-1b", "--plan", "P", "--step", "1").stderr)
        self.finish("S-1")
        self.ok(self.alpha, "init", "S-2", "--plan", "P", "--step", "2")
        self.assertEqual(store.read("alpha", "S-2")[0]["needs"], "1")
        self.ok(self.alpha, "init", "S-4", "--plan", "P", "--step", "4", "--ignore-needs")

    def test_ready_shows_each_step_state(self):
        self.approved_plan()
        self.ok(self.alpha, "init", "S-1", "--plan", "P", "--step", "1")
        self.ok(self.alpha, "init", "S-3", "--plan", "P", "--step", "3")
        self.finish("S-1")
        out = self.ok(self.alpha, "ready")
        self.assertIn("===== plan alpha/P v1 (approved) =====", out)
        lines = {line.split()[0]: line for line in out.splitlines()[2:]}
        self.assertIn("done", lines["1"])
        self.assertIn("ready", lines["2"])
        self.assertIn("active", lines["3"])
        self.assertIn("next coder", lines["3"])
        self.assertIn("blocked", lines["4"])
        self.assertIn("needs 2,3", lines["4"])
        self.assertIn("Launch", lines["4"])
        self.assertIn("alpha/P", self.ok(self.outside, "ready", "alpha/P"))
        self.assertEqual(self.cli(self.alpha, "ready", "S-1").returncode, 1)


if __name__ == "__main__":
    unittest.main()
