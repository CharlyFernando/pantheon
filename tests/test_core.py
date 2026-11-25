"""Tests for step 1: the per-repo board, turns, rounds and plans.

Run: python3 -I ~/.agents/skills/pantheon/tests/run.py
"""

import os
import subprocess
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pantheon import board, store

from env import SCRIPT, Env, git, make_repo


class Rules(unittest.TestCase):
    """The turn rules, without files."""

    def item(self, **extra):
        return board.new_item("X", "r", "t", 3, branch="b", worktree="/w", **extra), "\n# X\n"

    def test_only_the_named_role_posts_and_the_human_always_may(self):
        header, history = self.item()
        with self.assertRaises(PermissionError):
            board.apply_post(header, history, "reviewer", "coder", "no")
        header, history = board.apply_post(header, history, "coder", "reviewer", "done", commit="abc")
        self.assertEqual((header["turn"], header["next"], header["commit"]), ("1", "reviewer", "abc"))
        self.assertIn("## turn 1 · coder → reviewer", history)
        header, _ = board.apply_post(header, history, "human", "coder", "stop")
        self.assertEqual(header["next"], "coder")

    def test_a_coder_answer_to_requested_changes_opens_a_new_round(self):
        header, history = self.item()
        header, history = board.apply_post(header, history, "coder", "reviewer", "c")
        header, history = board.apply_post(
            header, history, "reviewer", "coder", "r", verdict="CHANGES_REQUESTED", findings=["a"]
        )
        self.assertEqual(header["round"], "1")
        header, history = board.apply_post(header, history, "coder", "reviewer", "fixed")
        self.assertEqual(header["round"], "2")

    def test_the_round_limit_and_repeated_findings_hand_the_turn_to_the_human(self):
        header, history = self.item()
        header["max_rounds"] = "1"
        header, history = board.apply_post(header, history, "coder", "reviewer", "c")
        header, _ = board.apply_post(
            header, history, "reviewer", "coder", "r", verdict="CHANGES_REQUESTED", findings=["a"]
        )
        self.assertEqual((header["next"], header["reason"]), ("human", "round limit reached"))

        header, history = self.item()
        for _ in range(2):
            header, history = board.apply_post(header, history, "coder", "reviewer", "c")
            header, history = board.apply_post(
                header, history, "reviewer", "coder", "r", verdict="CHANGES_REQUESTED", findings=["b", "a"]
            )
        self.assertEqual(header["next"], "human")
        self.assertIn("repeats", header["reason"])

    def test_only_the_human_approves_a_plan_and_only_once_it_has_text(self):
        header = board.new_plan("P", "r", "", 3)
        with self.assertRaises(ValueError):
            board.apply_post(header, "", "human", "coder", "ok", verdict="APPROVED")
        header = board.apply_plan(header, "coder")
        header, history = board.apply_post(header, "", "coder", "reviewer", "plan v1")
        header, history = board.apply_post(header, history, "reviewer", "human", "good", verdict="APPROVED")
        self.assertFalse(board.plan_approved(header))
        header, _ = board.apply_post(header, history, "human", "coder", "yes", verdict="APPROVED")
        self.assertTrue(board.plan_approved(header))
        self.assertEqual(header["next"], "human")

    def test_revising_an_approved_plan_reopens_it(self):
        header = board.apply_plan(board.new_plan("P", "r", "", 3), "coder")
        header, _ = board.apply_post(header, "", "human", "human", "yes", verdict="APPROVED")
        header = board.apply_plan(header, "coder")
        self.assertEqual((header["plan_version"], header["next"]), ("2", "coder"))
        self.assertFalse(board.plan_approved(header))

    def test_every_post_stamps_updated(self):
        header, history = self.item()
        header["updated"] = "old"
        header, _ = board.apply_post(header, history, "coder", "reviewer", "c")
        self.assertNotEqual(header["updated"], "old")


