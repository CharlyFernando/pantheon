"""The shared test environment: a temporary home and config, and real git repos."""

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

from pantheon import store

SCRIPT = os.path.join(ROOT, "pantheon.py")


def git(cwd, *args):
    subprocess.run(("git", *args), cwd=cwd, check=True, capture_output=True)


def make_repo(parent, name, remote=None):
    path = os.path.join(parent, name)
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "start")
    if remote:
        git(path, "remote", "add", "origin", remote)
    return path


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


class Env(unittest.TestCase):
    """A temporary Pantheon home, config and real git repos, driven through the command line."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.config = os.path.join(self.tmp.name, "config.toml")
        with open(self.config, "w", encoding="utf-8") as target:
            target.write('[forges]\n"code.example.org" = "gitlab"\n')
        self.env = {**os.environ, "PANTHEON_HOME": self.home, "PANTHEON_CONFIG": self.config}
        os.environ.update(PANTHEON_HOME=self.home, PANTHEON_CONFIG=self.config)
        self.alpha = make_repo(self.tmp.name, "alpha", "git@code.example.org:team/alpha.git")
        self.beta = make_repo(self.tmp.name, "beta", "https://github.com/me/beta.git")
        self.outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(self.outside)

    def tearDown(self):
        os.environ.pop("PANTHEON_HOME", None)
        os.environ.pop("PANTHEON_CONFIG", None)
        self.tmp.cleanup()

    def cli(self, cwd, *args, timeout=20):
        return subprocess.run(
            (sys.executable, "-I", SCRIPT, *args), cwd=cwd, capture_output=True, text=True, env=self.env, timeout=timeout
        )

    def ok(self, cwd, *args):
        result = self.cli(cwd, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def join(self, cwd, role, item=None, agent="claude", model=None):
        """Start a session and return its id; the model defaults to one per agent."""
        extra = (item,) if item else ()
        extra += ("--model", model or f"{agent}-model")
        out = self.ok(cwd, "join", *extra, "--role", role, "--agent", agent)
        return next(line.split("=", 1)[1] for line in out.splitlines() if line.startswith("export PANTHEON_SESSION="))
