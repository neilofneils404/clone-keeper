#!/usr/bin/env python3
"""Exercise create, review, apply and undo in a disposable synthetic desktop."""
from pathlib import Path
import json
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
        common = [str(CLI), "--omarchy-path", str(omarchy), "--plugins-dir", str(plugins), "--state-dir", str(state)]

        def run(*args):
            print("\n$ clone-keeper " + " ".join(args), flush=True)
            output = subprocess.run(common + list(args) + ["--json"], check=True, text=True, capture_output=True)
            result = json.loads(output.stdout)
            if "message" in result:
                print(result["message"])
            elif "summary" in result:
                print(f"{result['summary']['files']} changed files, {result['summary']['conflicts']} conflicts")
            return result

        run("clone", "omarchy.clock", "--id", "demo.clock")
        local = qml.replace('"HH:mm"', '"hh:mm AP"')
        (clone / "BarWidget.qml").write_text(local)
        (source / "BarWidget.qml").write_text(qml.replace("refreshSeconds: 60", "refreshSeconds: 30"))
        (omarchy / "version").write_text("demo-2\n")
        plan = run("prepare", "demo.clock")
        assert not plan["hasConflicts"] and plan["validation"]["valid"]
        for change in plan["changes"]:
            print(change["diff"])
        run("apply", plan["planId"], "--expect", plan["candidateHash"], "--expect-plan", plan["planHash"])
        merged = (clone / "BarWidget.qml").read_text()
        baseline = (state / "clones/demo.clock/baseline/BarWidget.qml").read_text()
        assert '"hh:mm AP"' in merged and "refreshSeconds: 30" in merged
        assert '"HH:mm"' in baseline and "refreshSeconds: 30" in baseline
        print("Verified: the clone keeps both edits; the new baseline contains only upstream.")
        run("undo", "demo.clock")
        assert (clone / "BarWidget.qml").read_text() == local
        assert (state / "clones/demo.clock/baseline/BarWidget.qml").read_text() == qml
        print("Verified: undo restored both the customized clone and its original baseline.")
    print("\nDisposable demo removed. No installed plugins or shell configuration were used.")


if __name__ == "__main__":
    main()
