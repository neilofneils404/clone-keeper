"""Prune only history that undo, recovery and ancestry no longer need."""

import json
import unittest

from tests import test_cli
from tests.test_workflow import WorkflowFixture

CLONE = test_cli.CLONE_ID


class PruneTests(WorkflowFixture):
    def setUp(self):
        super().setUp()
        (self.root / "empty.log").write_text("")
        (self.root / "pkg").mkdir()

    def roots(self, *arguments):
        return ("--package-cache", self.root / "pkg", "--pacman-log", self.root / "empty.log", *arguments)

    def update(self, number):
        self.replace(self.source, f"property int one: {number}", f"property int one: {number + 1}")
        plan = self.prepare()
        self.apply(plan)

    def transactions(self):
        return [path.name for path in sorted((self.state / "transactions" / CLONE).iterdir())]

    def test_keeps_newest_transactions_and_their_undo(self):
        self.track()
        for number in range(1, 5):
            self.update(number)
        history = self.cli("history", CLONE)["transactions"]
        oldest = history[0]["id"]
        listed = self.cli("prune", "--keep-transactions", "2", readonly=True)
        self.assertTrue(listed["dryRun"])
        self.assertEqual([item["id"] for item in listed["items"]], [history[0]["id"], history[1]["id"]])
        self.assertEqual(len(self.transactions()), 4)
        removed = self.cli("prune", "--keep-transactions", "2", "--yes")
        self.assertEqual(len(removed["removed"]), 2)
        self.assertNotIn(oldest, self.transactions())
        self.assertFalse((self.state / ".staging" / oldest).exists())
        self.assertFalse((self.plugins.parent / ".clone-keeper-staging" / oldest).exists())
        self.cli("undo", CLONE)
        self.cli("undo", CLONE)
        self.assertIn("property int one: 3", (self.clone / "BarWidget.qml").read_text())
        self.cli("undo", CLONE, success=False)

    def test_unfinished_transaction_keeps_every_journal(self):
        self.track()
        for number in range(1, 4):
            self.update(number)
        newest = self.state / "transactions" / CLONE / self.transactions()[-1] / "record.json"
        record = json.loads(newest.read_text())
        record["status"] = "prepared"
        newest.write_text(json.dumps(record))
        self.assertEqual(self.cli("prune", "--keep-transactions", "1", readonly=True)["items"], [])

    def test_keeps_recent_releases_and_any_an_untracked_clone_needs(self):
        self.replace(self.clone, "Original greeting", "Local greeting")  # stays untracked
        for number in range(1, 6):
            (self.omarchy / "version").write_text(f"4.1.{number}\n")
            self.replace(self.source, f"property int one: {number}", f"property int one: {number + 1}")
            self.cli(*self.roots("sync"))
        self.assertEqual(self.cli("prune", "--keep-releases", "2", readonly=True)["items"], [])
        self.track_with_origin("4.1.5")
        listed = self.cli("prune", "--keep-releases", "2", readonly=True)["items"]
        self.assertEqual(sorted(item["versions"][0] for item in listed), ["4.1.1", "4.1.2", "4.1.3"])
        self.cli("prune", "--keep-releases", "2", "--yes")
        found = self.cli(*self.roots("origins", CLONE))
        self.assertEqual(sorted(version for item in found["candidates"] for version in item["versions"]),
                         ["4.1.4", "4.1.5"])

    def track_with_origin(self, version):
        found = self.cli(*self.roots("origins", CLONE))
        chosen = next(item for item in found["candidates"] if version in item["versions"])
        self.cli(*self.roots("track", CLONE, "--origin", version, "--expect", chosen["baselineHash"]))

    def test_refuses_to_keep_nothing(self):
        self.cli("prune", "--keep-transactions", "0", success=False, readonly=True)


if __name__ == "__main__":
    unittest.main()
