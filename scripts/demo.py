#!/usr/bin/env python3
"""Show an independent text merge using only a disposable synthetic desktop."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile


CLI = Path(__file__).resolve().parents[1] / "bin" / "clone-keeper"


def main():
    with tempfile.TemporaryDirectory(prefix="clone-keeper-demo-") as temp:
        root = Path(temp)
        omarchy, plugins, state = (root / name for name in ("omarchy", "plugins", "state"))
        source = omarchy / "shell/plugins/panels/clock"
        source.mkdir(parents=True)
        (omarchy / "version").write_text("demo-1\n")
        original = {
            "schemaVersion": 1, "id": "omarchy.clock", "name": "Clock",
            "version": "1.0.0", "kinds": ["bar-widget"],
            "entryPoints": {"barWidget": "BarWidget.qml"},
            "barWidget": {"displayName": "Clock", "defaultSection": "center"},
        }
        (source / "manifest.json").write_text(json.dumps(original, indent=2) + "\n")
        qml = ('import QtQuick\n\nItem {\n'
               '    property string clockFormat: "HH:mm"\n\n'
               '    // Your visual preferences live here.\n\n'
               '    property bool showDate: true\n\n'
               '    // Upstream behavior lives here.\n\n'
               '    property int refreshSeconds: 60\n}\n')
        (source / "BarWidget.qml").write_text(qml)
        clone = plugins / "demo.clock"
        clone.parent.mkdir()
        shutil.copytree(source, clone)
        customized = dict(original)
        customized.update(id="demo.clock", name="My Clock", omarchy={"clonedFrom": "omarchy.clock"})
        customized["barWidget"] = {**original["barWidget"], "displayName": "My Clock"}
        (clone / "manifest.json").write_text(json.dumps(customized, indent=2) + "\n")
        common = [str(CLI), "--omarchy-path", str(omarchy), "--plugins-dir", str(plugins), "--state-dir", str(state)]

        def run(*args):
            print("\n$ clone-keeper " + " ".join(args), flush=True)
            subprocess.run(common + list(args), check=True)

        run("track", "demo.clock")
        (clone / "BarWidget.qml").write_text(qml.replace('"HH:mm"', '"hh:mm AP"'))
        (source / "BarWidget.qml").write_text(qml.replace("refreshSeconds: 60", "refreshSeconds: 30"))
        (omarchy / "version").write_text("demo-2\n")
        run("status")
        run("preview", "demo.clock")
        run("export", "demo.clock", "--output", str(root / "candidate"))
        merged = (root / "candidate/BarWidget.qml").read_text()
        assert '"hh:mm AP"' in merged and "refreshSeconds: 30" in merged
        print("\nVerified: your time format and the upstream refresh change are both preserved.")
        print("The disposable demo directory is now removed. No installed plugins were used.")


if __name__ == "__main__":
    main()