class Cli(Env):
    """The command line against a temporary home and real git repos."""

    def test_init_fills_the_metadata_from_the_repo(self):
        self.assertEqual(self.ok(self.alpha, "init", "A-1", "--title", "Fix it", "--mr", "7").strip(), "alpha/A-1")
        header, _ = store.read("alpha", "A-1")
        self.assertEqual(header["repo"], "alpha")
        self.assertEqual(header["branch"], "main")
        self.assertEqual(header["worktree"], os.path.realpath(self.alpha))
        self.assertEqual((header["forge"], header["mr"], header["title"]), ("gitlab", "7", "Fix it"))
        self.assertTrue(header["created"] and header["updated"])
        self.assertEqual(self.cli(self.alpha, "init", "A-1").returncode, 1)
        self.ok(self.beta, "init", "B-1")
        self.assertEqual(store.read("beta", "B-1")[0]["forge"], "github")

    def test_every_worktree_of_a_repo_shares_its_board(self):
        worktree = os.path.join(self.tmp.name, "alpha-wt")
        git(self.alpha, "worktree", "add", "-q", "-b", "side", worktree)
        self.ok(worktree, "init", "A-2")
        header, _ = store.read("alpha", "A-2")
        self.assertEqual((header["branch"], header["worktree"]), ("side", os.path.realpath(worktree)))
        self.assertIn("A-2", self.ok(self.alpha, "list"))

    def test_a_second_repo_with_the_same_name_is_refused(self):
        elsewhere = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(elsewhere)
        other = make_repo(elsewhere, "alpha")
        self.ok(self.alpha, "init", "A-1")
        clash = self.cli(other, "init", "A-9")
        self.assertEqual(clash.returncode, 1)
        self.assertIn("--repo", clash.stderr)
        self.ok(other, "init", "A-9", "--repo", "alpha2")
        self.assertEqual(store.read("alpha2", "A-9")[0]["repo"], "alpha2")

    def test_items_are_named_by_repo_from_anywhere(self):
        self.ok(self.alpha, "init", "SAME")
        self.ok(self.beta, "init", "SAME")
        self.ok(self.alpha, "init", "ONLY-A")
        self.assertIn("item: ONLY-A", self.ok(self.outside, "show", "ONLY-A"))
        ambiguous = self.cli(self.outside, "show", "SAME")
        self.assertEqual(ambiguous.returncode, 1)
        self.assertIn("repo: beta",self.ok(self.outside, "show", "beta/SAME"))
        self.ok(self.outside, "post", "alpha/SAME", "--session", self.join(self.outside, "coder"), "--next", "reviewer", "-m", "x")
        self.assertEqual(store.read("alpha", "SAME")[0]["next"], "reviewer")
        self.assertEqual(store.read("beta", "SAME")[0]["next"], "coder")

    def test_list_is_the_current_repo_inside_git_and_every_repo_outside(self):
        self.ok(self.alpha, "init", "A-1", "--mr", "4")
        self.ok(self.beta, "init", "B-1", "--mr", "5")
        inside = self.ok(self.alpha, "list")
        self.assertIn("alpha/A-1", inside)
        self.assertNotIn("beta/B-1", inside)
        self.assertIn("!4", inside)
        everywhere = self.ok(self.alpha, "list", "--all")
        self.assertIn("beta/B-1", everywhere)
        self.assertIn("#5", everywhere)
        self.assertIn("beta/B-1", self.ok(self.outside, "list"))
        self.assertIn("alpha", self.ok(self.outside, "repos"))

    def test_concurrent_posts_never_lose_a_turn(self):
        self.ok(self.alpha, "init", "A-1")
        errors = []

        def human(index):
            result = self.cli(self.alpha, "post", "A-1", "--role", "human", "--next", "human", "-m", f"post {index}")
            if result.returncode:
                errors.append(result.stderr)

        threads = [threading.Thread(target=human, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        header, history = store.read("alpha", "A-1")
        self.assertEqual(header["turn"], "8")
        self.assertEqual(history.count("## turn "), 8)

    def test_wait_wakes_on_a_later_post(self):
        self.ok(self.alpha, "init", "A-1")
        waiter = subprocess.Popen(
            (sys.executable, "-I", SCRIPT, "wait", "A-1", "--role", "reviewer", "--after-turn", "0", "--interval", "0.1"),
            cwd=self.alpha, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(0.4)
        self.assertIsNone(waiter.poll())
        self.ok(self.alpha, "post", "A-1", "--session", self.join(self.alpha, "coder"), "--next", "reviewer", "--commit", "abc", "-m", "please")
        out, err = waiter.communicate(timeout=10)
        self.assertEqual(waiter.returncode, 0, err)
        self.assertIn("please", out)
        self.assertIn("commit: abc", out)

    def test_inbox_outside_git_wakes_on_any_repo_and_times_out_otherwise(self):
        timeout = self.cli(self.outside, "inbox", "--role", "reviewer", "--timeout", "0.3", "--interval", "0.1")
        self.assertEqual(timeout.returncode, 2)
        self.ok(self.beta, "init", "B-1")
        self.ok(self.beta, "post", "B-1", "--session", self.join(self.beta, "coder"), "--next", "reviewer", "-m", "look")
        out = self.ok(self.outside, "inbox", "--role", "reviewer", "--timeout", "2")
        self.assertIn("===== beta/B-1 =====", out)
        alpha_only = self.cli(self.alpha, "inbox", "--role", "reviewer", "--timeout", "0.3", "--interval", "0.1")
        self.assertEqual(alpha_only.returncode, 2)

    def test_inbox_handed_to_wakes_on_a_new_hand_off(self):
        self.ok(self.alpha, "init", "A-1")
        self.ok(self.alpha, "post", "A-1", "--session", self.join(self.alpha, "coder"), "--next", "reviewer", "-m", "c")
        waiter = subprocess.Popen(
            (sys.executable, "-I", SCRIPT, "inbox", "--role", "coder", "--handed-to", "human", "--interval", "0.1"),
            cwd=self.alpha, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(0.4)
        self.ok(self.alpha, "post", "A-1", "--session", self.join(self.alpha, "reviewer"), "--next", "human", "--verdict", "APPROVED", "-m", "ok")
        out, err = waiter.communicate(timeout=10)
        self.assertEqual(waiter.returncode, 0, err)
        self.assertIn("verdict: APPROVED", out)

    def test_a_step_needs_an_approved_plan_and_prints_it(self):
        refused = self.cli(self.alpha, "init", "S-1", "--plan", "P", "--step", "1")
        self.assertEqual(refused.returncode, 1)
        self.ok(self.alpha, "init", "P", "--kind", "plan", "--title", "Big change")
        plan_file = os.path.join(self.tmp.name, "plan.md")
        with open(plan_file, "w", encoding="utf-8") as target:
            target.write("## Goal\nG\n## Steps\n1. one\n2. two\n")
        self.ok(self.alpha, "plan", "P", "--session", self.join(self.alpha, "coder"), "--file", plan_file)
        self.assertEqual(self.cli(self.alpha, "init", "S-1", "--plan", "P", "--step", "1").returncode, 1)
        self.ok(self.alpha, "post", "P", "--role", "human", "--next", "human", "--verdict", "APPROVED", "-m", "go")
        self.assertEqual(self.cli(self.alpha, "init", "S-0", "--plan", "P", "--step", "0").returncode, 1)
        self.ok(self.alpha, "init", "S-1", "--plan", "P", "--step", "1")
        shown = self.ok(self.alpha, "show", "S-1")
        self.assertIn("plan alpha/P v1 (approved) · this item is step 1", shown)
        self.assertIn("2. two", shown)
        self.assertIn("v1 approved", self.ok(self.alpha, "list"))
        self.assertIn("P#1", self.ok(self.alpha, "list"))
        self.assertFalse(any(name.endswith(".plan") for _, name, _, _ in store.items(["alpha"])))

    def test_unreadable_files_are_ignored(self):
        self.ok(self.alpha, "init", "A-1")
        directory = store.repo_dir("alpha")
        for name in ("broken.md", ".hidden.md", "A-1.md.123.tmp"):
            with open(os.path.join(directory, name), "w", encoding="utf-8") as target:
                target.write("not a board")
        self.assertEqual([name for _, name, _, _ in store.items(["alpha"])], ["A-1"])

    def test_bad_names_are_refused(self):
        for name in ("../x", ".x", "a/b/c"):
            self.assertEqual(self.cli(self.alpha, "init", name).returncode, 1, name)


if __name__ == "__main__":
    unittest.main()
