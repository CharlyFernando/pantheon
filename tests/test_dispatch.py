"""Tests for step 5: cast, approval, worktrees and launching agents headless.

Run: python3 -I ~/.agents/skills/pantheon/tests/run.py
"""

import os
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env import Env

from pantheon import agents, sessions, store

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_agent.py")


class CastRules(unittest.TestCase):
    def test_cast_round_trips_and_validates(self):
        cast = agents.parse_cast(["coder=claude:claude-opus-5-5", "reviewer=gentle"])
        self.assertEqual(cast, {"coder": ("claude", "claude-opus-5-5"), "reviewer": ("gentle", "")})
        self.assertEqual(agents.parse_cast(agents.format_cast(cast)), cast)
        with self.assertRaises(ValueError):
            agents.parse_cast(["coder"])

    def test_argv_fills_placeholders_and_drops_an_empty_model_flag(self):
        template = ["x", "--session-id", "{session}", "--model", "{model}", "{prompt}"]
        self.assertEqual(agents.argv(template, "do it", "u1", "m1"), ["x", "--session-id", "u1", "--model", "m1", "do it"])
        self.assertEqual(agents.argv(template, "do {it}", "u1", ""), ["x", "--session-id", "u1", "do {it}"])


class Dispatch(Env):
    def setUp(self):
        super().setUp()
        self.record = os.path.join(self.tmp.name, "record.txt")
        self.env["FAKE_RECORD"] = self.record
        fake = f'["{sys.executable}", "-I", "{FAKE}"'
        with open(self.config, "a", encoding="utf-8") as target:
            target.write(
                f"""
[worktrees]
path = "{self.tmp.name}/wt/{{repo}}/{{item}}"

[agents.fake]
launch = {fake}, "launch", "{{session}}", "--model", "{{model}}", "{{prompt}}"]
resume = {fake}, "resume", "{{session}}", "--model", "{{model}}", "{{prompt}}"]

[agents.fake2]
launch = {fake}, "launch", "{{session}}", "--model", "{{model}}", "{{prompt}}"]
resume = {fake}, "resume", "{{session}}", "--model", "{{model}}", "{{prompt}}"]

[agents.oneshot]
launch = {fake}, "launch", "{{session}}", "--model", "{{model}}", "{{prompt}}"]
resume = []

[agents.ide]
headless = false

[agents.off]
enabled = false
launch = ["true"]
"""
            )
        self.ok(self.alpha, "init", "A-1", "--new-worktree")
        self.worktree = store.read("alpha", "A-1")[0]["worktree"]

    def settle(self, item="A-1", turn=None, timeout=15):
        """Wait until the launched agent posted (the turn moved past `turn`)."""
        deadline = time.monotonic() + timeout
        start = int(store.read("alpha", item)[0]["turn"]) if turn is None else turn
        while time.monotonic() < deadline:
            header, _ = store.read("alpha", item)
            if int(header["turn"]) > start and not sessions.holder_alive(header):
                return header
            time.sleep(0.1)
        log = "".join(open(os.path.join(self.home, "logs", f), encoding="utf-8").read() for f in os.listdir(os.path.join(self.home, "logs")))
        self.fail(f"the agent did not post in time:\n{log}")

    def records(self):
        with open(self.record, encoding="utf-8") as source:
            return [line.split() for line in source]

    def test_new_worktree_follows_the_convention(self):
        self.assertEqual(self.worktree, os.path.join(self.tmp.name, "wt", "alpha", "A-1"))
        self.assertTrue(os.path.isdir(self.worktree))
        self.assertEqual(store.read("alpha", "A-1")[0]["branch"], "pantheon/a-1")
        again = self.cli(self.alpha, "init", "A-1b", "--new-worktree", "--branch", "pantheon/a-1")
        self.assertEqual(again.returncode, 1)

    def test_nothing_launches_before_the_human_approves(self):
        refused = self.cli(self.alpha, "dispatch", "A-1")
        self.assertIn("is not approved", refused.stderr)
        self.assertFalse(os.path.exists(self.record))

    def test_approve_completes_and_checks_the_cast(self):
        missing = self.cli(self.alpha, "approve", "A-1", "--cast", "coder=fake:m1")
        self.assertIn("no agent for reviewer", missing.stderr)
        same = self.cli(self.alpha, "approve", "A-1", "--cast", "coder=fake:m1", "--cast", "reviewer=fake2:m1", "--cast", "qa=fake2:m3")
        self.assertIn("runs another model", same.stderr)
        self.assertIn("disabled", self.cli(self.alpha, "approve", "A-1", "--cast", "coder=off", "--cast", "reviewer=fake:m2", "--cast", "qa=fake:m3").stderr)
        self.assertIn("has no planner", self.cli(self.alpha, "approve", "A-1", "--cast", "planner=fake").stderr)
        with open(self.config, "a", encoding="utf-8") as target:
            target.write('[defaults.cast]\ncoder = "fake"\n')
        self.ok(self.alpha, "approve", "A-1", "--cast", "reviewer=fake2:m2", "--cast", "qa=fake2:m3")
        header, history = store.read("alpha", "A-1")
        self.assertEqual(header["cast"], "coder=fake reviewer=fake2:m2 qa=fake2:m3")
        self.assertTrue(header["approved"])
        self.assertIn("Approved for dispatch", history)

    def test_an_agent_session_cannot_approve(self):
        coder = self.join(self.alpha, "coder", "A-1")
        self.env["PANTHEON_SESSION"] = coder
        refused = self.cli(self.alpha, "approve", "A-1", "--cast", "coder=fake:m1", "--cast", "reviewer=fake2:m2", "--cast", "qa=fake2:m3")
        self.assertIn("is the human's", refused.stderr)

    def test_a_full_round_trip_resumes_each_agent_conversation(self):
        self.env["FAKE_REVIEWER"] = "CHANGES_REQUESTED,APPROVED"
        self.ok(self.alpha, "approve", "A-1", "--cast", "coder=fake:m1", "--cast", "reviewer=fake2:m2", "--cast", "qa=fake2:m3", "--dispatch")
        self.assertEqual(self.settle(turn=0)["next"], "reviewer")
        self.assertIn("launched reviewer@fake2", self.ok(self.alpha, "dispatch", "A-1"))
        self.assertEqual(self.settle(turn=1)["next"], "coder")
        self.assertIn("resumed coder@fake", self.ok(self.alpha, "dispatch", "A-1"))
        self.assertEqual(self.settle(turn=2)["round"], "2")
        self.assertIn("resumed reviewer@fake2", self.ok(self.alpha, "dispatch", "A-1"))
        self.assertEqual(self.settle(turn=3)["next"], "qa")
        self.ok(self.alpha, "dispatch", "A-1")
        header = self.settle(turn=4)
        self.assertEqual((header["next"], header["verdict"]), ("human", "PASSED"))
        self.assertIn("waits on the human", self.cli(self.alpha, "dispatch", "A-1").stderr)

        runs = self.records()
        self.assertEqual([r[0] for r in runs], ["launch", "launch", "resume", "resume", "launch"])
        self.assertEqual(runs[0][1], runs[2][1], "the coder's second round continues its conversation")
        self.assertEqual(runs[1][1], runs[3][1])
        self.assertEqual([r[2] for r in runs], ["m1", "m2", "m1", "m2", "m3"])
        self.assertTrue(all(r[4] == self.worktree for r in runs), "agents run in the item's worktree")
        history = store.read("alpha", "A-1")[1]
        self.assertIn("coder@fake [", history)
        self.assertIn("qa@fake2 [", history)
        self.assertEqual(len([s for s in sessions.every() if s["item"] == "A-1"]), 3)

    def test_an_agent_that_cannot_resume_starts_a_new_conversation(self):
        self.env["FAKE_REVIEWER"] = "CHANGES_REQUESTED,APPROVED"
        self.ok(self.alpha, "approve", "A-1", "--cast", "coder=oneshot:m1", "--cast", "reviewer=fake2:m2", "--cast", "qa=fake2:m3", "--dispatch")
        self.settle(turn=0)
        self.ok(self.alpha, "dispatch", "A-1")
        self.settle(turn=1)
        self.assertIn("launched coder@oneshot", self.ok(self.alpha, "dispatch", "A-1"))
        self.settle(turn=2)
        coder_runs = [r for r in self.records() if r[3] == "coder"]
        self.assertNotEqual(coder_runs[0][1], coder_runs[1][1])

    def test_dispatch_refuses_a_held_or_revoked_item(self):
        self.ok(self.alpha, "approve", "A-1", "--cast", "coder=fake:m1", "--cast", "reviewer=fake2:m2", "--cast", "qa=fake2:m3")
        holder = self.join(self.alpha, "coder", "A-1", model="m1")
        self.assertIn(f"held by session {holder}", self.cli(self.alpha, "dispatch", "A-1").stderr)
        self.ok(self.alpha, "leave", holder)
        self.ok(self.alpha, "revoke", "A-1")
        self.assertIn("is not approved", self.cli(self.alpha, "dispatch", "A-1").stderr)

    def test_dry_run_and_manual_agents_launch_nothing(self):
        self.ok(self.alpha, "approve", "A-1", "--cast", "coder=ide:gemini-3", "--cast", "reviewer=fake2:m2", "--cast", "qa=fake2:m3")
        manual = self.cli(self.alpha, "dispatch", "A-1")
        self.assertEqual(manual.returncode, 3)
        self.assertIn("join alpha/A-1 --role coder --agent ide --model gemini-3", manual.stdout)
        self.ok(self.alpha, "approve", "A-1", "--cast", "coder=fake:m1", "--cast", "reviewer=fake2:m2", "--cast", "qa=fake2:m3")
        dry = self.ok(self.alpha, "dispatch", "A-1", "--dry-run")
        self.assertIn("would run in", dry)
        self.assertIn("--model m1", dry)
        self.assertEqual(sessions.every(), [])
        self.assertFalse(os.path.exists(self.record))

    def test_a_missing_binary_frees_the_item(self):
        with open(self.config, "a", encoding="utf-8") as target:
            target.write('[agents.ghost]\nlaunch = ["/nonexistent/agent", "{prompt}"]\n')
        self.ok(self.alpha, "approve", "A-1", "--cast", "coder=ghost:m1", "--cast", "reviewer=fake2:m2", "--cast", "qa=fake2:m3")
        failed = self.cli(self.alpha, "dispatch", "A-1")
        self.assertIn("could not start ghost", failed.stderr)
        self.assertEqual(store.read("alpha", "A-1")[0]["holder"], "")


if __name__ == "__main__":
    unittest.main()
