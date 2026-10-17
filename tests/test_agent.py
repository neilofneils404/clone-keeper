"""Hand one conflicting candidate file to the owner's coding agent."""

import subprocess
import unittest
from unittest import mock

from clone_keeper.core import Keeper, KeeperError
from clone_keeper.service import Workflow
from tests.test_workflow import WorkflowFixture


class AgentTests(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.track()
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.source, "Original greeting", "Upstream greeting")
        self.plan = self.prepare()
        self.work = Workflow(Keeper(self.omarchy, self.plugins, self.state))
        self.work.is_live = lambda: True
        self.launched = []

    def run_agent(self, default="claude"):
        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=default + "\n", stderr="")

        def fake_popen(command, **kwargs):
            self.launched.append((command, kwargs))

        with mock.patch("clone_keeper.service.subprocess.run", fake_run), \
                mock.patch("clone_keeper.service.subprocess.Popen", fake_popen):
            return self.work.ask_agent(self.plan["planId"], "BarWidget.qml")

    def test_prompt_names_the_candidate_and_read_only_versions(self):
        before = self.snapshot(self.state / "plans")
        result = self.run_agent()
        [(command, kwargs)] = self.launched
        self.assertEqual(command[0], "omarchy-agent-prompt")
        candidate = str(self.state / "plans" / self.plan["planId"] / "candidate" / "BarWidget.qml")
        self.assertIn(candidate, command[1])
        self.assertIn("/local/BarWidget.qml", command[1])
        self.assertIn("Edit only", command[1])
        self.assertEqual(str(kwargs["cwd"]), str(self.state / "plans" / self.plan["planId"] / "candidate"))
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(result["agent"], "claude")
        self.assertEqual(before, self.snapshot(self.state / "plans"), "Asking an agent changed the plan")

    def test_requires_a_default_agent(self):
        with self.assertRaisesRegex(KeeperError, "default coding agent"):
            self.run_agent(default="")
        self.assertEqual(self.launched, [])

    def test_refuses_outside_the_live_plugin_directory(self):
        result = self.cli("ask-agent", self.plan["planId"], "--path", "BarWidget.qml", success=False, readonly=True)
        self.assertIn("installed user plugin directory", result.stdout + result.stderr)

    def test_refuses_paths_outside_the_candidate(self):
        with self.assertRaises(KeeperError):
            with mock.patch("clone_keeper.service.subprocess.run",
                            lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="claude\n", stderr="")):
                self.work.ask_agent(self.plan["planId"], "../record.json")


if __name__ == "__main__":
    unittest.main()
