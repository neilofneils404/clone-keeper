"""Recognize an updated clone's load failures in the restarted shell's log."""

from pathlib import Path
import unittest
from unittest import mock

from clone_keeper import health
from clone_keeper.core import KeeperError

PATH = "/home/u/.config/omarchy/plugins/neil.audio"
LOG = "\n".join([
    "\x1b[33m  WARN\x1b[97m scene\x1b[0m: Plugin widget neil.audio failed: file://" + PATH + "/Panel.qml:12:5: Foo is not a type",
    "  WARN scene: Plugin widget neil.audio-copy failed: unrelated",
    "  WARN scene: panel plugin omarchy.audio failed to load: unrelated",
    "  WARN qml: file://" + PATH + "/Panel.qml:40: TypeError: Cannot read property 'x' of null",
    "  INFO: Configuration Loaded",
])


QS_LIST = """Instance a:
  Process ID: 11
  Config path: /usr/share/omarchy/shell/shell.qml
Instance b:
  Process ID: 22
  Config path: /tmp/pilot/omarchy/shell/shell.qml
"""


class InstanceTests(unittest.TestCase):
    def test_finds_the_instance_for_a_config(self):
        with mock.patch.object(health.subprocess, "run",
                               return_value=mock.Mock(stdout=QS_LIST, returncode=0)):
            self.assertEqual(health.shell_instance("/tmp/pilot/omarchy/shell"), 22)
            self.assertEqual(health.shell_instance("/usr/share/omarchy/shell/"), 11)
            self.assertIsNone(health.shell_instance("/elsewhere"))


class LogTests(unittest.TestCase):
    def test_separates_load_failures_from_other_messages(self):
        failures, others = health.clone_messages(LOG, "neil.audio", PATH)
        self.assertEqual(len(failures), 1)
        self.assertIn("Foo is not a type", failures[0])
        self.assertNotIn("\x1b", failures[0])
        self.assertEqual(len(others), 1)
        self.assertIn("TypeError", others[0])

    def test_recognizes_each_shell_failure_format(self):
        for line in ("panel plugin neil.audio failed to load: x", "service plugin load failed for neil.audio: x",
                     "bar option neil.audio failed to load, falling back to omarchy.bar"):
            self.assertEqual(len(health.clone_messages(line, "neil.audio", PATH)[0]), 1, line)

    def test_clean_log(self):
        self.assertEqual(health.clone_messages("INFO: Configuration Loaded", "neil.audio", PATH), ([], []))


class VerifyTests(unittest.TestCase):
    """Workflow.verify_update with the shell restart and log stubbed out."""

    def setUp(self):
        from clone_keeper.core import Keeper
        from clone_keeper.service import Workflow
        self.work = Workflow(Keeper(Path("/o"), Path(PATH).parent, Path("/s")))
        self.work.restart_shell = mock.Mock(return_value={})
        self.work.transactions = mock.Mock()
        patcher = mock.patch.object(health.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def verify(self, log, pids=(100, 200, 200, 300)):
        instances = iter(pids)
        with mock.patch.object(health, "shell_log", return_value=log) as read, \
                mock.patch.object(health, "shell_instance", side_effect=lambda config: next(instances)):
            result = self.work.verify_update("neil.audio")
        self.read = read
        return result

    def test_reads_the_restarted_instances_log(self):
        self.verify("INFO: Configuration Loaded")
        self.read.assert_called_once_with(200)

    def test_unchanged_shell_instance_is_not_trusted(self):
        result = self.verify(LOG, pids=(100, 100))
        self.assertFalse(result["restarted"])
        self.assertIn("restarted shell", result["warning"])
        self.work.transactions.undo.assert_not_called()

    def test_healthy_update_is_kept(self):
        result = self.verify("INFO: Configuration Loaded")
        self.assertTrue(result["restarted"])
        self.assertNotIn("undone", result)
        self.work.transactions.undo.assert_not_called()

    def test_load_failure_is_undone_and_shell_restarted_again(self):
        result = self.verify(LOG)
        self.assertTrue(result["undone"])
        self.assertIn("Plugin widget neil.audio failed: Panel.qml:12:5: Foo is not a type", result["message"])
        self.work.transactions.undo.assert_called_once_with("neil.audio")
        self.assertEqual(self.work.restart_shell.call_count, 2)

    def test_failed_undo_is_reported(self):
        self.work.transactions.undo.side_effect = KeeperError("edited since apply")
        result = self.verify(LOG)
        self.assertFalse(result["undone"])
        self.assertIn("edited since apply", result["warning"])

    def test_restart_refusal_keeps_update_with_warning(self):
        self.work.restart_shell.side_effect = KeeperError("session is locked")
        result = self.verify("")
        self.assertFalse(result["restarted"])
        self.assertIn("session is locked", result["warning"])
        self.work.transactions.undo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
