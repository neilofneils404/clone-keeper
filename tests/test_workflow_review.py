"""Publication-boundary recovery and corrupted saved-input regressions."""

from contextlib import redirect_stdout
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from clone_keeper import cli
from clone_keeper.core import Keeper, KeeperError
from clone_keeper.service import Workflow
from tests import test_cli


class InterruptedPublication(BaseException):
    """Simulate process loss after a durable publication boundary."""


class WorkflowReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_cli.CloneKeeperIntegrationTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.keeper = Keeper(self.fixture.omarchy, self.fixture.plugins, self.fixture.state)
        self.workflow = Workflow(self.keeper)
        self.new_id = "neil.recovered-clock"
        self.new_clone = self.fixture.plugins / self.new_id
        self.source_before = self.fixture.snapshot(self.fixture.omarchy)

    def tearDown(self):
        self.assertEqual(self.source_before, self.fixture.snapshot(self.fixture.omarchy),
                         "Recovery must never modify Omarchy sources")

    def interrupt_after_publication(self, destination):
        from clone_keeper import service
        publish = service.atomic_new_tree

        def interrupted_publish(path, tree):
            publish(path, tree)
            if path == destination:
                raise InterruptedPublication()

        with patch("clone_keeper.service.atomic_new_tree", side_effect=interrupted_publish):
            with self.assertRaises(InterruptedPublication):
                self.workflow.create(test_cli.SOURCE_ID, self.new_id)
        record, _ = self.keeper.load_baseline(self.new_id)
        self.assertTrue(record["creationPending"])

    def assert_creation_completed(self):
        record, baseline = self.keeper.load_baseline(self.new_id)
        self.assertFalse(record["creationPending"])
        _, local, manifest, source_id = self.keeper.clone(self.new_id)
        self.assertEqual(local, baseline)
        self.assertEqual(manifest["id"], self.new_id)
        self.assertEqual(source_id, test_cli.SOURCE_ID)

    def test_recovery_finishes_after_baseline_published_before_clone(self):
        self.interrupt_after_publication(self.keeper.state_path(self.new_id))
        self.assertFalse(self.new_clone.exists())
        result = self.workflow.recover(self.new_id)
        self.assertFalse(result["enabled"])
        self.assert_creation_completed()

    def test_recovery_finishes_after_clone_published_before_completion_record(self):
        self.interrupt_after_publication(self.new_clone)
        before = self.fixture.snapshot(self.new_clone)
        self.workflow.recover(self.new_id)
        self.assertEqual(before, self.fixture.snapshot(self.new_clone))
        self.assert_creation_completed()

    def test_recovery_preserves_edits_after_interrupted_clone_publication(self):
        self.interrupt_after_publication(self.new_clone)
        self.fixture.write(self.new_clone, "README.md", "User edit after interruption.\n")
        before = self.fixture.snapshot(self.new_clone)
        with self.assertRaises(KeeperError):
            self.workflow.recover(self.new_id)
        self.assertEqual(before, self.fixture.snapshot(self.new_clone))
        record, _ = self.keeper.load_baseline(self.new_id)
        self.assertTrue(record["creationPending"])

    def test_recovery_refuses_foreign_empty_directory_at_pending_target(self):
        self.interrupt_after_publication(self.keeper.state_path(self.new_id))
        self.new_clone.mkdir(mode=0o750)
        with self.assertRaises(KeeperError):
            self.workflow.recover(self.new_id)
        self.assertEqual(list(self.new_clone.iterdir()), [])
        self.assertEqual(self.new_clone.stat().st_mode & 0o777, 0o750)

    def test_stale_configured_id_refuses_creation_before_publishing_any_tree(self):
        configurations = [
            {"bar": {"id": self.new_id}},
            {"bar": {"layout": {"left": [self.new_id]}}},
            {"bar": {"layout": {"right": [{"id": self.new_id}]}}},
            {"plugins": [{"id": self.new_id}]},
        ]
        config = self.fixture.plugins.parent / "shell.json"
        for value in configurations:
            with self.subTest(config=value):
                config.write_text(json.dumps(value))
                config_before = config.read_bytes()
                with self.assertRaises(KeeperError):
                    self.workflow.create(test_cli.SOURCE_ID, self.new_id)
                self.assertFalse(self.new_clone.exists())
                self.assertFalse(self.keeper.state_path(self.new_id).exists())
                self.assertEqual(config_before, config.read_bytes())

    def test_pending_creation_rechecks_shell_references_before_live_publication(self):
        self.interrupt_after_publication(self.keeper.state_path(self.new_id))
        config = self.fixture.plugins.parent / "shell.json"
        config.write_text(json.dumps({"plugins": [{"id": self.new_id}]}))
        with self.assertRaises(KeeperError):
            self.workflow.recover(self.new_id)
        self.assertFalse(self.new_clone.exists())
        record, _ = self.keeper.load_baseline(self.new_id)
        self.assertTrue(record["creationPending"])

    def test_corrupt_immutable_saved_plan_input_cannot_be_reviewed_or_applied(self):
        self.fixture.track()
        self.fixture.replace(self.fixture.source, "Original farewell", "Upstream farewell")
        self.source_before = self.fixture.snapshot(self.fixture.omarchy)
        plan = self.workflow.plans.prepare(test_cli.CLONE_ID)
        original_clone = self.fixture.snapshot(self.fixture.clone)
        original_state = self.fixture.snapshot(self.keeper.state_path(test_cli.CLONE_ID))
        saved = self.fixture.state / "plans" / plan["planId"] / "upstream" / "README.md"
        saved.write_text("A corrupt source snapshot.\n")
        with self.assertRaises(KeeperError):
            self.workflow.plans.review(plan["planId"])
        with self.assertRaises(KeeperError):
            self.workflow.transactions.apply(plan["planId"], plan["candidateHash"])
        self.assertEqual(original_clone, self.fixture.snapshot(self.fixture.clone))
        self.assertEqual(original_state, self.fixture.snapshot(self.keeper.state_path(test_cli.CLONE_ID)))

    def test_cross_tree_plan_cannot_be_applied_to_another_plugins_directory(self):
        self.fixture.track()
        self.fixture.replace(self.fixture.source, "Original farewell", "Upstream farewell")
        self.source_before = self.fixture.snapshot(self.fixture.omarchy)
        plan = self.workflow.plans.prepare(test_cli.CLONE_ID)
        other_plugins = self.fixture.root / "different-plugins"
        other_plugins.mkdir()
        other = Workflow(Keeper(self.fixture.omarchy, other_plugins, self.fixture.state))
        original = self.fixture.snapshot(self.fixture.plugins)
        with self.assertRaises(KeeperError):
            other.plans.review(plan["planId"])
        with self.assertRaises(KeeperError):
            other.transactions.apply(plan["planId"], plan["candidateHash"])
        self.assertEqual(original, self.fixture.snapshot(self.fixture.plugins))
        self.assertEqual(list(other_plugins.iterdir()), [])

    def test_traversal_clone_id_in_saved_plan_never_mutates_outside_plugins(self):
        self.fixture.track()
        plan = self.workflow.plans.prepare(test_cli.CLONE_ID)
        record_path = self.fixture.state / "plans" / plan["planId"] / "record.json"
        record = json.loads(record_path.read_text())
        record["cloneId"] = "../outside"
        record["clonePath"] = str(self.fixture.plugins / "../outside")
        record_path.write_text(json.dumps(record))
        outside = self.fixture.root / "outside"
        self.fixture.write(outside, "keep.txt", "Do not touch.\n")
        outside_before = self.fixture.snapshot(outside)
        with self.assertRaises(KeeperError):
            self.workflow.transactions.apply(plan["planId"], plan["candidateHash"])
        self.assertEqual(outside_before, self.fixture.snapshot(outside))

    def test_enable_waits_for_discovery_then_activates_exactly_once(self):
        clock = [0.0]
        replies = iter(["", "[]", '[{"id":"another.plugin"}]',
                        json.dumps([{"id": test_cli.CLONE_ID}]), "ok"])
        clone_before = self.fixture.snapshot(self.fixture.clone)

        def run(command, **kwargs):
            clock[0] += 0.2
            return subprocess.CompletedProcess(command, 0, next(replies), "")

        with patch.object(self.workflow, "is_live", return_value=True), \
                patch("clone_keeper.service.subprocess.run", side_effect=run) as ipc, \
                patch("clone_keeper.service.time.monotonic", side_effect=lambda: clock[0]), \
                patch("clone_keeper.service.time.sleep", side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            result = self.workflow.enable(test_cli.CLONE_ID)
        self.assertTrue(result["enabled"])
        self.assertEqual([call.args[0][2] for call in ipc.call_args_list],
                         ["rescanPlugins", "listPlugins", "listPlugins", "listPlugins", "enablePlugin"])
        self.assertTrue(all(0 < call.kwargs["timeout"] <= 8 for call in ipc.call_args_list))
        self.assertEqual(clone_before, self.fixture.snapshot(self.fixture.clone))

    def test_enable_discovery_timeout_is_bounded_and_never_activates(self):
        clock = [0.0]

        def run(command, **kwargs):
            clock[0] += min(1, kwargs["timeout"])
            return subprocess.CompletedProcess(command, 0, "" if command[2] == "rescanPlugins" else "[]", "")

        with patch.object(self.workflow, "is_live", return_value=True), \
                patch("clone_keeper.service.subprocess.run", side_effect=run) as ipc, \
                patch("clone_keeper.service.time.monotonic", side_effect=lambda: clock[0]), \
                patch("clone_keeper.service.time.sleep", side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            with self.assertRaisesRegex(KeeperError, "Timed out"):
                self.workflow.enable(test_cli.CLONE_ID)
        self.assertLessEqual(clock[0], 8.001)
        self.assertNotIn("enablePlugin", [call.args[0][2] for call in ipc.call_args_list])

    def test_enable_refuses_invalid_registry_data_without_activation(self):
        for invalid in ["not-json", '{}', '[null]', '[{"name":"Missing ID"}]']:
            with self.subTest(response=invalid):
                replies = [subprocess.CompletedProcess([], 0, "", ""),
                           subprocess.CompletedProcess([], 0, invalid, "")]
                with patch.object(self.workflow, "is_live", return_value=True), \
                        patch("clone_keeper.service.subprocess.run", side_effect=replies) as ipc:
                    with self.assertRaisesRegex(KeeperError, "invalid plugin list"):
                        self.workflow.enable(test_cli.CLONE_ID)
                self.assertEqual([call.args[0][2] for call in ipc.call_args_list],
                                 ["rescanPlugins", "listPlugins"])

    def test_enable_does_not_retry_a_shell_transport_failure(self):
        with patch.object(self.workflow, "is_live", return_value=True), \
                patch("clone_keeper.service.subprocess.run", side_effect=subprocess.TimeoutExpired("omarchy-shell", 8)) as ipc:
            with self.assertRaises(KeeperError):
                self.workflow.enable(test_cli.CLONE_ID)
        self.assertEqual(ipc.call_count, 1)

    def test_clone_remains_saved_when_shell_rejects_activation(self):
        def run(command, **kwargs):
            response = {"rescanPlugins": "", "listPlugins": json.dumps([{"id": self.new_id}]),
                        "enablePlugin": "activation rejected by shell"}[command[2]]
            return subprocess.CompletedProcess(command, 0, response, "")

        output = io.StringIO()
        with patch.object(Workflow, "is_live", return_value=True), \
                patch("clone_keeper.service.subprocess.run", side_effect=run) as ipc, \
                redirect_stdout(output):
            result_code = cli.main(["--omarchy-path", str(self.fixture.omarchy),
                                    "--plugins-dir", str(self.fixture.plugins),
                                    "--state-dir", str(self.fixture.state),
                                    "clone", test_cli.SOURCE_ID, "--id", self.new_id, "--enable", "--json"])
        result = json.loads(output.getvalue())
        self.assertEqual(result_code, 0)
        self.assertTrue(result["tracked"])
        self.assertFalse(result["enabled"])
        self.assertIn("Clone saved", result["warning"])
        self.assertIn("activation rejected", result["warning"])
        self.assertEqual(sum(call.args[0][2] == "enablePlugin" for call in ipc.call_args_list), 1)
        self.assert_creation_completed()

    def test_enable_refuses_pending_creation_without_shell_ipc(self):
        self.interrupt_after_publication(self.new_clone)
        with patch.object(self.workflow, "is_live", return_value=True), \
                patch("clone_keeper.service.subprocess.run") as ipc:
            with self.assertRaisesRegex(KeeperError, "unfinished creation"):
                self.workflow.enable(self.new_id)
        ipc.assert_not_called()

    def test_enable_refuses_pending_transaction_without_shell_ipc(self):
        self.fixture.track()
        plan = self.workflow.plans.prepare(test_cli.CLONE_ID)
        with patch.object(self.workflow.transactions, "_checkpoint", side_effect=InterruptedPublication):
            with self.assertRaises(InterruptedPublication):
                self.workflow.transactions.apply(plan["planId"], plan["candidateHash"])
        self.assertIsNotNone(self.workflow.transactions.history(test_cli.CLONE_ID)["pendingTransaction"])
        with patch.object(self.workflow, "is_live", return_value=True), \
                patch("clone_keeper.service.subprocess.run") as ipc:
            with self.assertRaisesRegex(KeeperError, "unfinished transaction"):
                self.workflow.enable(test_cli.CLONE_ID)
        ipc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
