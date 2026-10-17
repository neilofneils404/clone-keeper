"""Tell the owner about upstream updates: dry-merge status and one-time notifications."""

import json
import unittest

from tests import test_cli
from tests.test_workflow import WorkflowFixture


class UpdateNoticeTests(WorkflowFixture):
    def setUp(self):
        super().setUp()
        stub = self.fake_bin / "omarchy-notification-send"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$0.log"\nexit "${NOTIFY_EXIT:-0}"\n')
        stub.chmod(0o755)
        self.log = stub.with_name("omarchy-notification-send.log")
        (self.root / "empty.log").write_text("")
        (self.root / "pkg").mkdir()
        self.track()

    def sync(self):
        return self.cli("--package-cache", self.root / "pkg", "--pacman-log", self.root / "empty.log",
                        "sync", "--notify")

    def row(self):
        return self.cli("status")["clones"][0]

    def notifications(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_status_reports_whether_an_update_merges_cleanly(self):
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.source, "Original farewell", "Upstream farewell")
        row = self.row()
        self.assertEqual((row["status"], row["conflicts"]), ("diverged", 0))
        self.assertIn("merge cleanly", row["message"])
        self.replace(self.source, "Original greeting", "Upstream greeting")
        row = self.row()
        self.assertEqual(row["conflicts"], 1)
        self.assertIn("overlap in 1 file", row["message"])

    def test_notifies_once_per_upstream_change(self):
        self.assertEqual(self.sync()["notified"], [])
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.source, "Original farewell", "Upstream farewell")
        self.assertEqual(self.sync()["notified"], [test_cli.CLONE_ID])
        self.assertEqual(self.sync()["notified"], [])
        [message] = self.notifications()
        self.assertIn("Omarchy update for Neil's Clock", message)
        self.assertIn("merges cleanly", message)
        self.assertIn("--exec omarchy-shell neil.clone-keeper open", message)
        self.replace(self.source, "Upstream farewell", "Newer farewell")
        self.assertEqual(self.sync()["notified"], [test_cli.CLONE_ID])
        self.assertEqual(len(self.notifications()), 2)

    def test_failed_notification_is_retried(self):
        self.replace(self.source, "Original farewell", "Upstream farewell")
        stub = self.fake_bin / "omarchy-notification-send"
        stub.write_text(stub.read_text().replace("${NOTIFY_EXIT:-0}", "1"))
        self.assertEqual(self.sync()["notified"], [])
        self.assertFalse((self.state / "notified.json").exists())
        stub.write_text(stub.read_text().replace('exit "1"', 'exit "0"'))
        self.assertEqual(self.sync()["notified"], [test_cli.CLONE_ID])
        self.assertEqual(json.loads((self.state / "notified.json").read_text()).keys(), {test_cli.CLONE_ID})

    def test_sync_without_notify_is_silent(self):
        self.replace(self.source, "Original farewell", "Upstream farewell")
        result = self.cli("--package-cache", self.root / "pkg", "--pacman-log", self.root / "empty.log", "sync")
        self.assertEqual(result["notified"], [])
        self.assertEqual(self.notifications(), [])


if __name__ == "__main__":
    unittest.main()
