"""Crash and editor-race regressions using disposable roots exclusively."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from clone_keeper import cli, plans, transactions
from clone_keeper.core import KeeperError, write_tree
from tests import test_atomic, test_cli


@contextmanager
def fixture():
    case = test_atomic.AtomicTransactionTests(methodName="runTest")
    case.setUp()
    try:
        yield case
    finally:
        case.doCleanups()


class StagingRecoveryTests(unittest.TestCase):
    def test_process_death_mid_copy_does_not_poison_apply_or_undo_recovery(self):
        for undo in (False, True):
            for suffix in ("clone", "state"):
                with self.subTest(undo=undo, suffix=suffix), fixture() as case:
                    if undo:
                        case.apply()
                        record = case.transactions.history(case.ident)["transactions"][-1]
                        # Recovery must also reconstruct a missing staging tree.
                        for path in case.transactions._paths(record)[1:]:
                            shutil.rmtree(path)
                    original = transactions._write_snapshot

                    def interrupted(destination, tree, directories):
                        if destination.name == suffix:
                            destination.mkdir()
                            write_tree(destination, dict(list(tree.items())[:1]))
                            os._exit(77)  # No context-manager cleanup, like process loss.
                        original(destination, tree, directories)

                    child = os.fork()
                    if child == 0:
                        try:
                            with patch.object(transactions, "_write_snapshot", side_effect=interrupted):
                                if undo:
                                    case.transactions.undo(case.ident)
                                else:
                                    case.apply()
                        finally:
                            os._exit(1)
                    _, status = os.waitpid(child, 0)
                    self.assertEqual(os.waitstatus_to_exitcode(status), 77)
                    history = case.transactions.history(case.ident)
                    self.assertIsNotNone(history["pendingTransaction"])
                    result = case.transactions.recover(case.ident)
                    self.assertEqual(result["status"], "undone" if undo else "applied")
                    record = case.transactions.history(case.ident)["transactions"][-1]
                    prefix = "before" if undo else "after"
                    for live, name in ((case.clone, "clone"), (case.keeper.state_path(case.ident), "state")):
                        self.assertEqual(transactions._snapshot(live)[2], record["snapshots"][f"{prefix}-{name}"])

    def test_replacing_an_existing_stage_never_exposes_a_partial_tree(self):
        with fixture() as case:
            case.apply()
            record = case.transactions.history(case.ident)["transactions"][-1]
            stage = case.transactions._paths(record)[1]
            before = transactions._snapshot(stage)

            def interrupted(destination, tree, directories):
                destination.mkdir()
                write_tree(destination, dict(list(tree.items())[:1]))
                raise RuntimeError("interrupted copy")

            with patch.object(transactions, "_write_snapshot", side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError, "interrupted copy"):
                    case.transactions._stage(stage, record, "after-clone", "before-clone")
            self.assertEqual(transactions._snapshot(stage), before)
            case.transactions._stage(stage, record, "after-clone", "before-clone")
            self.assertEqual(transactions._snapshot(stage)[2], record["snapshots"]["after-clone"])
            self.assertTrue(any(transactions._snapshot(path)[2] == before[2]
                                for path in stage.parent.glob(".stage-*/clone")))


class CandidateEditorTests(unittest.TestCase):
    def test_edit_during_resolution_preparation_is_preserved(self):
        with fixture() as case:
            live = Path(case.review["editablePath"])
            resolutions = (live.parent / "resolutions.json").read_bytes()

            def edited(stage, tree):
                write_tree(stage, tree)
                (live / "run.sh").write_bytes(b"manual edit\n")

            with patch.object(plans, "write_tree", side_effect=edited):
                with self.assertRaisesRegex(KeeperError, "Candidate changed"):
                    case.plans.resolve(case.review["planId"], "Clock.qml", take="local")
            self.assertEqual((live / "run.sh").read_bytes(), b"manual edit\n")
            self.assertEqual((live.parent / "resolutions.json").read_bytes(), resolutions)

    def test_exchange_race_preserves_both_versions_and_does_not_accept_resolution(self):
        for edit_new_tree in (False, True):
            with self.subTest(edit_new_tree=edit_new_tree), fixture() as case:
                live = Path(case.review["editablePath"])
                resolutions = (live.parent / "resolutions.json").read_bytes()
                original = transactions.exchange_directories
                calls = []

                def edited(stage, candidate):
                    if not calls:
                        (candidate / "run.sh").write_bytes(b"old-tree editor save\n")
                    original(stage, candidate)
                    if not calls and edit_new_tree:
                        (candidate / "run.sh").write_bytes(b"new-tree editor save\n")
                    calls.append(stage)

                with patch.object(transactions, "exchange_directories", side_effect=edited):
                    with self.assertRaisesRegex(KeeperError, "Candidate changed during resolution"):
                        case.plans.resolve(case.review["planId"], "Clock.qml", take="local")
                copies = [(live / "run.sh").read_bytes(), (calls[0] / "run.sh").read_bytes()]
                self.assertIn(b"old-tree editor save\n", copies)
                if edit_new_tree:
                    self.assertIn(b"new-tree editor save\n", copies)
                else:
                    self.assertEqual(copies[0], b"old-tree editor save\n")
                self.assertEqual((live.parent / "resolutions.json").read_bytes(), resolutions)

    def test_editor_with_open_file_can_save_after_successful_resolution(self):
        with fixture() as case:
            live = Path(case.review["editablePath"])
            with (live / "run.sh").open("r+b") as editor:
                case.plans.resolve(case.review["planId"], "Clock.qml", take="edited")
                editor.seek(0)
                editor.write(b"late editor save\n")
                editor.truncate()
            backups = list(live.parent.glob(".resolution-*/candidate/run.sh"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"late editor save\n")
            case.plans.prepare(case.ident)
            self.assertEqual(backups[0].read_bytes(), b"late editor save\n",
                             "Duplicate cleanup must preserve a previously resolved plan")


class DetachedRootTests(unittest.TestCase):
    def test_overlapping_roots_are_rejected_before_starting_worker_or_writing(self):
        with fixture() as case:
            for state in (case.clone, case.source / "state", case.root):
                with self.subTest(state=state):
                    before = test_cli.CloneKeeperIntegrationTests.snapshot(case.root)
                    output, error = io.StringIO(), io.StringIO()
                    with patch("clone_keeper.operations.os.fork") as fork, \
                            redirect_stdout(output), redirect_stderr(error):
                        result = cli.main(["--omarchy-path", str(case.omarchy),
                                           "--plugins-dir", str(case.plugins), "--state-dir", str(state),
                                           "recover", case.ident, "--detach", "--json"])
                    self.assertEqual(result, 1)
                    self.assertIn("must be separate trees", json.loads(output.getvalue())["error"])
                    fork.assert_not_called()
                    self.assertEqual(test_cli.CloneKeeperIntegrationTests.snapshot(case.root), before)
