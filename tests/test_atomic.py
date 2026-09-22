"""Crash/recovery tests operate exclusively on disposable fixture directories."""
import json
import errno
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clone_keeper.core import File, Keeper, KeeperError, atomic_new_tree, read_tree, tree_hash
from clone_keeper.plans import Plans
from clone_keeper.transactions import Transactions, _snapshot, publish_directory


@unittest.skipUnless(shutil.which("git"), "git is required for text merging")
class AtomicTransactionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="clone-keeper-atomic-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.omarchy = self.root / "omarchy"
        self.source = self.omarchy / "shell" / "plugins" / "clock"
        self.plugins = self.root / "plugins"
        self.ident = "test.clock"
        self.clone = self.plugins / self.ident
        self.state = self.root / "state"
        self.source.mkdir(parents=True)
        self.clone.mkdir(parents=True)
        (self.omarchy / "version").write_text("4.0.1\n")
        source_manifest = {"schemaVersion": 1, "id": "omarchy.clock", "name": "Clock",
                           "version": "1.0.0", "kinds": ["bar-widget"],
                           "entryPoints": {"barWidget": "Clock.qml"}}
        clone_manifest = {**source_manifest, "id": self.ident, "name": "My Clock",
                          "omarchy": {"clonedFrom": "omarchy.clock"}}
        # Deliberately noncanonical bytes and an empty private directory prove
        # undo restores the actual former tree rather than the normalized plan.
        (self.source / "manifest.json").write_text(json.dumps(source_manifest))
        (self.clone / "manifest.json").write_text(json.dumps(clone_manifest, indent=4))
        original = ('import QtQuick\nItem {\n    property string greeting: "first"\n'
                    + "\n".join(f"    property int padding{index}: {index}" for index in range(12))
                    + '\n    property string farewell: "last"\n}\n')
        for root in (self.clone, self.source):
            (root / "Clock.qml").write_text(original)
            (root / "run.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
            (root / "run.sh").chmod(0o755)
        self.keeper = Keeper(self.omarchy, self.plugins, self.state)
        self.keeper.track(self.ident)
        (self.clone / "empty-private").mkdir(mode=0o700)
        (self.clone / "Clock.qml").write_text(original.replace("first", "my first"))
        (self.source / "Clock.qml").write_text(original.replace("last", "new upstream last"))
        self.plans = Plans(self.keeper)
        self.review = self.plans.prepare(self.ident)
        self.transactions = Transactions(self.keeper)
        self.before_clone = _snapshot(self.clone)
        self.before_state = _snapshot(self.keeper.state_path(self.ident))

    def apply(self):
        return self.transactions.apply(self.review["planId"], self.review["candidateHash"], self.review["planHash"])

    def test_apply_advances_only_upstream_and_undo_restores_exact_previous_trees(self):
        self.assertEqual(self.apply()["status"], "applied")
        _, trees = self.plans.load(self.review["planId"])
        _, baseline = self.keeper.load_baseline(self.ident)
        self.assertEqual(baseline, trees["upstream"])
        self.assertNotEqual(baseline, trees["candidate"])
        self.assertIn(b"my first", (self.clone / "Clock.qml").read_bytes())
        self.assertEqual(self.transactions.undo(self.ident)["status"], "undone")
        self.assertEqual(_snapshot(self.clone), self.before_clone)
        self.assertEqual(_snapshot(self.keeper.state_path(self.ident)), self.before_state)

    def test_recovery_finishes_each_interrupted_apply_boundary(self):
        for phase in ("prepared", "apply-clone-exchanged", "apply-clone", "apply-state-exchanged", "apply-state"):
            with self.subTest(phase=phase):
                def crash(name, record):
                    if name == phase:
                        raise RuntimeError("simulated process death")
                with patch.object(self.transactions, "_checkpoint", side_effect=crash):
                    with self.assertRaisesRegex(RuntimeError, "simulated"):
                        self.apply()
                self.assertIsNotNone(self.transactions.history(self.ident)["pendingTransaction"])
                with self.assertRaises(KeeperError):
                    self.plans.prepare(self.ident)
                self.assertEqual(self.transactions.recover(self.ident)["status"], "applied")
                self.assertIsNone(self.transactions.history(self.ident)["pendingTransaction"])
                _, baseline = self.keeper.load_baseline(self.ident)
                _, trees = self.plans.load(self.review["planId"])
                self.assertEqual(baseline, trees["upstream"])
                self.transactions.undo(self.ident)
                self.assertEqual(_snapshot(self.clone), self.before_clone)
                self.assertEqual(_snapshot(self.keeper.state_path(self.ident)), self.before_state)

    def test_recovery_finishes_each_interrupted_undo_boundary(self):
        for phase in ("undo-prepared", "undo-clone-exchanged", "undo-clone", "undo-state-exchanged", "undo-state"):
            with self.subTest(phase=phase):
                self.apply()
                def crash(name, record):
                    if name == phase:
                        raise RuntimeError("simulated process death")
                with patch.object(self.transactions, "_checkpoint", side_effect=crash):
                    with self.assertRaisesRegex(RuntimeError, "simulated"):
                        self.transactions.undo(self.ident)
                self.assertEqual(self.transactions.recover(self.ident)["status"], "undone")
                self.assertEqual(_snapshot(self.clone), self.before_clone)
                self.assertEqual(_snapshot(self.keeper.state_path(self.ident)), self.before_state)

    def test_recovery_refuses_unknown_live_edits_without_altering_either_tree(self):
        def crash(name, record):
            if name == "apply-clone-exchanged":
                raise RuntimeError("simulated process death")
        with patch.object(self.transactions, "_checkpoint", side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.apply()
        (self.clone / "new-user-file").write_text("preserve me\n")
        clone_before = _snapshot(self.clone)
        state_before = _snapshot(self.keeper.state_path(self.ident))
        with self.assertRaises(KeeperError):
            self.transactions.recover(self.ident)
        self.assertEqual(_snapshot(self.clone), clone_before)
        self.assertEqual(_snapshot(self.keeper.state_path(self.ident)), state_before)

    def test_recovery_refuses_changed_displaced_directory(self):
        def crash(name, record):
            if name == "apply-clone-exchanged":
                raise RuntimeError("simulated process death")
        with patch.object(self.transactions, "_checkpoint", side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.apply()
        transaction = self.transactions.history(self.ident)["transactions"][-1]
        _, displaced, _ = self.transactions._paths(transaction)
        (displaced / "late-editor-change").write_text("do not lose\n")
        with self.assertRaises(KeeperError):
            self.transactions.recover(self.ident)
        self.assertEqual((displaced / "late-editor-change").read_text(), "do not lose\n")

    def test_backup_tampering_blocks_undo_before_any_live_change(self):
        self.apply()
        transaction = self.transactions.history(self.ident)["transactions"][-1]
        directory = self.state / "transactions" / self.ident / transaction["id"]
        (directory / "before-clone" / "Clock.qml").write_text("tampered\n")
        current_clone = _snapshot(self.clone)
        current_state = _snapshot(self.keeper.state_path(self.ident))
        with self.assertRaises(KeeperError):
            self.transactions.undo(self.ident)
        self.assertIsNone(self.transactions.history(self.ident)["pendingTransaction"])
        self.assertEqual(_snapshot(self.clone), current_clone)
        self.assertEqual(_snapshot(self.keeper.state_path(self.ident)), current_state)

    def test_clean_recovery_is_a_successful_noop(self):
        result = self.transactions.recover(self.ident)
        self.assertEqual(result["status"], "clean")
        self.assertFalse(result["reloadRequired"])
        self.assertEqual(_snapshot(self.clone), self.before_clone)
        self.assertEqual(_snapshot(self.keeper.state_path(self.ident)), self.before_state)

    def test_atomic_exchange_failure_before_commit_leaves_no_pending_transaction(self):
        with patch("clone_keeper.transactions._exchange", side_effect=KeeperError("unsupported filesystem")):
            with self.assertRaises(KeeperError):
                self.apply()
        self.assertEqual(self.transactions.history(self.ident)["transactions"], [])
        self.assertEqual(_snapshot(self.clone), self.before_clone)
        self.assertEqual(_snapshot(self.keeper.state_path(self.ident)), self.before_state)

    def test_source_change_during_staging_is_rejected_before_durable_intent(self):
        original_probe = self.transactions._probe_exchange
        def mutate_source(root, live):
            original_probe(root, live)
            (self.source / "Clock.qml").write_text("another new upstream revision\n")
        with patch.object(self.transactions, "_probe_exchange", side_effect=mutate_source):
            with self.assertRaises(KeeperError):
                self.apply()
        self.assertIsNone(self.transactions.history(self.ident)["pendingTransaction"])
        self.assertEqual(_snapshot(self.clone), self.before_clone)
        self.assertEqual(_snapshot(self.keeper.state_path(self.ident)), self.before_state)

    def test_apply_preserves_existing_private_directory_modes(self):
        self.apply()
        self.assertTrue((self.clone / "empty-private").is_dir())
        self.assertEqual((self.clone / "empty-private").stat().st_mode & 0o777, 0o700)

    def assert_metadata_refusal(self):
        clone_before = read_tree(self.clone)
        state_before = read_tree(self.state)
        with self.assertRaises(KeeperError):
            self.apply()
        self.assertEqual(read_tree(self.clone), clone_before)
        self.assertEqual(read_tree(self.state), state_before)
        self.assertIsNone(self.transactions.history(self.ident)["pendingTransaction"])

    def test_apply_refuses_hardlinks_without_changes_or_pending_intent(self):
        original = self.clone / "run.sh"
        outside_link = self.root / "shared-script"
        os.link(original, outside_link)
        self.assert_metadata_refusal()
        self.assertEqual(original.stat().st_ino, outside_link.stat().st_ino)
        self.assertEqual(original.stat().st_nlink, 2)

    def test_apply_refuses_special_file_modes_without_changes_or_pending_intent(self):
        original = self.clone / "run.sh"
        for mode in (0o4755, 0o2755, 0o1755):
            with self.subTest(mode=oct(mode)):
                original.chmod(mode)
                self.assert_metadata_refusal()
                self.assertEqual(original.stat().st_mode & 0o7777, mode)
        original.chmod(0o755)

    def test_apply_refuses_file_and_directory_xattrs_without_dropping_them(self):
        for target in (self.clone / "run.sh", self.clone / "empty-private", self.clone):
            with self.subTest(target=target):
                try:
                    os.setxattr(target, "user.clone-keeper-test", b"preserve")
                except OSError as exc:
                    if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                        self.skipTest("fixture filesystem does not support extended attributes")
                    raise
                try:
                    self.assert_metadata_refusal()
                    self.assertEqual(os.getxattr(target, "user.clone-keeper-test"), b"preserve")
                finally:
                    os.removexattr(target, "user.clone-keeper-test")

    def test_apply_refuses_reported_acl_metadata(self):
        original = os.listxattr
        def list_attributes(path, **kwargs):
            return ["system.posix_acl_access"] if Path(path) == self.clone / "run.sh" else original(path, **kwargs)
        with patch("clone_keeper.transactions.os.listxattr", side_effect=list_attributes):
            self.assert_metadata_refusal()

    def test_unsupported_xattr_filesystem_is_accepted_but_other_errors_fail_closed(self):
        with patch("clone_keeper.transactions.os.listxattr", side_effect=OSError(errno.ENOTSUP, "unsupported")):
            self.assertEqual(self.apply()["status"], "applied")
            self.transactions.undo(self.ident)
        clone_before, state_before = read_tree(self.clone), read_tree(self.state)
        with patch("clone_keeper.transactions.os.listxattr", side_effect=OSError(errno.EIO, "I/O failure")):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(read_tree(self.clone), clone_before)
        self.assertEqual(read_tree(self.state), state_before)
        self.assertIsNone(self.transactions.history(self.ident)["pendingTransaction"])

    def test_recovery_refuses_malformed_live_baseline_json_without_overwriting_it(self):
        def crash(name, record):
            if name == "apply-clone-exchanged":
                raise RuntimeError("simulated process death")
        with patch.object(self.transactions, "_checkpoint", side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.apply()
        record = self.keeper.state_path(self.ident) / "record.json"
        record.write_bytes(b"{malformed baseline")
        clone_before, state_before = _snapshot(self.clone), _snapshot(self.keeper.state_path(self.ident))
        with self.assertRaises(KeeperError):
            self.transactions.recover(self.ident)
        self.assertEqual(_snapshot(self.clone), clone_before)
        self.assertEqual(_snapshot(self.keeper.state_path(self.ident)), state_before)

    def test_recovery_rejects_malformed_journal_shapes_with_actionable_errors(self):
        def crash(name, record):
            if name == "prepared":
                raise RuntimeError("simulated process death")
        with patch.object(self.transactions, "_checkpoint", side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.apply()
        original = self.transactions.history(self.ident)["transactions"][-1]
        journal = self.state / "transactions" / self.ident / original["id"] / "record.json"
        for field, invalid in (("status", None), ("status", "unknown"),
                               ("snapshots", []), ("clonePath", []), ("createdAt", None)):
            with self.subTest(field=field, invalid=invalid):
                journal.write_text(json.dumps({**original, field: invalid}))
                journal_before = journal.read_bytes()
                with self.assertRaises(KeeperError):
                    self.transactions.recover(self.ident)
                self.assertEqual(journal.read_bytes(), journal_before)
                self.assertEqual(_snapshot(self.clone), self.before_clone)
                self.assertEqual(_snapshot(self.keeper.state_path(self.ident)), self.before_state)

    def test_new_directory_publication_never_replaces_an_existing_empty_directory(self):
        stage, destination = self.root / "stage", self.root / "destination"
        stage.mkdir()
        (stage / "payload").write_text("complete staged content\n")
        destination.mkdir()
        original_inode = destination.stat().st_ino
        with self.assertRaises(FileExistsError):
            publish_directory(stage, destination)
        self.assertEqual(destination.stat().st_ino, original_inode)
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual((stage / "payload").read_text(), "complete staged content\n")
        destination.rmdir()
        publish_directory(stage, destination)
        self.assertFalse(stage.exists())
        self.assertEqual((destination / "payload").read_text(), "complete staged content\n")

    def test_apply_refuses_pending_clone_creation_without_a_transaction(self):
        path = self.keeper.state_path(self.ident) / "record.json"
        record = json.loads(path.read_bytes())
        record["creationPending"] = True
        path.write_text(json.dumps(record))
        clone_before, state_before = _snapshot(self.clone), _snapshot(self.keeper.state_path(self.ident))
        with self.assertRaises(KeeperError):
            self.plans.prepare(self.ident)
        with self.assertRaisesRegex(KeeperError, "creation is unfinished"):
            self.apply()
        self.assertEqual(_snapshot(self.clone), clone_before)
        self.assertEqual(_snapshot(self.keeper.state_path(self.ident)), state_before)
        self.assertEqual(self.transactions.history(self.ident)["transactions"], [])


class AtomicPublicationTests(unittest.TestCase):
    def test_parent_ancestry_is_synced_before_publication_even_when_precreated(self):
        from clone_keeper import transactions
        with tempfile.TemporaryDirectory(prefix="clone-keeper-parents-") as temporary:
            root = Path(temporary)
            # The caller-created existing directory also needs its own parent
            # entry synced; only syncing newly-created descendants is inadequate.
            caller_created = root / "new-state"
            caller_created.mkdir()
            destination = caller_created / "new-clones" / "nested" / "test.clock"
            synced = []
            original_sync, original_publish = transactions._fsync_directory, transactions.publish_directory

            def sync(directory):
                synced.append(directory)
                original_sync(directory)

            def publish(stage, target):
                expected = [target.parent, *target.parent.parents]
                self.assertEqual(synced[:len(expected)], expected)
                self.assertIn(root, synced)
                self.assertFalse(target.exists())
                original_publish(stage, target)

            with patch.object(transactions, "_fsync_directory", side_effect=sync), \
                    patch.object(transactions, "publish_directory", side_effect=publish):
                atomic_new_tree(destination, {"payload": File(b"complete\n")})
            self.assertEqual((destination / "payload").read_bytes(), b"complete\n")

    def test_parent_fsync_failure_prevents_publication(self):
        from clone_keeper import transactions
        with tempfile.TemporaryDirectory(prefix="clone-keeper-parents-") as temporary:
            destination = Path(temporary) / "new-parent" / "test.clock"
            with patch.object(transactions, "_fsync_directory", side_effect=OSError(errno.EIO, "fsync failed")), \
                    patch.object(transactions, "publish_directory") as publish:
                with self.assertRaises(OSError):
                    atomic_new_tree(destination, {"payload": File(b"complete\n")})
                publish.assert_not_called()
            self.assertFalse(destination.exists())

    def test_existing_file_or_symlink_ancestor_is_preserved_and_refused(self):
        with tempfile.TemporaryDirectory(prefix="clone-keeper-parents-") as temporary:
            root = Path(temporary)
            obstruction = root / "file-parent"
            obstruction.write_text("keep this file")
            with self.assertRaises(KeeperError):
                atomic_new_tree(obstruction / "child" / "test.clock", {})
            self.assertEqual(obstruction.read_text(), "keep this file")
            actual = root / "actual"
            actual.mkdir()
            alias = root / "alias"
            alias.symlink_to(actual, target_is_directory=True)
            with self.assertRaises(KeeperError):
                atomic_new_tree(alias / "child" / "test.clock", {})
            self.assertEqual(list(actual.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
