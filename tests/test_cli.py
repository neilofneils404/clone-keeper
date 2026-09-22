"""Exercise the public CLI against disposable upstream and clone directories.

These tests intentionally use only the command-line boundary.  Source and clone
snapshots ensure that planning and exporting never mutate installed plugins.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
CLI = PROJECT / "bin" / "clone-keeper"
SOURCE_ID = "omarchy.clock"
CLONE_ID = "neil.clock"


@unittest.skipUnless(shutil.which("git"), "git is required for merge integration tests")
class CloneKeeperIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="clone-keeper-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.omarchy = self.root / "omarchy"
        self.source = self.omarchy / "shell" / "plugins" / "panels" / "clock"
        self.plugins = self.root / "plugins"
        self.clone = self.plugins / CLONE_ID
        self.state = self.root / "state"
        self.source.mkdir(parents=True)
        self.plugins.mkdir()
        (self.omarchy / "version").write_text("4.0.1\n", encoding="utf-8")
        self.manifest = {
            "schemaVersion": 1,
            "id": SOURCE_ID,
            "name": "Clock",
            "version": "1.0.0",
            "author": "Omarchy",
            "description": "Date and time",
            "kinds": ["bar-widget"],
            "entryPoints": {"barWidget": "BarWidget.qml"},
            "barWidget": {"displayName": "Clock", "allowMultiple": False},
        }
        self.write_manifest(self.source, self.manifest)
        self.write(self.source, "BarWidget.qml", self.base_qml())
        self.write(self.source, "README.md", "Original clock documentation.\n")
        shutil.copytree(self.source, self.clone)
        cloned_manifest = dict(self.manifest)
        cloned_manifest.update(id=CLONE_ID, name="Neil's Clock")
        cloned_manifest["omarchy"] = {"clonedFrom": SOURCE_ID}
        cloned_manifest["barWidget"] = {
            "displayName": "Neil's Clock", "allowMultiple": False
        }
        self.write_manifest(self.clone, cloned_manifest)

    @staticmethod
    def base_qml():
        # Enough unchanged context separates edits to opposite ends of this file.
        return "\n".join([
            "import QtQuick",
            "Item {",
            '    property string greeting: "Original greeting"',
            "    property int one: 1",
            "    property int two: 2",
            "    property int three: 3",
            "    property int four: 4",
            "    property int five: 5",
            "    property int six: 6",
            "    property int seven: 7",
            "    property int eight: 8",
            '    property string farewell: "Original farewell"',
            "}",
            "",
        ])

    @staticmethod
    def write(directory, relative, content):
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    @classmethod
    def write_manifest(cls, directory, manifest):
        cls.write(directory, "manifest.json", json.dumps(manifest, indent=2) + "\n")

    @staticmethod
    def snapshot(directory):
        """Record bytes, links, directories and mode bits without following links."""
        result = {}
        if not directory.exists():
            return result
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(directory).as_posix()
            if path.is_symlink():
                result[relative] = ("symlink", os.readlink(path))
            elif path.is_dir():
                result[relative] = ("directory",)
            else:
                result[relative] = (
                    "file", path.read_bytes(), path.stat().st_mode & 0o777
                )
        return result

    def run_cli(self, *arguments, success=True):
        source_before = self.snapshot(self.source)
        clones_before = self.snapshot(self.plugins)
        command = [
            sys.executable, str(CLI),
            "--omarchy-path", str(self.omarchy),
            "--plugins-dir", str(self.plugins),
            "--state-dir", str(self.state),
            *map(str, arguments),
        ]
        result = subprocess.run(
            command, text=True, capture_output=True, timeout=20,
            cwd=self.root,
        )
        self.assertEqual(source_before, self.snapshot(self.source),
                         "CLI changed the installed upstream plugin")
        self.assertEqual(clones_before, self.snapshot(self.plugins),
                         "CLI changed installed clone data")
        self.assertNotIn("Traceback (most recent call last)", result.stderr,
                         "CLI crashed instead of returning a handled result")
        if success is True:
            self.assertEqual(result.returncode, 0,
                             f"{command!r}\n{result.stdout}\n{result.stderr}")
        elif success is False:
            self.assertNotEqual(result.returncode, 0,
                                "Unsafe/invalid operation was accepted")
        return result

    def run_json(self, *arguments, success=True):
        result = self.run_cli(*arguments, "--json", success=success)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"CLI returned invalid JSON: {result.stdout!r}; "
                      f"stderr={result.stderr!r}; {exc}")

    def track(self, baseline=None):
        arguments = ["track", CLONE_ID]
        if baseline is not None:
            arguments.extend(["--baseline", baseline])
        return self.run_json(*arguments)

    def preview(self):
        # Both exit conventions are acceptable for a review with conflicts;
        # callers must receive the complete documented JSON either way.
        return self.run_json("preview", CLONE_ID, success=None)

    def export(self, output=None, success=True):
        output = output or self.root / "candidate"
        result = self.run_cli("export", CLONE_ID, "--output", output, "--json",
                              success=success)
        if success:
            json.loads(result.stdout)
        return output

    def replace(self, directory, old, new, relative="BarWidget.qml"):
        path = directory / relative
        content = path.read_text(encoding="utf-8")
        self.assertIn(old, content)
        path.write_text(content.replace(old, new), encoding="utf-8")

    def changes_by_path(self, preview):
        return {change["path"]: change for change in preview["changes"]}

    def assert_clean_preview(self, preview):
        self.assertEqual(preview["cloneId"], CLONE_ID)
        self.assertEqual(preview["sourceId"], SOURCE_ID)
        self.assertFalse(preview["hasConflicts"])
        self.assertEqual(preview["summary"]["conflicts"], 0)
        self.assertTrue(preview["validation"]["valid"], preview["validation"])
        self.assertEqual(preview["validation"]["errors"], [])

    def assert_conflict(self, path):
        preview = self.preview()
        self.assertTrue(preview["hasConflicts"], preview)
        self.assertTrue(self.changes_by_path(preview)[path]["conflict"])
        self.assertGreaterEqual(preview["summary"]["conflicts"], 1)
        output = self.export(success=False)
        self.assertFalse(output.exists(), "Rejected export left a partial candidate")
        return preview

    def test_status_reports_untracked_clone_and_required_fields(self):
        status = self.run_json("status")
        self.assertEqual(status["errors"], [])
        self.assertEqual(len(status["clones"]), 1)
        clone = status["clones"][0]
        self.assertEqual(clone["id"], CLONE_ID)
        self.assertEqual(clone["sourceId"], SOURCE_ID)
        self.assertEqual(Path(clone["path"]), self.clone)
        self.assertFalse(clone["tracked"])
        for field in ("name", "status", "message", "localChangedFiles",
                      "upstreamChangedFiles", "baselineVersion"):
            self.assertIn(field, clone)

    def test_unchanged_clone_can_be_tracked_without_historical_baseline(self):
        self.track()
        status = self.run_json("status")
        self.assertTrue(status["clones"][0]["tracked"])
        preview = self.preview()
        self.assert_clean_preview(preview)
        self.assertIn("baseVersion", preview)
        self.assertIn("upstreamVersion", preview)

    def test_modified_clone_requires_explicit_historical_baseline(self):
        self.replace(self.clone, "Original greeting", "Personal greeting")
        self.run_cli("track", CLONE_ID, "--json", success=False)
        self.assertFalse(self.run_json("status")["clones"][0]["tracked"])
        self.track(baseline=self.source)
        self.assertTrue(self.run_json("status")["clones"][0]["tracked"])

    def test_manifest_only_customization_also_requires_explicit_baseline(self):
        manifest = json.loads((self.clone / "manifest.json").read_text())
        manifest["description"] = "A description customized by the user"
        self.write_manifest(self.clone, manifest)
        self.run_cli("track", CLONE_ID, "--json", success=False)

    def test_explicit_baseline_must_match_source_identity(self):
        historical = self.root / "historical"
        shutil.copytree(self.source, historical)
        manifest = dict(self.manifest, id="omarchy.calendar")
        self.write_manifest(historical, manifest)
        self.run_cli("track", CLONE_ID, "--baseline", historical, "--json",
                     success=False)
        self.assertFalse(self.run_json("status")["clones"][0]["tracked"])

    def test_historical_baseline_is_used_when_current_upstream_has_advanced(self):
        historical = self.root / "historical"
        shutil.copytree(self.source, historical)
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.source, "Original farewell", "Upstream farewell")
        self.track(baseline=historical)
        self.assert_clean_preview(self.preview())
        merged = (self.export() / "BarWidget.qml").read_text()
        self.assertIn("Local greeting", merged)
        self.assertIn("Upstream farewell", merged)

    def test_tracking_twice_refuses_to_replace_known_base(self):
        self.track()
        state_before = self.snapshot(self.state)
        self.replace(self.source, "Original greeting", "Upstream greeting")
        self.run_cli("track", CLONE_ID, "--baseline", self.source, "--json",
                     success=False)
        self.assertEqual(state_before, self.snapshot(self.state))

    def test_baseline_is_a_snapshot_not_a_reference_to_current_source(self):
        self.track()
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.source, "Original greeting", "Upstream greeting")
        self.assert_conflict("BarWidget.qml")

    def test_nonoverlapping_text_edits_merge_and_preserve_clone_identity(self):
        self.track()
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.source, "Original farewell", "Upstream farewell")
        source_manifest = dict(self.manifest, version="1.1.0", description="New upstream description")
        self.write_manifest(self.source, source_manifest)
        (self.omarchy / "version").write_text("4.0.2\n", encoding="utf-8")
        preview = self.preview()
        self.assert_clean_preview(preview)
        self.assertIn("BarWidget.qml", self.changes_by_path(preview))
        output = self.export()
        merged = (output / "BarWidget.qml").read_text()
        self.assertIn("Local greeting", merged)
        self.assertIn("Upstream farewell", merged)
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual(manifest["id"], CLONE_ID)
        self.assertEqual(manifest["name"], "Neil's Clock")
        self.assertEqual(manifest["omarchy"]["clonedFrom"], SOURCE_ID)
        self.assertEqual(manifest["barWidget"]["displayName"], "Neil's Clock")
        self.assertEqual(manifest["version"], "1.1.0")
        self.assertEqual(manifest["description"], "New upstream description")

    def test_local_only_changes_survive_clean_export(self):
        self.track()
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.write(self.clone, "private-local.js", "// local helper\n")
        (self.clone / "README.md").unlink()
        self.assert_clean_preview(self.preview())
        output = self.export()
        self.assertIn("Local greeting", (output / "BarWidget.qml").read_text())
        self.assertEqual((output / "private-local.js").read_text(), "// local helper\n")
        self.assertFalse((output / "README.md").exists())

    def test_upstream_additions_and_deletions_are_exported(self):
        self.track()
        self.write(self.source, "lib/new-helper.js", "// new upstream helper\n")
        (self.source / "README.md").unlink()
        self.assert_clean_preview(self.preview())
        output = self.export()
        self.assertEqual((output / "lib/new-helper.js").read_text(),
                         "// new upstream helper\n")
        self.assertFalse((output / "README.md").exists())

    def test_upstream_delete_and_local_modify_conflict(self):
        self.track()
        self.write(self.clone, "README.md", "Local documentation.\n")
        (self.source / "README.md").unlink()
        self.assert_conflict("README.md")

    def test_local_delete_and_upstream_modify_conflict(self):
        self.track()
        (self.clone / "README.md").unlink()
        self.write(self.source, "README.md", "Upstream documentation.\n")
        self.assert_conflict("README.md")

    def test_different_additions_at_same_path_conflict(self):
        self.track()
        self.write(self.clone, "new.js", "local version\n")
        self.write(self.source, "new.js", "upstream version\n")
        self.assert_conflict("new.js")

    def test_identical_concurrent_additions_are_not_a_conflict(self):
        self.track()
        self.write(self.clone, "new.js", "same version\n")
        self.write(self.source, "new.js", "same version\n")
        self.assert_clean_preview(self.preview())
        self.assertEqual((self.export() / "new.js").read_text(), "same version\n")

    def test_binary_divergence_conflicts_without_lossy_text_conversion(self):
        self.write(self.source, "image.bin", b"\x00base\xff")
        self.write(self.clone, "image.bin", b"\x00base\xff")
        self.track()
        self.write(self.source, "image.bin", b"\x00upstream\xfe")
        self.write(self.clone, "image.bin", b"\x00local\xfd")
        self.assert_conflict("image.bin")

    def test_upstream_only_binary_change_is_preserved_exactly(self):
        self.write(self.source, "image.bin", b"\x00base\xff")
        self.write(self.clone, "image.bin", b"\x00base\xff")
        self.track()
        expected = b"\x00upstream\xfe\x80"
        self.write(self.source, "image.bin", expected)
        self.assert_clean_preview(self.preview())
        self.assertEqual((self.export() / "image.bin").read_bytes(), expected)

    def test_export_preserves_executable_helper_mode_and_bytes(self):
        script = b"#!/bin/sh\nprintf 'clock helper\\n'\n"
        for directory in (self.source, self.clone):
            self.write(directory, "bin/helper", script).chmod(0o755)
        self.track()
        self.assert_clean_preview(self.preview())
        exported = self.export() / "bin/helper"
        self.assertEqual(exported.read_bytes(), script)
        self.assertEqual(exported.stat().st_mode & 0o777, 0o755)

    def test_upstream_chmod_merges_with_local_content_change(self):
        script = "#!/bin/sh\nprintf 'original\\n'\n"
        for directory in (self.source, self.clone):
            self.write(directory, "helper.sh", script).chmod(0o644)
        self.track()
        (self.source / "helper.sh").chmod(0o755)
        self.write(self.clone, "helper.sh", script.replace("original", "personal"))
        preview = self.preview()
        self.assert_clean_preview(preview)
        self.assertIn("helper.sh", self.changes_by_path(preview))
        exported = self.export() / "helper.sh"
        self.assertEqual(exported.read_text(), script.replace("original", "personal"))
        self.assertEqual(exported.stat().st_mode & 0o777, 0o755)

    def test_local_chmod_merges_with_upstream_content_change(self):
        script = "#!/bin/sh\nprintf 'original\\n'\n"
        for directory in (self.source, self.clone):
            self.write(directory, "helper.sh", script).chmod(0o755)
        self.track()
        (self.clone / "helper.sh").chmod(0o644)
        self.write(self.source, "helper.sh", script.replace("original", "upstream"))
        self.assert_clean_preview(self.preview())
        exported = self.export() / "helper.sh"
        self.assertEqual(exported.read_text(), script.replace("original", "upstream"))
        self.assertEqual(exported.stat().st_mode & 0o777, 0o644)

    def test_corrupt_saved_baseline_content_blocks_preview_and_export(self):
        record = self.track()
        saved = Path(record["statePath"]) / "baseline" / "README.md"
        saved.write_text("Tampered ancestor contents.\n")
        state_before = self.snapshot(self.state)
        result = self.run_cli("preview", CLONE_ID, "--json", success=False)
        self.assertIn("integrity", result.stderr.lower())
        output = self.export(success=False)
        self.assertFalse(output.exists())
        status = self.run_json("status")
        self.assertIn("integrity", status["clones"][0]["message"].lower())
        self.assertEqual(state_before, self.snapshot(self.state),
                         "A failed integrity check must not rewrite the saved baseline")

    def test_corrupt_saved_baseline_mode_blocks_preview_and_export(self):
        record = self.track()
        saved = Path(record["statePath"]) / "baseline" / "README.md"
        saved.chmod(saved.stat().st_mode ^ 0o100)
        state_before = self.snapshot(self.state)
        result = self.run_cli("preview", CLONE_ID, "--json", success=False)
        self.assertIn("integrity", result.stderr.lower())
        output = self.export(success=False)
        self.assertFalse(output.exists())
        self.assertEqual(state_before, self.snapshot(self.state))

    def test_removed_upstream_is_reported_and_cannot_be_exported(self):
        self.track()
        shutil.rmtree(self.source)
        state_before = self.snapshot(self.state)
        status = self.run_json("status")
        self.assertEqual(len(status["clones"]), 1)
        self.assertTrue(status["clones"][0]["tracked"])
        self.assertIn("source", status["clones"][0]["message"].lower())
        result = self.run_cli("preview", CLONE_ID, "--json", success=False)
        self.assertIn("missing", result.stderr.lower())
        output = self.export(success=False)
        self.assertFalse(output.exists())
        self.assertEqual(state_before, self.snapshot(self.state))

    def test_missing_upstream_prevents_automatic_baseline_capture(self):
        shutil.rmtree(self.source)
        result = self.run_cli("track", CLONE_ID, "--json", success=False)
        self.assertIn("missing", result.stderr.lower())
        self.assertFalse(self.run_json("status")["clones"][0]["tracked"])

    def test_invalid_clone_omarchy_metadata_returns_handled_errors(self):
        manifest = json.loads((self.clone / "manifest.json").read_text())
        for invalid in (None, [], "omarchy.clock", 7):
            with self.subTest(omarchy=invalid):
                manifest["omarchy"] = invalid
                self.write_manifest(self.clone, manifest)
                self.run_cli("track", CLONE_ID, "--json", success=False)
                self.run_cli("preview", CLONE_ID, "--json", success=False)
                self.export(success=False)

    def test_invalid_flat_upstream_omarchy_metadata_returns_handled_error(self):
        # The shipped catalog also uses named *.manifest.json files whose entry
        # points sit beside the manifest, rather than an individual plugin dir.
        flat = self.source.parent / "clock.manifest.json"
        helper = self.source.parent / "BarWidget.qml"
        helper.write_text(self.base_qml())
        shutil.rmtree(self.source)
        for invalid in (None, [], "invalid", 7):
            with self.subTest(omarchy=invalid):
                manifest = dict(self.manifest, omarchy=invalid)
                flat.write_text(json.dumps(manifest))
                before = self.snapshot(self.omarchy)
                self.run_cli("track", CLONE_ID, "--json", success=False)
                self.assertEqual(before, self.snapshot(self.omarchy))

    def test_file_to_directory_collision_is_invalid_and_export_is_rejected(self):
        self.write(self.source, "settings", "original settings\n")
        self.write(self.clone, "settings", "original settings\n")
        self.track()
        # Local keeps and edits the file while upstream replaces it by a folder.
        self.write(self.clone, "settings", "personal settings\n")
        (self.source / "settings").unlink()
        self.write(self.source, "settings/options.json", "{}\n")
        preview = self.preview()
        self.assertTrue(preview["hasConflicts"])
        self.assertFalse(preview["validation"]["valid"])
        self.assertTrue(any("collision" in error.lower()
                            for error in preview["validation"]["errors"]))
        output = self.export(success=False)
        self.assertFalse(output.exists())

    def test_invalid_candidate_manifest_blocks_export(self):
        self.track()
        source_manifest = dict(self.manifest)
        source_manifest["entryPoints"] = {"barWidget": "Missing.qml"}
        self.write_manifest(self.source, source_manifest)
        preview = self.preview()
        self.assertFalse(preview["validation"]["valid"], preview)
        self.assertTrue(preview["validation"]["errors"])
        output = self.export(success=False)
        self.assertFalse(output.exists())

    def test_export_refuses_existing_directory_without_modifying_it(self):
        self.track()
        output = self.root / "candidate"
        output.mkdir()
        self.write(output, "keep.txt", "do not overwrite\n")
        before = self.snapshot(output)
        self.export(output, success=False)
        self.assertEqual(before, self.snapshot(output))

    def test_export_refuses_existing_file(self):
        self.track()
        output = self.root / "candidate"
        output.write_text("do not overwrite\n")
        self.export(output, success=False)
        self.assertEqual(output.read_text(), "do not overwrite\n")

    def test_export_refuses_existing_dangling_symlink(self):
        self.track()
        output = self.root / "candidate"
        target = self.root / "missing-target"
        output.symlink_to(target)
        self.export(output, success=False)
        self.assertTrue(output.is_symlink())
        self.assertEqual(os.readlink(output), str(target))
        self.assertFalse(target.exists())

    def test_export_refuses_locations_inside_live_plugin_trees(self):
        self.track()
        for output in (self.clone / "candidate", self.source / "candidate",
                       self.plugins / "new-live-plugin"):
            with self.subTest(output=output):
                self.export(output, success=False)
                self.assertFalse(output.exists())

    def test_clone_symlinks_are_refused_during_tracking(self):
        target = self.root / "outside-secret"
        target.write_bytes(b"not plugin data")
        (self.clone / "link").symlink_to(target)
        self.run_cli("track", CLONE_ID, "--baseline", self.source, "--json",
                     success=False)
        self.assertEqual(target.read_bytes(), b"not plugin data")

    def test_upstream_symlinks_block_preview_and_export(self):
        self.track()
        target = self.root / "outside-secret"
        target.write_bytes(b"not plugin data")
        (self.source / "link").symlink_to(target)
        result = self.run_cli("preview", CLONE_ID, "--json", success=False)
        self.assertNotEqual(result.returncode, 0)
        output = self.export(success=False)
        self.assertFalse(output.exists())
        self.assertEqual(target.read_bytes(), b"not plugin data")

    def test_historical_baseline_symlinks_are_refused(self):
        historical = self.root / "historical"
        shutil.copytree(self.source, historical)
        (historical / "link").symlink_to(self.source / "README.md")
        self.run_cli("track", CLONE_ID, "--baseline", historical, "--json",
                     success=False)

    def test_export_refuses_symlink_parent_into_live_directory(self):
        self.track()
        alias = self.root / "alias"
        alias.symlink_to(self.clone, target_is_directory=True)
        output = alias / "candidate"
        self.export(output, success=False)
        self.assertFalse(output.exists())

    def test_clone_identifier_cannot_escape_plugins_directory(self):
        self.run_cli("track", "../outside", "--json", success=False)
        self.run_cli("preview", "../outside", "--json", success=False)

    def test_untracked_preview_and_export_fail(self):
        self.run_cli("preview", CLONE_ID, "--json", success=False)
        output = self.export(success=False)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
