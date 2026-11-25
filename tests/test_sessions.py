"""Tests for step 2: sessions, role contracts, identity per turn and holds.

Run: python3 -I ~/.agents/skills/pantheon/tests/run.py
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env import Env

from pantheon import sessions, store


class Sessions(Env):
    def setUp(self):
        super().setUp()
        self.ok(self.alpha, "init", "A-1", "--title", "work")

    def age(self, session_id, seconds):
        """Make a session's heartbeat `seconds` old."""
        path = sessions.path_of(session_id)
        with open(path, encoding="utf-8") as source:
            record = json.load(source)
        moment = sessions.utcnow().timestamp() - seconds
        record["heartbeat"] = sessions.stamp(sessions.datetime.datetime.fromtimestamp(moment, sessions.datetime.timezone.utc))
        with open(path, "w", encoding="utf-8") as target:
            json.dump(record, target)

    def test_join_hands_the_contract_and_holds_the_item_whose_turn_it_is(self):
        out = self.ok(self.alpha, "join", "A-1", "--role", "coder", "--agent", "claude", "--model", "claude-opus-5-5")
        session_id = out.split()[1]
        self.assertIn("coder@claude · holding alpha/A-1", out)
        self.assertIn("# Rol: coder", out)
        self.assertEqual(store.read("alpha", "A-1")[0]["holder"], session_id)
        record = sessions.load(session_id)
        self.assertEqual((record["repo"], record["item"], record["model"]), ("alpha", "A-1", "claude-opus-5-5"))

        second = self.ok(self.alpha, "join", "A-1", "--role", "coder", "--agent", "gentle", "--model", "gentle-model")
        self.assertIn(f"held by session {session_id}", second)
        reviewer = self.ok(self.alpha, "join", "A-1", "--role", "reviewer", "--agent", "opencode", "--model", "opencode-model")
        self.assertIn("it is coder's turn", reviewer)
        self.assertIn("# Rol: reviewer", reviewer)

    def test_agents_post_through_a_session_and_the_post_names_them(self):
        refused = self.cli(self.alpha, "post", "A-1", "--role", "coder", "--next", "reviewer", "-m", "x")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("join first", refused.stderr)

        coder = self.join(self.alpha, "coder", "A-1", model="claude-opus-5-5")
        mismatch = self.cli(self.alpha, "post", "A-1", "--session", coder, "--role", "reviewer", "--next", "human", "-m", "x")
        self.assertIn("plays coder", mismatch.stderr)

        self.ok(self.alpha, "post", "A-1", "--session", coder, "--next", "reviewer", "--commit", "abc", "-m", "done")
        header, history = store.read("alpha", "A-1")
        self.assertIn(f"## turn 1 · coder@claude [{coder}] → reviewer", history)
        self.assertIn("- model: claude-opus-5-5", history)
        self.assertEqual(header["holder"], "", "a post releases the item")

        self.ok(self.alpha, "post", "A-1", "--role", "human", "--next", "coder", "-m", "the human needs no session")

    def test_the_session_can_come_from_the_environment(self):
        coder = self.join(self.alpha, "coder", "A-1")
        self.env["PANTHEON_SESSION"] = coder
        self.ok(self.alpha, "post", "A-1", "--next", "reviewer", "-m", "via env")
        self.assertIn(f"[{coder}]", store.read("alpha", "A-1")[1])

    def test_a_live_holder_blocks_others_and_a_stale_one_does_not(self):
        first = self.join(self.alpha, "coder", "A-1")
        second = self.join(self.alpha, "coder", agent="gentle")
        blocked = self.cli(self.alpha, "post", "A-1", "--session", second, "--next", "reviewer", "-m", "mine")
        self.assertEqual(blocked.returncode, 1)
        self.assertIn(f"held by session {first}", blocked.stderr)
        self.assertEqual(self.cli(self.alpha, "claim", "A-1", "--session", second).returncode, 1)

        self.age(first, 31 * 60)
        self.assertIn(f"{first} (stale)", self.ok(self.alpha, "list"))
        self.assertIn("stale", self.ok(self.alpha, "sessions"))
        self.ok(self.alpha, "claim", "A-1", "--session", second)
        self.assertEqual(store.read("alpha", "A-1")[0]["holder"], second)
        self.ok(self.alpha, "post", "A-1", "--session", second, "--next", "reviewer", "-m", "taken over")

    def test_the_lease_comes_from_the_config(self):
        with open(self.config, "a", encoding="utf-8") as target:
            target.write("[sessions]\nlease_minutes = 1\n")
        first = self.join(self.alpha, "coder", "A-1")
        self.age(first, 90)
        self.assertIn("(stale)", self.ok(self.alpha, "list"))

    def test_a_session_whose_process_died_is_stale(self):
        out = self.ok(self.alpha, "join", "A-1", "--role", "coder", "--agent", "claude", "--model", "claude-model", "--mode", "headless", "--pid", "999999999")
        self.assertIn("holding", out)
        self.assertIn("(stale)", self.ok(self.alpha, "list"))

    def test_inbox_skips_an_item_another_live_session_holds(self):
        coder = self.join(self.alpha, "coder", "A-1")
        self.ok(self.alpha, "post", "A-1", "--session", coder, "--next", "reviewer", "-m", "review it")
        first = self.join(self.alpha, "reviewer", "A-1", agent="gentle")
        second = self.join(self.alpha, "reviewer", agent="opencode")
        mine = self.ok(self.alpha, "inbox", "--session", first, "--timeout", "1")
        self.assertIn("===== alpha/A-1 =====", mine)
        theirs = self.cli(self.alpha, "inbox", "--session", second, "--timeout", "0.5", "--interval", "0.1")
        self.assertEqual(theirs.returncode, 2)

    def test_release_and_leave_free_the_item(self):
        coder = self.join(self.alpha, "coder", "A-1")
        self.ok(self.alpha, "release", "A-1", "--session", coder)
        self.assertEqual(store.read("alpha", "A-1")[0]["holder"], "")
        self.ok(self.alpha, "claim", "A-1", "--session", coder)
        self.assertIn("released alpha/A-1", self.ok(self.alpha, "leave", coder))
        self.assertEqual(store.read("alpha", "A-1")[0]["holder"], "")
        self.assertNotIn(coder, self.ok(self.alpha, "sessions"))
        self.assertIn("left", self.ok(self.alpha, "sessions", "--all"))
        gone = self.cli(self.alpha, "post", "A-1", "--session", coder, "--next", "reviewer", "-m", "x")
        self.assertIn("has left", gone.stderr)

    def test_heartbeat_keeps_a_session_alive(self):
        coder = self.join(self.alpha, "coder", "A-1")
        self.age(coder, 29 * 60)
        self.ok(self.alpha, "heartbeat", coder)
        self.assertTrue(sessions.alive(sessions.load(coder)))
        self.assertNotIn("(stale)", self.ok(self.alpha, "list"))

    def test_agents_are_the_known_ones_or_the_configured_ones(self):
        self.assertEqual(self.cli(self.alpha, "join", "--role", "coder", "--agent", "nobody", "--model", "nobody-model").returncode, 1)
        with open(self.config, "a", encoding="utf-8") as target:
            target.write("[agents.aider]\nenabled = true\n")
        self.ok(self.alpha, "join", "--role", "coder", "--agent", "aider", "--model", "aider-model")
        self.assertEqual(self.cli(self.alpha, "join", "--role", "human", "--agent", "claude", "--model", "claude-model").returncode, 2)

    def test_role_prints_each_contract(self):
        for role in ("coder", "reviewer", "human"):
            self.assertIn(f"# Rol: {role}", self.ok(self.alpha, "role", role))


if __name__ == "__main__":
    unittest.main()
