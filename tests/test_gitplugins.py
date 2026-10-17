"""Update git-installed plugins that carry local edits, with review, undo and recovery."""

import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from clone_keeper.core import Keeper
from clone_keeper.service import Workflow
from tests.test_workflow import WorkflowFixture

PLUGIN = "acme.widget"
QML = "import QtQuick\nItem {\n    property string title: \"Widget\"\n" + "".join(
    f"    property int p{n}: {n}\n" for n in range(8)) + "    property string footer: \"End\"\n}\n"


def run(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd), "PATH": "/usr/bin:/bin"})


class GitFixture(WorkflowFixture):
    """A git-installed plugin cloned from a disposable origin repository."""

    def setUp(self):
        super().setUp()
        self.origin = self.root / "origin"
        self.origin.mkdir()
        run("init", "-q", "-b", "main", cwd=self.origin)
        manifest = {"schemaVersion": 1, "id": PLUGIN, "name": "Widget", "version": "1.0.0",
                    "kinds": ["bar-widget"], "entryPoints": {"barWidget": "BarWidget.qml"}}
        (self.origin / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (self.origin / "BarWidget.qml").write_text(QML)
        (self.origin / ".gitignore").write_text("cache/\n")
        run("add", ".", cwd=self.origin)
        run("commit", "-qm", "one", cwd=self.origin)
        self.plugin = self.plugins / PLUGIN
        run("clone", "-q", str(self.origin), str(self.plugin), cwd=self.root)
        (self.plugin / "cache").mkdir()
        (self.plugin / "cache" / "data.bin").write_bytes(b"generated")

    def edit(self, directory, old, new):
        path = directory / "BarWidget.qml"
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))

    def upstream(self, old, new):
        self.edit(self.origin, old, new)
        run("commit", "-qam", "two", cwd=self.origin)

    def head(self, directory=None):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=directory or self.plugin, capture_output=True,
                              text=True, check=True).stdout.strip()

    def row(self):
        return next((row for row in self.cli("status")["clones"] if row["id"] == PLUGIN), None)

    def fetch(self):
        return self.cli("fetch", PLUGIN)

    def prepare_git(self):
        return self.cli("prepare", PLUGIN, readonly=True)


class GitPluginTests(GitFixture):
    def test_unedited_plugin_is_left_to_omarchy_and_ignored_files_are_not_edits(self):
        self.assertIsNone(self.row())
        self.edit(self.plugin, '"Widget"', '"My widget"')
        row = self.row()
        self.assertEqual((row["kind"], row["status"], row["localChangedFiles"]), ("git", "local-only", 1))

    def test_update_keeps_edits_as_uncommitted_changes_and_undo_restores(self):
        self.edit(self.plugin, '"Widget"', '"My widget"')
        (self.plugin / "notes.txt").write_text("mine\n")
        before = self.head()
        self.upstream('"End"', '"Upstream end"')
        target = self.head(self.origin)
        self.assertEqual(self.row()["status"], "local-only")
        self.assertEqual(self.fetch()["status"], "diverged")
        plan = self.prepare_git()
        self.assertFalse(plan["hasConflicts"])
        self.assertTrue(plan["validation"]["valid"], plan["validation"])
        self.cli("apply", plan["planId"], "--expect", plan["candidateHash"])
        self.assertEqual(self.head(), target)
        text = (self.plugin / "BarWidget.qml").read_text()
        self.assertIn('"My widget"', text)
        self.assertIn('"Upstream end"', text)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.plugin, capture_output=True, text=True).stdout
        self.assertEqual(sorted(status.splitlines()), [" M BarWidget.qml", "?? notes.txt"])
        self.assertEqual((self.plugin / "cache" / "data.bin").read_bytes(), b"generated")
        self.assertEqual(self.row()["status"], "local-only")
        self.cli("undo", PLUGIN)
        self.assertEqual(self.head(), before)
        self.assertIn('"End"', (self.plugin / "BarWidget.qml").read_text())
        self.assertIn('"My widget"', (self.plugin / "BarWidget.qml").read_text())
        self.assertEqual((self.plugin / "notes.txt").read_text(), "mine\n")

    def test_overlapping_edit_is_resolved_per_conflict(self):
        self.edit(self.plugin, '"Widget"', '"My widget"')
        self.upstream('"Widget"', '"New widget"')
        self.fetch()
        plan = self.prepare_git()
        self.assertTrue(plan["hasConflicts"])
        plan = self.cli("resolve", plan["planId"], "--path", "BarWidget.qml", "--conflict", "0", "--take", "local",
                        readonly=True)
        self.cli("apply", plan["planId"], "--expect", plan["candidateHash"])
        self.assertIn('"My widget"', (self.plugin / "BarWidget.qml").read_text())

    def test_edits_after_review_make_the_plan_stale(self):
        self.edit(self.plugin, '"Widget"', '"My widget"')
        self.upstream('"End"', '"Upstream end"')
        self.fetch()
        plan = self.prepare_git()
        self.edit(self.plugin, '"My widget"', '"Changed again"')
        self.assertTrue(self.cli("review", plan["planId"], readonly=True)["stale"])
        self.cli("apply", plan["planId"], "--expect", plan["candidateHash"], success=False)
        self.assertIn('"Changed again"', (self.plugin / "BarWidget.qml").read_text())

    def test_local_commits_are_left_to_git(self):
        self.edit(self.plugin, '"Widget"', '"My widget"')
        run("commit", "-qam", "mine", cwd=self.plugin)
        self.edit(self.plugin, '"My widget"', '"Still mine"')
        self.upstream('"End"', '"Upstream end"')
        self.fetch()
        row = self.row()
        self.assertEqual(row["status"], "error")
        self.assertIn("local commits", row["message"])
        self.cli("prepare", PLUGIN, success=False, readonly=True)

    def test_hooks_in_the_plugin_repository_do_not_run(self):
        marker = self.root / "hook-ran"
        hook = self.plugin / ".git" / "hooks" / "reference-transaction"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
        self.edit(self.plugin, '"Widget"', '"My widget"')
        self.upstream('"End"', '"Upstream end"')
        self.fetch()
        plan = self.prepare_git()
        self.cli("apply", plan["planId"], "--expect", plan["candidateHash"])
        self.assertFalse(marker.exists())


