"""Regressions for filesystem snapshots and reviewable merge candidates."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clone_keeper.core import (
    File, Keeper, KeeperError, Source, file_diff, read_tree, tree_hash,
)


class SnapshotReviewTests(unittest.TestCase):
    @unittest.skipIf(os.geteuid() == 0, "root can read mode-000 directories")
    def test_unreadable_subdirectory_rejects_snapshot_instead_of_omitting_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hidden = root / "helpers"
            hidden.mkdir()
            (hidden / "required.js").write_text("export const value = 1;\n")
            (root / "visible.txt").write_text("visible\n")
            hidden.chmod(0)
            try:
                with self.assertRaises(OSError):
                    read_tree(root)
            finally:
                hidden.chmod(0o700)

    def test_binary_permission_change_is_visible_even_when_content_is_identical(self):
        before = File(b"\x00asset\xff", 0o644)
        after = File(before.data, 0o755)
        diff = file_diff("asset.bin", before, after)
        self.assertIn("mode 0644 -> 0755", diff)
        self.assertNotIn("Binary content differs", diff)

    def test_flat_source_materializes_sibling_directory_and_rewrites_imports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            widgets = root / "bar" / "widgets"
            indicators = root / "bar" / "indicators"
            widgets.mkdir(parents=True)
            indicators.mkdir()
            source_manifest = widgets / "Indicators.manifest.json"
            source_manifest.write_text(json.dumps({
                "schemaVersion": 1,
                "id": "omarchy.indicators",
                "name": "Indicators",
                "version": "1.0.0",
                "kinds": ["bar-widget"],
                "entryPoints": {"barWidget": "Indicators.qml"},
                "omarchy": {"clonePaths": [
                    {"source": "../indicators", "target": "indicators"},
                ]},
            }))
            (widgets / "Indicators.qml").write_text(
                'import "../indicators"\nItem {}\n')
            (indicators / "Counter.qml").write_text("Item {}\n")
            binary = b"\x00keep these bytes\xff"
            (indicators / "asset.bin").write_bytes(binary)
            helper = indicators / "helper.sh"
            helper.write_text("#!/bin/sh\nprintf ready\n")
            helper.chmod(0o755)

            materialized = Source(source_manifest, root).materialize()

            self.assertEqual(set(materialized), {
                "manifest.json", "Indicators.qml", "indicators/Counter.qml",
                "indicators/asset.bin", "indicators/helper.sh",
            })
            self.assertEqual(materialized["Indicators.qml"].data,
                             b'import "indicators"\nItem {}\n')
            self.assertEqual(materialized["indicators/asset.bin"].data, binary)
            self.assertEqual(materialized["indicators/helper.sh"].mode, 0o755)
            manifest = json.loads(materialized["manifest.json"].data)
            self.assertEqual(manifest["id"], "omarchy.indicators")
            self.assertNotIn("clonePaths", manifest["omarchy"])


class ExportReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="clone-keeper-review-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.omarchy = self.root / "omarchy"
        self.source = self.omarchy / "shell" / "plugins" / "clock"
        self.plugins = self.root / "plugins"
        self.clone_id = "neil.clock"
        self.clone = self.plugins / self.clone_id
        self.state = self.root / "state"
        self.source.mkdir(parents=True)
        self.clone.mkdir(parents=True)
        (self.omarchy / "version").write_text("4.0.1\n")
        self.source_manifest = {
            "schemaVersion": 1,
            "id": "omarchy.clock",
            "name": "Clock",
            "version": "1.0.0",
            "description": "Original description",
            "kinds": ["bar-widget"],
            "entryPoints": {"barWidget": "Clock.qml"},
            "barWidget": {"displayName": "Clock"},
        }
        self.clone_manifest = {
            **self.source_manifest,
            "id": self.clone_id,
            "name": "My Clock",
            "barWidget": {"displayName": "My Clock"},
            "omarchy": {"clonedFrom": "omarchy.clock"},
        }
        for directory, manifest in ((self.source, self.source_manifest),
                                    (self.clone, self.clone_manifest)):
            self.write_manifest(directory, manifest)
            (directory / "Clock.qml").write_text("import QtQuick\nItem {}\n")
        self.keeper = Keeper(self.omarchy, self.plugins, self.state)
        self.keeper.track(self.clone_id)

    @staticmethod
    def write_manifest(directory, manifest):
        (directory / "manifest.json").write_text(json.dumps(manifest) + "\n")

    def test_export_rejects_upstream_change_after_candidate_was_computed(self):
        original_preview = self.keeper.preview

        def change_source_after_preview(ident):
            report, candidate = original_preview(ident)
            (self.source / "Clock.qml").write_text(
                "import QtQuick\nItem { property int newUpstreamValue: 42 }\n")
            return report, candidate

        output = self.root / "candidate"
        clone_before = read_tree(self.clone)
        with patch.object(self.keeper, "preview", side_effect=change_source_after_preview):
            with self.assertRaises(KeeperError):
                self.keeper.export(self.clone_id, output)
        self.assertFalse(output.exists())
        self.assertEqual(read_tree(self.clone), clone_before)

    def test_identity_renamed_after_tracking_survives_upstream_update(self):
        meta_before, base_before = self.keeper.load_baseline(self.clone_id)
        self.clone_manifest["name"] = "My Renamed Clock"
        self.clone_manifest["barWidget"]["displayName"] = "Personal Time"
        self.write_manifest(self.clone, self.clone_manifest)
        self.source_manifest["name"] = "New Upstream Clock Name"
        self.source_manifest["barWidget"]["displayName"] = "New Upstream Label"
        self.source_manifest["description"] = "Updated upstream description"
        self.write_manifest(self.source, self.source_manifest)
        output = self.root / "candidate"

        self.keeper.export(self.clone_id, output)

        candidate_manifest = json.loads((output / "manifest.json").read_bytes())
        self.assertEqual(candidate_manifest["id"], self.clone_id)
        self.assertEqual(candidate_manifest["name"], "My Renamed Clock")
        self.assertEqual(candidate_manifest["barWidget"]["displayName"], "Personal Time")
        self.assertEqual(candidate_manifest["omarchy"]["clonedFrom"], "omarchy.clock")
        self.assertEqual(candidate_manifest["description"], "Updated upstream description")
        meta_after, base_after = self.keeper.load_baseline(self.clone_id)
        self.assertEqual(meta_after, meta_before)
        self.assertEqual(tree_hash(base_after), tree_hash(base_before))


if __name__ == "__main__":
    unittest.main()
