"""Tests for step 3: flows, the six roles, QA verdicts and the other-model rule.

Run: python3 -I ~/.agents/skills/pantheon/tests/run.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env import Env

from pantheon import board, store


def post(state, role, text="x", wanted=None, **kw):
    header, history = state
    return board.apply_post(header, history, role, wanted, text, **kw)


class FlowRules(unittest.TestCase):
    def change(self, flow=None, max_rounds=3):
        return board.new_item("X", "r", "t", max_rounds, flow=flow, branch="b", worktree="/w"), ""

    def test_the_default_change_flow_goes_coder_reviewer_qa_human(self):
        state = self.change()
        self.assertEqual((state[0]["flow"], state[0]["next"]), ("coder,reviewer,qa", "coder"))
        state = post(state, "coder", model="claude-opus-5-5")
        self.assertEqual(state[0]["next"], "reviewer")
        state = post(state, "reviewer", verdict="APPROVED", model="deepseek-v4-pro")
        self.assertEqual(state[0]["next"], "qa")
        state = post(state, "qa", verdict="PASSED", model="gpt-5")
        self.assertEqual((state[0]["next"], state[0]["verdict"]), ("human", "PASSED"))

    def test_a_failed_qa_goes_back_to_the_coder_and_the_fix_is_reviewed_again(self):
        state = post(self.change(), "coder", model="m1")
        state = post(state, "reviewer", verdict="APPROVED", model="m2")
        state = post(state, "qa", verdict="FAILED", findings=["login"], model="m3")
        self.assertEqual((state[0]["next"], state[0]["round"]), ("coder", "1"))
        state = post(state, "coder", model="m1")
        self.assertEqual((state[0]["next"], state[0]["round"]), ("reviewer", "2"))

    def test_needs_decision_goes_to_the_human_and_so_does_the_round_limit(self):
        state = post(self.change(), "coder", model="m1")
        self.assertEqual(post(state, "reviewer", verdict="NEEDS_DECISION", model="m2")[0]["next"], "human")
        state = post(self.change(max_rounds=1), "coder", model="m1")
        state = post(state, "reviewer", verdict="APPROVED", model="m2")
        state = post(state, "qa", verdict="FAILED", findings=["a"], model="m3")
        self.assertEqual((state[0]["next"], state[0]["reason"]), ("human", "round limit reached"))

    def test_agents_follow_the_flow_or_escalate_and_the_human_redirects(self):
        state = self.change()
        with self.assertRaises(ValueError):
            post(state, "coder", wanted="qa")
        escalated = post(state, "coder", wanted="human")
        self.assertEqual((escalated[0]["next"], escalated[0]["reason"]), ("human", "escalated"))
        self.assertIn("handed to the human: escalated", escalated[1])
        self.assertEqual(post(state, "human", wanted="qa")[0]["next"], "qa")
        self.assertEqual(post(state, "human")[0]["next"], "coder", "an answer with no --next leaves the turn in place")
        self.assertEqual(post(state, "human", verdict="CHANGES_REQUESTED")[0]["next"], "coder")

    def test_each_role_posts_its_own_verdicts(self):
        state = post(self.change(), "coder", model="m1")
        with self.assertRaises(ValueError):
            post(state, "reviewer", verdict="PASSED", model="m2")
        with self.assertRaises(ValueError):
            post(self.change(), "coder", verdict="APPROVED")
        state = post(state, "reviewer", verdict="APPROVED", model="m2")
        with self.assertRaises(ValueError):
            post(state, "qa", verdict="APPROVED", model="m3")

    def test_custom_flows_and_their_validation(self):
        state = self.change("coder,reviewer")
        state = post(post(state, "coder", model="m1"), "reviewer", verdict="APPROVED", model="m2")
        self.assertEqual(state[0]["next"], "human")
        state = post(self.change("coder,qa"), "coder", model="m1")
        self.assertEqual(state[0]["next"], "qa")
        for bad in ("reviewer,coder", "coder,coder", "coder,human", "coder,planner", "coder,orchestrator"):
            with self.assertRaises(ValueError, msg=bad):
                board.parse_flow(bad)

    def test_reviewer_and_qa_run_another_model_than_the_coder(self):
        self.assertTrue(board.same_model("deepseek/deepseek-v4-pro:high", "DeepSeek-V4-Pro"))
        self.assertFalse(board.same_model("", ""))
        state = post(self.change(), "coder", model="claude-opus-5-5")
        self.assertEqual(state[0]["coder_model"], "claude-opus-5-5")
        with self.assertRaises(PermissionError):
            post(state, "reviewer", verdict="APPROVED", model="anthropic/claude-opus-5-5")
        state = post(state, "reviewer", verdict="APPROVED", model="claude-sonnet-5-5")
        with self.assertRaises(PermissionError):
            post(state, "qa", verdict="PASSED", model="claude-opus-5-5")

    def test_the_planner_writes_the_plan_and_a_coder_reopening_it_hands_it_to_the_human(self):
        plan = board.new_plan("P", "r", "", 3)
        self.assertEqual((plan["flow"], plan["next"]), ("planner,reviewer", "planner"))
        with self.assertRaises(PermissionError):
            board.apply_plan(plan, "coder")
        plan = board.apply_plan(plan, "planner")
        state = post((plan, ""), "planner", model="m1")
        state = post(state, "reviewer", verdict="APPROVED", model="m2")
        self.assertEqual(state[0]["next"], "human")
        state = post(state, "human", wanted="human", verdict="APPROVED")
        self.assertTrue(board.plan_approved(state[0]))
        reopened = board.apply_plan(state[0], "coder")
        self.assertEqual(reopened["next"], "coder")
        self.assertEqual(post((reopened, state[1]), "coder", model="m1")[0]["next"], "human")

    def test_a_note_takes_no_turn(self):
        state = post(self.change(), "coder", model="m1")
        header, history = board.apply_note(state[0], state[1], "orchestrator", "stuck?", agent="claude", session_id="s-1")
        self.assertEqual((header["turn"], header["next"]), (state[0]["turn"], state[0]["next"]))
        self.assertIn("## note · orchestrator@claude [s-1]", history)
        self.assertIn("stuck?", board.last_post(history))


class FlowCli(Env):
    def test_a_full_cycle_with_three_agents(self):
        self.ok(self.alpha, "init", "A-1")
        coder = self.join(self.alpha, "coder", "A-1", model="claude-opus-5-5")
        self.ok(self.alpha, "post", "A-1", "--session", coder, "--commit", "abc", "-m", "done")
        same = self.ok(self.alpha, "join", "A-1", "--role", "reviewer", "--agent", "claude", "--model", "claude-opus-5-5")
        self.assertIn("runs another model", same)
        same_id = same.split()[1]
        idle = self.cli(self.alpha, "inbox", "--session", same_id, "--timeout", "0.5", "--interval", "0.1")
        self.assertEqual(idle.returncode, 2, "a same-model reviewer is never offered the item")

        reviewer = self.join(self.alpha, "reviewer", "A-1", agent="gentle", model="deepseek-v4-pro")
        self.ok(self.alpha, "post", "A-1", "--session", reviewer, "--verdict", "APPROVED", "-m", "clean")
        qa = self.join(self.alpha, "qa", "A-1", agent="opencode", model="gpt-5")
        self.ok(self.alpha, "post", "A-1", "--session", qa, "--verdict", "PASSED", "-m", "works")
        header, history = store.read("alpha", "A-1")
        self.assertEqual((header["next"], header["verdict"], header["coder_model"]), ("human", "PASSED", "claude-opus-5-5"))
        self.assertIn(f"qa@opencode [{qa}] → human", history)

    def test_init_takes_a_flow_and_refuses_a_bad_one(self):
        self.ok(self.alpha, "init", "A-1", "--flow", "coder,reviewer")
        self.assertEqual(store.read("alpha", "A-1")[0]["flow"], "coder,reviewer")
        self.assertEqual(self.cli(self.alpha, "init", "A-2", "--flow", "reviewer").returncode, 1)
        self.ok(self.alpha, "init", "P", "--kind", "plan")
        self.assertEqual(store.read("alpha", "P")[0]["next"], "planner")

    def test_a_wrong_next_is_refused_and_join_needs_a_model(self):
        self.ok(self.alpha, "init", "A-1")
        coder = self.join(self.alpha, "coder", "A-1")
        wrong = self.cli(self.alpha, "post", "A-1", "--session", coder, "--next", "qa", "-m", "x")
        self.assertIn("only the human redirects", wrong.stderr)
        self.assertEqual(self.cli(self.alpha, "join", "--role", "qa", "--agent", "claude").returncode, 2)

    def test_the_orchestrator_leaves_notes_without_taking_the_turn(self):
        self.ok(self.alpha, "init", "A-1")
        orchestrator = self.join(self.alpha, "orchestrator")
        self.ok(self.alpha, "note", "A-1", "--session", orchestrator, "-m", "DECISIÓN: ¿qa o no?")
        header, history = store.read("alpha", "A-1")
        self.assertEqual((header["turn"], header["next"]), ("0", "coder"))
        self.assertIn(f"## note · orchestrator@claude [{orchestrator}]", history)
        self.ok(self.alpha, "note", "A-1", "--role", "human", "-m", "sí, con qa")
        self.assertEqual(self.cli(self.alpha, "post", "A-1", "--session", orchestrator, "-m", "x").returncode, 1)

    def test_every_role_has_a_contract(self):
        for role in board.ROLES:
            self.assertIn(f"# Rol: {role}", self.ok(self.alpha, "role", role))


if __name__ == "__main__":
    unittest.main()