class GitPruneTests(GitFixture):
    def test_prunes_older_updates_with_their_backups(self):
        self.edit(self.plugin, '"Widget"', '"My widget"')
        for number in (5, 6):
            self.upstream(f"p{number}: {number}", f"p{number}: {number + 10}")
            self.fetch()
            plan = self.prepare_git()
            self.cli("apply", plan["planId"], "--expect", plan["candidateHash"])
        history = self.cli("history", PLUGIN)["transactions"]
        stage = history[0]["stage"]
        listed = self.cli("prune", "--keep-transactions", "1", readonly=True)["items"]
        self.assertEqual([item["id"] for item in listed], [history[0]["id"]])
        self.cli("prune", "--keep-transactions", "1", "--yes")
        self.assertFalse(Path(stage).exists())
        self.cli("undo", PLUGIN)
        self.assertIn("p6: 6\n", (self.plugin / "BarWidget.qml").read_text())
        self.assertIn('"My widget"', (self.plugin / "BarWidget.qml").read_text())


class GitRecoveryTests(GitFixture):
    """Interruptions around the directory exchange are finished or abandoned."""

    def prepared_plan(self):
        self.edit(self.plugin, '"Widget"', '"My widget"')
        self.upstream('"End"', '"Upstream end"')
        self.fetch()
        work = Workflow(Keeper(self.omarchy, self.plugins, self.state))
        return work, self.prepare_git()

    def test_crash_before_exchange_is_abandoned(self):
        work, plan = self.prepared_plan()
        before = self.head()
        with mock.patch("clone_keeper.transactions.exchange_directories", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                work.apply(plan["planId"], plan["candidateHash"])
        self.assertEqual(self.row()["status"], "error")
        self.assertEqual(self.cli("recover", PLUGIN)["status"], "abandoned")
        self.assertEqual(self.head(), before)

    def test_crash_after_exchange_is_finished(self):
        work, plan = self.prepared_plan()
        original = work.git._persist
        def persist(record, status):
            if status == "applied":
                raise KeyboardInterrupt
            original(record, status)
        with mock.patch.object(work.git, "_persist", side_effect=persist):
            with self.assertRaises(KeyboardInterrupt):
                work.apply(plan["planId"], plan["candidateHash"])
        self.assertEqual(self.cli("recover", PLUGIN)["status"], "applied")
        self.assertIn('"Upstream end"', (self.plugin / "BarWidget.qml").read_text())
        self.cli("undo", PLUGIN)
        self.assertIn('"End"', (self.plugin / "BarWidget.qml").read_text())


if __name__ == "__main__":
    unittest.main()
