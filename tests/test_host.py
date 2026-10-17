"""Warn about shell components a clone uses that a newer Omarchy removed."""

import json
from pathlib import Path
import shutil
import unittest

from clone_keeper import host
from clone_keeper.core import File
from tests import test_cli
from tests.test_workflow import WorkflowFixture

PANEL = "import QtQuick\nItem {\n  property bool manageIpc: true\n  property string ipcTarget: \"\"\n  function open() {}\n}\n"


class WarningTests(unittest.TestCase):
    before = {"Ui.Panel": ["ipcTarget", "manageIpc", "open"], "Commons.Style": ["font"]}

    def tree(self, text):
        return {"Panel.qml": File(text.encode(), 0o644)}

    def test_reports_removed_member_the_clone_uses(self):
        clone = self.tree("import qs.Ui as Ui\nUi.Panel {\n  manageIpc: false\n}\n")
        after = {"Ui.Panel": ["ipcTarget", "open"], "Commons.Style": ["font"]}
        self.assertEqual(host.warnings(clone, self.before, after), ["Ui.Panel no longer declares manageIpc."])

    def test_reports_removed_component(self):
        clone = self.tree("import qs.Commons\nItem { width: Style.font.body }\n")
        self.assertEqual(host.warnings(clone, self.before, {"Ui.Panel": self.before["Ui.Panel"]}),
                         ["Commons.Style is no longer part of the Omarchy shell."])

    def test_ignores_unused_components_and_mere_words(self):
        clone = self.tree("import qs.Ui as Ui\nUi.Panel {\n  // remember to open the manageIpc docs\n}\n")
        self.assertEqual(host.warnings(clone, self.before, {"Ui.Panel": [], "Commons.Style": []}), [])

    def test_baseline_release(self):
        self.assertEqual(host.baseline_release({"provenance": "created-from-source", "baselineVersion": "4.0.4-1"}), "4.0.4-1")
        self.assertEqual(host.baseline_release({"provenance": "historical-release-source",
                                                "evidence": {"versions": ["4.0.3-1", "4.0.4-1"]}}), "4.0.4-1")
        self.assertIsNone(host.baseline_release({"provenance": "user-supplied-original", "baselineVersion": "x"}))


class StatusTests(WorkflowFixture):
    def setUp(self):
        super().setUp()
        (self.root / "empty.log").write_text("")
        (self.root / "pkg").mkdir()
        self.panel = self.omarchy / "shell" / "Ui" / "Panel.qml"
        self.panel.parent.mkdir(parents=True)
        self.panel.write_text(PANEL)
        self.replace(self.clone, "import QtQuick", "import QtQuick\nimport qs.Ui as Ui")
        self.replace(self.clone, "Item {", "Ui.Panel {\n    manageIpc: false")
        self.replace(self.source, "import QtQuick", "import QtQuick\nimport qs.Ui as Ui")
        self.replace(self.source, "Item {", "Ui.Panel {\n    manageIpc: false")

    def sync(self):
        return self.cli("--package-cache", self.root / "pkg", "--pacman-log", self.root / "empty.log", "sync")

    def test_status_warns_after_an_upgrade_removes_a_used_member(self):
        self.assertEqual(self.sync()["tracked"], [test_cli.CLONE_ID])
        self.assertEqual(self.cli("status")["clones"][0]["hostWarnings"], [])
        (self.omarchy / "version").write_text("4.0.2\n")
        self.panel.write_text(PANEL.replace("  property bool manageIpc: true\n", ""))
        self.assertEqual(self.cli("status")["clones"][0]["hostWarnings"], ["Ui.Panel no longer declares manageIpc."])


@unittest.skipUnless(Path("/usr/share/omarchy/bin/omarchy-plugin-validate").is_file(), "Omarchy validator not installed")
class ValidatorTests(WorkflowFixture):
    """Reviews use the matching Omarchy's own manifest validator."""

    def setUp(self):
        super().setUp()
        (self.omarchy / "bin").mkdir()
        shutil.copy("/usr/share/omarchy/bin/omarchy-plugin-validate", self.omarchy / "bin")
        self.track()

    def section(self, value):
        manifest = json.loads((self.clone / "manifest.json").read_text())
        manifest["barWidget"]["defaultSection"] = value
        (self.clone / "manifest.json").write_text(json.dumps(manifest))
        self.replace(self.source, "Original farewell", "Upstream farewell")
        return self.prepare()["validation"]

    def test_accepts_sections_the_installed_omarchy_accepts(self):
        self.assertTrue(self.section("center")["valid"])

    def test_reports_the_installed_validators_refusal(self):
        validation = self.section("bogus")
        self.assertFalse(validation["valid"])
        self.assertTrue(any("defaultSection" in error for error in validation["errors"]), validation)


class SectionTests(WorkflowFixture):
    """Without a validator, sections newer Omarchy releases accept are not refused."""

    def test_accepts_newer_bar_sections(self):
        self.track()
        manifest = json.loads((self.clone / "manifest.json").read_text())
        manifest["barWidget"]["defaultSection"] = "centerEnd"
        (self.clone / "manifest.json").write_text(json.dumps(manifest))
        self.replace(self.source, "Original farewell", "Upstream farewell")
        self.assertTrue(self.prepare()["validation"]["valid"])


if __name__ == "__main__":
    unittest.main()
