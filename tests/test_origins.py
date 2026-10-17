"""Find and adopt a customized clone's original from Omarchy releases on disk."""

from datetime import datetime, timedelta, timezone
import shutil
import subprocess
import unittest

from tests import test_cli
from tests.test_cli import CLONE_ID


@unittest.skipUnless(shutil.which("bsdtar"), "bsdtar is required to build package fixtures")
class OriginTests(unittest.TestCase):
    """A clone made under 4.0.0 and customized, with 4.0.1 now installed."""

    def setUp(self):
        # Reuse the disposable real-schema fixture without inheriting its tests.
        fixture = test_cli.CloneKeeperIntegrationTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for name in ("root", "source", "clone", "state", "replace", "run_json", "preview", "export",
                     "assert_clean_preview"):
            setattr(self, name, getattr(fixture, name))
        self.cache = self.root / "pkg"
        self.cache.mkdir()
        self.log = self.root / "pacman.log"
        # The release the clone was made from, before upstream changed it.
        self.package("4.0.0-1", self.source)
        self.replace(self.source, "Original farewell", "Upstream farewell")
        self.replace(self.clone, "Original greeting", "Local greeting")
        now = datetime.now(timezone.utc)
        self.write_log([(now - timedelta(days=30), "installed omarchy (4.0.0-1)"),
                        (now + timedelta(days=1), "upgraded omarchy (4.0.0-1 -> 4.0.1-1)")])
        self.birth_known = subprocess.run(["stat", "-c", "%W", str(self.clone)],
                                          capture_output=True, text=True).stdout.strip() not in ("", "0")

    def package(self, version, source):
        root = self.root / f"pkgroot-{version}"
        target = root / "usr/share/omarchy/shell/plugins/panels/clock"
        shutil.copytree(source, target)
        (root / "usr/share/omarchy/version").write_text(version.split("-")[0] + "\n")
        archive = self.cache / f"omarchy-{version}-x86_64.pkg.tar.zst"
        subprocess.run(["bsdtar", "--zstd", "-cf", str(archive), "-C", str(root), "usr"], check=True)
        return archive

    def write_log(self, events):
        self.log.write_text("".join(f"[{when.strftime('%Y-%m-%dT%H:%M:%S%z')}] [ALPM] {text}\n"
                                    for when, text in events))

    def origins(self, *extra, success=True):
        return self.run_json("--package-cache", self.cache, "--pacman-log", self.log,
                             "origins", CLONE_ID, *extra, success=success)

    def adopt(self, version, digest, success=True):
        return self.run_json("--package-cache", self.cache, "--pacman-log", self.log,
                             "track", CLONE_ID, "--origin", version, "--expect", digest, success=success)

    def test_ranks_the_release_that_explains_the_clone(self):
        found = self.origins()
        self.assertEqual(found["errors"], [])
        first = found["candidates"][0]
        self.assertEqual(first["versions"], ["4.0.0-1"])
        self.assertTrue(first.get("suggested"))
        self.assertEqual(first["changedFiles"], ["BarWidget.qml"])
        self.assertEqual(first["changedLines"], 2)
        self.assertEqual(first["releases"][0]["kind"], "pacman-cache")
        self.assertRegex(first["releases"][0]["sha256"], "^[0-9a-f]{64}$")
        installed = next(item for item in found["candidates"] if "4.0.1" in item["versions"])
        self.assertGreater(installed["changedLines"], first["changedLines"])
        if self.birth_known:
            self.assertEqual(found["installedAtCreation"], "4.0.0-1")
            self.assertTrue(first["timelineMatch"])

    def test_adopted_origin_merges_upstream_and_keeps_customization(self):
        digest = self.origins()["candidates"][0]["baselineHash"]
        self.adopt("4.0.0-1", "0" * 64, success=False)
        self.adopt("4.0.1", digest, success=False)
        tracked = self.adopt("4.0.0-1", digest)
        self.assertEqual(tracked["provenance"], "historical-release-source")
        self.assertEqual(tracked["baselineHash"], digest)
        record = test_cli.json.loads((self.state / "clones" / CLONE_ID / "record.json").read_text())
        self.assertEqual(record["evidence"]["versions"], ["4.0.0-1"])
        preview = self.preview()
        self.assert_clean_preview(preview)
        merged = (self.root / "candidate")
        self.export(merged)
        text = (merged / "BarWidget.qml").read_text()
        self.assertIn("Local greeting", text)
        self.assertIn("Upstream farewell", text)
        self.adopt("4.0.0-1", digest, success=False)

    def test_reports_missing_release_package(self):
        if not self.birth_known:
            self.skipTest("filesystem does not record directory creation times")
        next(self.cache.iterdir()).unlink()
        found = self.origins()
        self.assertEqual(found["installedAtCreation"], "4.0.0-1")
        self.assertFalse(any(item.get("suggested") for item in found["candidates"]))
        self.assertIn("not available", found["errors"][0]["message"])

    def test_supplied_release_tree_is_considered(self):
        release = self.root / "pkgroot-4.0.0-1" / "usr/share/omarchy"
        next(self.cache.iterdir()).unlink()
        found = self.origins("--search", release)
        supplied = next(item for item in found["candidates"] if "4.0.0" in item["versions"])
        self.assertEqual(supplied["releases"][0]["kind"], "supplied")
        self.assertEqual(supplied["changedLines"], 2)

    def test_rejects_unreadable_search_paths(self):
        self.origins("--search", self.root / "missing", success=False)


if __name__ == "__main__":
    unittest.main()
