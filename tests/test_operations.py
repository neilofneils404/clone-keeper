"""Detached operations, saved-review cleanup and panel-facing status details."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

from clone_keeper.core import Keeper, KeeperError
from clone_keeper.operations import Operations, process_identity
from clone_keeper.service import Workflow
from tests import test_cli, test_workflow


def wait_for(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for condition")


class DetachedOperationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="clone-keeper-operations-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.operations = Operations(self.state)

    def records(self):
        directory = self.state / "operations"
        return [json.loads(path.read_text()) for path in directory.glob("*.json")] if directory.exists() else []

    def test_worker_finishes_and_records_outcome_after_its_caller_is_killed(self):
        # Quickshell kills the helper of a destroyed Process with SIGKILL.
        marker = self.root / "finished"
        script = textwrap.dedent(f"""
            import sys, time
            from pathlib import Path
            sys.path.insert(0, {str(test_cli.PROJECT)!r})
            from clone_keeper.operations import Operations
            def work():
                time.sleep(1.0)
                Path({str(marker)!r}).write_text("done")
                return {{"message": "Finished after the caller died.", "reloadRequired": False}}
            print("started", flush=True)
            Operations(Path({str(self.state)!r})).run("demo.clock", "apply", work)
        """)
        caller = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: caller.poll() is None and caller.kill())
        wait_for(lambda: any(record["status"] == "running" for record in self.records()))
        caller.send_signal(signal.SIGKILL)
        # The worker must not hold the caller's pipes, or the panel's
        # collectors would wait for the detached work to end.
        output, _ = caller.communicate(timeout=2)
        self.assertEqual(output, b"started\n")
        self.assertFalse(marker.exists(), "Work finished before the caller was killed")
        record = wait_for(lambda: next((item for item in self.records() if item["status"] == "succeeded"), None))
        self.assertEqual(marker.read_text(), "done")
        summaries, errors = self.operations.summaries()
        self.assertEqual(errors, [])
        self.assertEqual([(item["status"], item["message"]) for item in summaries],
                         [("succeeded", "Finished after the caller died.")])
        self.assertEqual(self.operations.dismiss([record["requestId"]])["dismissed"], [record["requestId"]])
        self.assertEqual(self.records(), [])

    def test_caller_receives_failure_recorded_by_worker(self):
        def work():
            raise KeeperError("Candidate hash differs from the reviewed candidate")
        record = self.operations.run("demo.clock", "apply", work)
        self.assertEqual(record["status"], "failed")
        self.assertIn("Candidate hash differs", record["error"])
        self.assertEqual(self.operations.summaries()[0][0]["status"], "failed")

    def write_running(self, pid, start):
        request_id = "d" * 32
        self.operations._write({"schemaVersion": 1, "requestId": request_id, "cloneId": "demo.clock",
                                "operation": "enable", "status": "running", "pid": pid,
                                "processStart": start, "startedAt": "2026-09-26T00:00:00+00:00"})
        return request_id

    def test_running_record_of_a_dead_process_is_reported_as_interrupted(self):
        process = subprocess.Popen(["true"])
        process.wait()
        request_id = self.write_running(process.pid, "1")
        summary = self.operations.summaries()[0][0]
        self.assertEqual(summary["status"], "interrupted")
        self.assertIn("recover", summary["message"])
        self.assertEqual(self.operations.dismiss([request_id])["dismissed"], [request_id])

    def test_live_running_operation_is_reported_and_cannot_be_dismissed(self):
        request_id = self.write_running(os.getpid(), process_identity(os.getpid()))
        self.assertEqual(self.operations.summaries()[0][0]["status"], "running")
        with self.assertRaisesRegex(KeeperError, "still running"):
            self.operations.dismiss([request_id])

    def test_reused_process_id_is_not_mistaken_for_the_worker(self):
        self.write_running(os.getpid(), "not-this-process-start-time")
        self.assertEqual(self.operations.summaries()[0][0]["status"], "interrupted")

    def test_malformed_records_are_reported_without_hiding_others(self):
        (self.state / "operations").mkdir(parents=True)
        (self.state / "operations" / ("e" * 32 + ".json")).write_text("not json")
        self.operations.run("demo.clock", "undo", lambda: {"message": "Restored."})
        summaries, errors = self.operations.summaries()
        self.assertEqual([item["status"] for item in summaries], ["succeeded"])
        self.assertEqual(len(errors), 1)

    def test_request_ids_cannot_name_other_files(self):
        with self.assertRaises(KeeperError):
            self.operations.dismiss(["../clones/demo.clock/record"])


@unittest.skipUnless(shutil.which("git"), "git is required for merge integration tests")
class DetachedCliTests(test_workflow.WorkflowFixture):
    """Run the documented panel commands through the real CLI boundary."""

    def test_detached_apply_returns_its_result_and_leaves_an_outcome_for_status(self):
        plan = self.clean_update()
        result = self.cli("apply", plan["planId"], "--expect", plan["candidateHash"], "--detach")
        self.assertEqual(result["status"], "applied")
        self.assertRegex(result["requestId"], r"^[0-9a-f]{32}$")
        self.assertIn("Upstream farewell", (self.clone / "BarWidget.qml").read_text())
        operations = self.cli("status", readonly=True)["operations"]
        self.assertEqual([(item["requestId"], item["operation"], item["status"]) for item in operations],
                         [(result["requestId"], "apply", "succeeded")])
        self.assertEqual(self.cli("dismiss", result["requestId"])["dismissed"], [result["requestId"]])
        self.assertEqual(self.cli("status", readonly=True)["operations"], [])

    def test_detached_failure_is_json_with_request_id_and_recorded(self):
        plan = self.clean_update()
        result = self.cli("apply", plan["planId"], "--expect", "0" * 64, "--detach", success=False)
        error = json.loads(result.stdout)
        self.assertIn("Candidate hash differs", error["error"])
        operations = self.cli("status", readonly=True)["operations"]
        self.assertEqual([(item["requestId"], item["status"]) for item in operations],
                         [(error["requestId"], "failed")])

    def test_detached_clone_names_its_target_before_creating_it(self):
        result = self.cli("clone", test_cli.SOURCE_ID, "--id", "neil.second-clock", "--detach")
        self.assertEqual(result["cloneId"], "neil.second-clock")
        self.assertTrue((self.plugins / "neil.second-clock" / "manifest.json").exists())
        self.assertEqual(self.cli("status", readonly=True)["operations"][0]["cloneId"], "neil.second-clock")

    def test_unknown_plan_fails_before_any_worker_starts(self):
        self.cli("apply", "f" * 32, "--expect", "0" * 64, "--detach", success=False)
        self.assertFalse((self.state / "operations").exists())

    def test_relative_home_paths_are_expanded_for_replacement_files(self):
        self.track()
        self.replace(self.source, "Original greeting", "Upstream greeting")
        self.replace(self.clone, "Original greeting", "Local greeting")
        plan = self.prepare()
        self.assertTrue(plan["hasConflicts"])
        home = self.root / "home"
        home.mkdir()
        (home / "resolved.qml").write_text(test_cli.CloneKeeperIntegrationTests.base_qml()
                                           .replace("Original greeting", "Resolved greeting"))
        with patch.dict(os.environ, {"HOME": str(home)}):
            resolved = self.cli("resolve", plan["planId"], "--path", "BarWidget.qml", "--file", "~/resolved.qml")
        self.assertFalse(resolved["hasConflicts"])
        self.assertIn("Resolved greeting", (Path(resolved["editablePath"]) / "BarWidget.qml").read_text())


@unittest.skipUnless(shutil.which("git"), "git is required for merge integration tests")
class CleanupAndStatusTests(test_workflow.WorkflowFixture):
    def plan_ids(self):
        return sorted(item["planId"] for item in self.cli("plans", readonly=True)["plans"])

    def latest_plan(self):
        return self.cli("status", readonly=True)["clones"][0]["latestPlan"]

    def test_preparing_again_replaces_only_untouched_reviews_of_the_same_inputs(self):
        self.track()
        self.replace(self.source, "Original greeting", "Upstream greeting")
        self.replace(self.clone, "Original greeting", "Local greeting")
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(self.plan_ids(), [second["planId"]])
        self.cli("resolve", second["planId"], "--path", "BarWidget.qml", "--take", "local", readonly=True)
        third = self.prepare()
        self.assertEqual(self.plan_ids(), sorted([second["planId"], third["planId"]]))
        self.assertNotEqual(first["planId"], third["planId"])

    def test_resume_offers_only_plans_for_the_current_baseline(self):
        plan = self.clean_update()
        self.assertEqual(self.latest_plan()["planId"], plan["planId"])
        self.apply(plan)
        self.assertIsNone(self.latest_plan(), "An applied plan can no longer be applied")
        self.cli("undo", test_cli.CLONE_ID)
        self.assertEqual(self.latest_plan()["planId"], plan["planId"],
                         "Undo restores the baseline the retained plan was prepared against")

    def test_apply_keeps_current_and_previous_baseline_plans_only(self):
        first = self.clean_update()
        self.apply(first)
        self.replace(self.source, "Original greeting", "Second upstream greeting")
        second = self.prepare()
        self.apply(second)
        remaining = self.plan_ids()
        self.assertNotIn(first["planId"], remaining)
        self.assertIn(second["planId"], remaining)

    def test_apply_keeps_the_clone_manifest_layout(self):
        self.track()
        manifest = self.clone / "manifest.json"
        original_keys = list(json.loads(manifest.read_text()))
        self.assertNotEqual(original_keys, sorted(original_keys))
        upstream = json.loads((self.source / "manifest.json").read_text())
        upstream["version"] = "1.1.0"
        (self.source / "manifest.json").write_text(json.dumps(upstream, indent=2) + "\n")
        plan = self.prepare()
        self.apply(plan)
        text = manifest.read_text()
        value = json.loads(text)
        self.assertEqual(list(value)[:len(original_keys)], original_keys)
        self.assertEqual(value["version"], "1.1.0")
        self.assertEqual(value["id"], test_cli.CLONE_ID)
        self.assertTrue(text.startswith('{\n  "schemaVersion"'))
        status = self.cli("status", readonly=True)["clones"][0]
        self.assertEqual(status["status"], "up-to-date", "Manifest layout is not a local customization")


class EnabledStatusTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_cli.CloneKeeperIntegrationTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.workflow = Workflow(Keeper(self.fixture.omarchy, self.fixture.plugins, self.fixture.state))

    def test_live_status_reports_activation_from_the_shell_registry(self):
        listing = json.dumps([{"id": test_cli.CLONE_ID, "enabled": True}, {"id": "other", "enabled": False}])
        with patch.object(self.workflow, "is_live", return_value=True), \
                patch.object(self.workflow, "_shell", return_value=listing) as shell:
            row = self.workflow.status()["clones"][0]
        self.assertIs(row["enabled"], True)
        shell.assert_called_once_with("shell", "listPlugins", timeout=2)

    def test_unavailable_shell_leaves_activation_unknown(self):
        with patch.object(self.workflow, "is_live", return_value=True), \
                patch.object(self.workflow, "_shell", side_effect=KeeperError("no shell")):
            row = self.workflow.status()["clones"][0]
        self.assertIsNone(row["enabled"])

    def test_fixture_roots_never_query_the_shell(self):
        with patch.object(self.workflow, "_shell") as shell:
            row = self.workflow.status()["clones"][0]
        shell.assert_not_called()
        self.assertIsNone(row["enabled"])


if __name__ == "__main__":
    unittest.main()
