"""Independent CLI acceptance tests for reviewed apply, undo and clone creation."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from tests import test_cli


class WorkflowFixture(unittest.TestCase):
    """Disposable roots and CLI helpers shared by workflow acceptance tests."""

    def setUp(self):
        # Reuse the disposable real-schema fixture without inheriting its tests.
        self.fixture = test_cli.CloneKeeperIntegrationTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        for name in ("root", "omarchy", "source", "plugins", "clone", "state"):
            setattr(self, name, getattr(self.fixture, name))
        self.snapshot = self.fixture.snapshot
        self.write = self.fixture.write
        self.replace = self.fixture.replace
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.shell_calls = self.root / "unexpected-shell-call"
        for name in ("omarchy", "omarchy-shell"):
            stub = self.fake_bin / name
            stub.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$0 $*" >> "$CLONE_KEEPER_TEST_SHELL_CALLS"\n'
                "exit 99\n"
            )
            stub.chmod(0o755)

    def cli(self, *arguments, success=True, readonly=False):
        source_before = self.snapshot(self.omarchy)
        local_before = self.snapshot(self.plugins)
        environment = os.environ.copy()
        environment["PATH"] = str(self.fake_bin) + os.pathsep + environment["PATH"]
        environment["CLONE_KEEPER_TEST_SHELL_CALLS"] = str(self.shell_calls)
        result = subprocess.run(
            [sys.executable, str(test_cli.CLI),
             "--omarchy-path", str(self.omarchy),
             "--plugins-dir", str(self.plugins),
             "--state-dir", str(self.state),
             *map(str, arguments), "--json"],
            text=True, capture_output=True, timeout=25,
            cwd=self.root, env=environment,
        )
        self.assertEqual(source_before, self.snapshot(self.omarchy),
                         "Workflow wrote to installed Omarchy source")
        if readonly:
            self.assertEqual(local_before, self.snapshot(self.plugins),
                             "Review operation wrote to installed clones")
        self.assertFalse(self.shell_calls.exists(),
                         "Fixture workflow must not invoke the live Omarchy shell")
        self.assertNotIn("Traceback (most recent call last)", result.stderr,
                         result.stderr)
        if success is True:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        elif success is False:
            self.assertNotEqual(result.returncode, 0,
                                "Unsafe operation unexpectedly succeeded")
        if result.returncode != 0:
            return result
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(f"Workflow did not return JSON: {result.stdout!r}")

    def track(self):
        return self.cli("track", test_cli.CLONE_ID, readonly=True)

    def prepare(self):
        return self.cli("prepare", test_cli.CLONE_ID, readonly=True)

    def review(self, plan):
        return self.cli("review", plan["planId"], readonly=True)

    def apply(self, plan, *, success=True):
        return self.cli("apply", plan["planId"], "--expect",
                        plan["candidateHash"], success=success)

    def baseline_snapshot(self):
        return self.snapshot(self.state / "clones" / test_cli.CLONE_ID)

    def clean_update(self):
        self.track()
        self.replace(self.source, "Original farewell", "Upstream farewell")
        plan = self.prepare()
        self.assertFalse(plan["hasConflicts"])
        self.assertTrue(plan["validation"]["valid"])
        return plan

    def assert_apply_refused_without_changing_install(self, plan):
        local_before = self.snapshot(self.plugins)
        baseline_before = self.baseline_snapshot()
        self.apply(plan, success=False)
        self.assertEqual(local_before, self.snapshot(self.plugins))
        self.assertEqual(baseline_before, self.baseline_snapshot())


@unittest.skipUnless(shutil.which("git"), "git is required for merge integration tests")
class CloneKeeperWorkflowTests(WorkflowFixture):
    def test_prepare_and_review_leave_installed_clone_and_baseline_unchanged(self):
        plan = self.clean_update()
        baseline_before = self.baseline_snapshot()
        reviewed = self.review(plan)
        self.assertEqual(reviewed["planId"], plan["planId"])
        self.assertEqual(reviewed["candidateHash"], plan["candidateHash"])
        self.assertTrue(any(change["path"] == "BarWidget.qml"
                            for change in reviewed["changes"]))
        self.assertEqual(baseline_before, self.baseline_snapshot())

    def test_apply_advances_baseline_to_upstream_without_absorbing_local_edits(self):
        self.track()
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.source, "Original farewell", "Upstream farewell")
        plan = self.prepare()
        self.assertFalse(plan["hasConflicts"])
        self.apply(plan)
        installed = (self.clone / "BarWidget.qml").read_text()
        self.assertIn("Local greeting", installed)
        self.assertIn("Upstream farewell", installed)
        baseline = self.state / "clones" / test_cli.CLONE_ID / "baseline" / "BarWidget.qml"
        self.assertIn("Original greeting", baseline.read_text())
        self.assertNotIn("Local greeting", baseline.read_text())
        self.assertIn("Upstream farewell", baseline.read_text())
        status = self.cli("status", readonly=True)["clones"][0]
        self.assertGreater(status["localChangedFiles"], 0)
        self.assertEqual(status["upstreamChangedFiles"], 0)

    def test_second_upstream_update_preserves_original_local_customization(self):
        self.track()
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.source, "Original farewell", "First upstream farewell")
        self.apply(self.prepare())
        self.replace(self.source, "First upstream farewell", "Second upstream farewell")
        second = self.prepare()
        self.assertFalse(second["hasConflicts"])
        self.apply(second)
        installed = (self.clone / "BarWidget.qml").read_text()
        self.assertIn("Local greeting", installed)
        self.assertIn("Second upstream farewell", installed)
        self.assertNotIn("First upstream farewell", installed)

    def test_undo_restores_exact_clone_and_baseline_bytes_and_modes(self):
        for directory in (self.source, self.clone):
            self.write(directory, "helper.sh", "#!/bin/sh\nexit 0\n").chmod(0o755)
        self.track()
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.write(self.clone, "local.bin", b"\x00private\xff").chmod(0o600)
        private_directory = self.clone / "private-empty-directory"
        private_directory.mkdir(mode=0o710)
        self.clone.chmod(0o750)
        local_before = self.snapshot(self.clone)
        baseline_before = self.baseline_snapshot()
        self.replace(self.source, "Original farewell", "Upstream farewell")
        (self.source / "helper.sh").chmod(0o644)
        (self.source / "README.md").unlink()
        self.write(self.source, "new.bin", b"\x00upstream\xfe")
        self.apply(self.prepare())
        self.assertNotEqual(local_before, self.snapshot(self.clone))
        self.cli("undo", test_cli.CLONE_ID)
        self.assertEqual(local_before, self.snapshot(self.clone))
        self.assertEqual(baseline_before, self.baseline_snapshot())
        self.assertEqual(self.clone.stat().st_mode & 0o777, 0o750)
        self.assertEqual(private_directory.stat().st_mode & 0o777, 0o710)

    def test_undo_refuses_to_discard_edits_made_after_apply(self):
        self.apply(self.clean_update())
        self.write(self.clone, "later.txt", "An edit after the reviewed application.\n")
        local_before = self.snapshot(self.clone)
        baseline_before = self.baseline_snapshot()
        self.cli("undo", test_cli.CLONE_ID, success=False)
        self.assertEqual(local_before, self.snapshot(self.clone))
        self.assertEqual(baseline_before, self.baseline_snapshot())

    def test_apply_refuses_clone_changed_since_prepare(self):
        plan = self.clean_update()
        self.replace(self.clone, "Original greeting", "A late local edit")
        self.assert_apply_refused_without_changing_install(plan)

    def test_apply_refuses_upstream_changed_since_prepare(self):
        plan = self.clean_update()
        self.replace(self.source, "Original greeting", "A late upstream edit")
        self.assert_apply_refused_without_changing_install(plan)

    def test_apply_refuses_baseline_changed_since_prepare(self):
        plan = self.clean_update()
        baseline = self.state / "clones" / test_cli.CLONE_ID / "baseline" / "README.md"
        baseline.write_text("Tampered baseline\n")
        self.assert_apply_refused_without_changing_install(plan)

    def test_apply_refuses_hash_other_than_reviewed_candidate(self):
        plan = self.clean_update()
        wrong = dict(plan, candidateHash="0" * 64)
        self.assert_apply_refused_without_changing_install(wrong)

    def test_candidate_edit_after_review_requires_a_new_reviewed_hash(self):
        plan = self.clean_update()
        candidate = Path(plan["editablePath"]) / "BarWidget.qml"
        candidate.write_text(candidate.read_text().replace("Original greeting", "Edited candidate"))
        self.assert_apply_refused_without_changing_install(plan)
        reviewed = self.review(plan)
        self.assertNotEqual(plan["candidateHash"], reviewed["candidateHash"])
        self.apply(reviewed)
        self.assertIn("Edited candidate", (self.clone / "BarWidget.qml").read_text())

    def test_apply_refuses_pre_resolution_hash_after_candidate_changes(self):
        self.track()
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.source, "Original greeting", "Upstream greeting")
        original = self.prepare()
        self.assertTrue(original["hasConflicts"])
        self.cli("resolve", original["planId"], "--path", "BarWidget.qml",
                 "--take", "upstream", readonly=True)
        resolved = self.review(original)
        self.assertNotEqual(original["candidateHash"], resolved["candidateHash"])
        self.assertFalse(resolved["hasConflicts"])
        self.assert_apply_refused_without_changing_install(original)
        self.apply(resolved)
        self.assertIn("Upstream greeting", (self.clone / "BarWidget.qml").read_text())

    def test_unresolved_conflicts_cannot_be_applied(self):
        self.track()
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.source, "Original greeting", "Upstream greeting")
        plan = self.prepare()
        self.assertTrue(plan["hasConflicts"])
        self.assertTrue(any(item["path"] == "BarWidget.qml" for item in plan["conflicts"]))
        self.assert_apply_refused_without_changing_install(plan)

    def test_binary_conflicts_require_explicit_choice_for_each_file(self):
        choices = ("local", "upstream", "base", "delete")
        for choice in choices:
            for directory in (self.source, self.clone):
                self.write(directory, choice + ".bin", b"\x00base\xff")
        self.track()
        for choice in choices:
            self.write(self.clone, choice + ".bin", b"\x00local\xfe")
            self.write(self.source, choice + ".bin", b"\x00upstream\xfd")
        plan = self.prepare()
        self.assertTrue(plan["hasConflicts"])
        for choice in choices:
            self.cli("resolve", plan["planId"], "--path", choice + ".bin",
                     "--take", choice, readonly=True)
        resolved = self.review(plan)
        self.assertFalse(resolved["hasConflicts"])
        self.apply(resolved)
        self.assertEqual((self.clone / "local.bin").read_bytes(), b"\x00local\xfe")
        self.assertEqual((self.clone / "upstream.bin").read_bytes(), b"\x00upstream\xfd")
        self.assertEqual((self.clone / "base.bin").read_bytes(), b"\x00base\xff")
        self.assertFalse((self.clone / "delete.bin").exists())

    def test_modify_delete_conflicts_can_be_resolved_without_resurrecting_other_files(self):
        for name in ("keep.txt", "remove.txt"):
            for directory in (self.source, self.clone):
                self.write(directory, name, "original\n")
        self.track()
        self.write(self.clone, "keep.txt", "personal content\n")
        (self.source / "keep.txt").unlink()
        (self.clone / "remove.txt").unlink()
        self.write(self.source, "remove.txt", "upstream content\n")
        plan = self.prepare()
        self.assertTrue(plan["hasConflicts"])
        self.cli("resolve", plan["planId"], "--path", "keep.txt", "--take", "local",
                 readonly=True)
        self.cli("resolve", plan["planId"], "--path", "remove.txt", "--take", "delete",
                 readonly=True)
        resolved = self.review(plan)
        self.assertFalse(resolved["hasConflicts"])
        self.apply(resolved)
        self.assertEqual((self.clone / "keep.txt").read_text(), "personal content\n")
        self.assertFalse((self.clone / "remove.txt").exists())

    def test_file_directory_resolution_selects_the_complete_upstream_family(self):
        for directory in (self.source, self.clone):
            self.write(directory, "settings", "original settings\n")
        self.track()
        self.write(self.clone, "settings", "personal settings\n")
        (self.source / "settings").unlink()
        self.write(self.source, "settings/options.json", '{"clock": true}\n')
        plan = self.prepare()
        self.assertTrue(plan["hasConflicts"])
        self.cli("resolve", plan["planId"], "--path", "settings", "--take",
                 "upstream", readonly=True)
        resolved = self.review(plan)
        self.assertFalse(resolved["hasConflicts"])
        self.assertTrue(resolved["validation"]["valid"])
        self.apply(resolved)
        self.assertTrue((self.clone / "settings").is_dir())
        self.assertEqual((self.clone / "settings/options.json").read_text(),
                         '{"clock": true}\n')

    def test_manual_resolution_with_merge_markers_remains_unapplicable(self):
        self.track()
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.source, "Original greeting", "Upstream greeting")
        plan = self.prepare()
        manual = self.root / "manual.qml"
        manual.write_text("<<<<<<< local\nlocal\n=======\nupstream\n>>>>>>> upstream\n")
        # Rejecting the resolution itself or retaining it as invalid are both
        # safe; in neither case can the installed plugin receive merge markers.
        self.cli("resolve", plan["planId"], "--path", "BarWidget.qml", "--file",
                 manual, success=None, readonly=True)
        unresolved = self.review(plan)
        self.assertTrue(unresolved["hasConflicts"] or not unresolved["validation"]["valid"])
        self.assert_apply_refused_without_changing_install(unresolved)

    def test_manual_clean_resolution_applies_exact_reviewed_file(self):
        self.track()
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.source, "Original greeting", "Upstream greeting")
        plan = self.prepare()
        manual = self.root / "manual.qml"
        resolved_text = self.fixture.base_qml().replace("Original greeting", "Combined greeting")
        manual.write_text(resolved_text)
        self.cli("resolve", plan["planId"], "--path", "BarWidget.qml", "--file",
                 manual, readonly=True)
        resolved = self.review(plan)
        self.assertFalse(resolved["hasConflicts"])
        self.apply(resolved)
        self.assertEqual((self.clone / "BarWidget.qml").read_text(), resolved_text)

    def test_apply_refuses_git_checkout_without_losing_git_metadata(self):
        self.track()
        self.write(self.clone, ".git/HEAD", "ref: refs/heads/my-clock\n")
        self.write(self.clone, ".git/config", "[core]\n\trepositoryformatversion = 0\n")
        self.replace(self.source, "Original farewell", "Upstream farewell")
        plan = self.prepare()
        self.assert_apply_refused_without_changing_install(plan)
        self.assertEqual((self.clone / ".git/HEAD").read_text(), "ref: refs/heads/my-clock\n")

    def test_history_records_apply_and_undo(self):
        plan = self.clean_update()
        self.apply(plan)
        history = self.cli("history", test_cli.CLONE_ID, readonly=True)
        self.assertEqual(history["cloneId"], test_cli.CLONE_ID)
        self.assertIsNone(history["pendingTransaction"])
        self.assertTrue(any(row["planId"] == plan["planId"] for row in history["transactions"]))
        self.cli("undo", test_cli.CLONE_ID)
        after_undo = self.cli("history", test_cli.CLONE_ID, readonly=True)
        self.assertIsNone(after_undo["pendingTransaction"])
        self.assertNotEqual(history["transactions"], after_undo["transactions"])

    def test_recover_with_no_pending_transaction_preserves_installed_state(self):
        self.apply(self.clean_update())
        local_before = self.snapshot(self.plugins)
        baseline_before = self.baseline_snapshot()
        self.cli("recover", test_cli.CLONE_ID)
        self.assertEqual(local_before, self.snapshot(self.plugins))
        self.assertEqual(baseline_before, self.baseline_snapshot())

    def test_clone_creation_is_tracked_and_duplicate_is_refused(self):
        fresh_id = "neil.fresh-clock"
        created = self.cli("clone", test_cli.SOURCE_ID, "--id", fresh_id)
        fresh = self.plugins / fresh_id
        self.assertTrue(fresh.is_dir())
        manifest = json.loads((fresh / "manifest.json").read_text())
        self.assertEqual(manifest["id"], fresh_id)
        self.assertEqual(manifest["omarchy"]["clonedFrom"], test_cli.SOURCE_ID)
        self.assertEqual((fresh / "BarWidget.qml").read_bytes(),
                         (self.source / "BarWidget.qml").read_bytes())
        status = self.cli("status", readonly=True)
        row = next(row for row in status["clones"] if row["id"] == fresh_id)
        self.assertTrue(row["tracked"])
        local_before, state_before = self.snapshot(self.plugins), self.snapshot(self.state)
        self.cli("clone", test_cli.SOURCE_ID, "--id", fresh_id, success=False)
        self.assertEqual(local_before, self.snapshot(self.plugins))
        self.assertEqual(state_before, self.snapshot(self.state))

    def test_clone_creation_refuses_preexisting_baseline_without_leaving_clone(self):
        fresh_id = "neil.fresh-clock"
        orphan = self.state / "clones" / fresh_id
        self.write(orphan, "record.json", '{"existing": "must survive"}\n')
        orphan_before, local_before = self.snapshot(orphan), self.snapshot(self.plugins)
        self.cli("clone", test_cli.SOURCE_ID, "--id", fresh_id, success=False)
        self.assertEqual(orphan_before, self.snapshot(orphan))
        self.assertEqual(local_before, self.snapshot(self.plugins))
        self.assertFalse((self.plugins / fresh_id).exists())

    def test_resolve_cannot_escape_the_candidate_directory(self):
        plan = self.clean_update()
        outside = self.root / "outside.txt"
        outside.write_text("keep this\n")
        self.cli("resolve", plan["planId"], "--path", "../outside.txt", "--take",
                 "delete", success=False, readonly=True)
        self.assertEqual(outside.read_text(), "keep this\n")


if __name__ == "__main__":
    unittest.main()
