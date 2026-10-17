#!/usr/bin/env python3
"""Exercise the real QML controller and CLI in a disposable desktop fixture.

Requires Omarchy's QML components and Quickshell. Offscreen screenshots render
the actual KeeperView; the live shell and installed user plugins are untouched.
"""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile


PROJECT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New directory for fixture, logs and screenshots")
    parser.add_argument("--native-load", action="store_true", help="Also load the real popup hidden in a separate Wayland Quickshell instance")
    args = parser.parse_args()
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="clone-keeper-ui-"))
    if args.output:
        output.mkdir(parents=True, exist_ok=False)
    shell = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "shell"
    if not (shell / "Ui/qmldir").exists() or not shutil.which("quickshell") or not shutil.which("bsdtar"):
        parser.error("Omarchy shell modules, Quickshell and bsdtar are required")

    omarchy, plugins, state = (output / name for name in ("omarchy", "plugins", "state"))
    original_qml = ('import QtQuick\n\nItem {\n'
                    '    property string clockFormat: "HH:mm"\n\n'
                    '    // Appearance belongs to you.\n\n'
                    '    property bool showDate: true\n\n'
                    '    // Upstream behavior can improve independently.\n\n'
                    '    property int refreshSeconds: 60\n}\n')
    for name in ("clock", "audio"):
        source = omarchy / "shell/plugins/panels" / name
        source.mkdir(parents=True)
        manifest = {"schemaVersion": 1, "id": "omarchy." + name, "name": name.title(),
                    "version": "1.0.0", "kinds": ["bar-widget"],
                    "entryPoints": {"barWidget": "BarWidget.qml"},
                    "barWidget": {"displayName": name.title(), "defaultSection": "right"}}
        (source / "manifest.json").write_text(json.dumps(manifest))
        (source / "BarWidget.qml").write_text(original_qml)
    (omarchy / "version").write_text("ui-fixture-1\n")
    clock = omarchy / "shell/plugins/panels/clock"
    (clock / "assets").mkdir()
    (clock / "assets/pixel.bin").write_bytes(b"\0original")

    # A fixture package cache and empty log keep origin searches inside the fixture.
    (output / "pkg").mkdir()
    (output / "pacman.log").write_text("")
    command = [str(PROJECT / "bin/clone-keeper"), "--omarchy-path", str(omarchy),
               "--plugins-dir", str(plugins), "--state-dir", str(state),
               "--package-cache", str(output / "pkg"), "--pacman-log", str(output / "pacman.log")]
    subprocess.run(command + ["clone", "omarchy.clock", "--id", "demo.clock", "--json"],
                   check=True, text=True, capture_output=True)
    # demo.audio was cloned from release 1.0.0, still in the package cache,
    # and customized; upstream has since changed another line.
    audio_source = omarchy / "shell/plugins/panels/audio"
    release = output / "release-1.0.0"
    shutil.copytree(audio_source, release / "usr/share/omarchy/shell/plugins/panels/audio")
    (release / "usr/share/omarchy/version").write_text("1.0.0\n")
    subprocess.run(["bsdtar", "--zstd", "-cf", str(output / "pkg/omarchy-1.0.0-1-x86_64.pkg.tar.zst"),
                    "-C", str(release), "usr"], check=True)
    audio_clone = plugins / "demo.audio"
    shutil.copytree(audio_source, audio_clone)
    manifest = json.loads((audio_clone / "manifest.json").read_text())
    manifest.update(id="demo.audio", name="My Audio", omarchy={"clonedFrom": "omarchy.audio"})
    manifest["barWidget"]["displayName"] = "My Audio"
    (audio_clone / "manifest.json").write_text(json.dumps(manifest))
    (audio_clone / "BarWidget.qml").write_text(original_qml.replace("showDate: true", "showDate: false"))
    (audio_source / "BarWidget.qml").write_text(original_qml.replace("refreshSeconds: 60", "refreshSeconds: 45"))
    # An unedited copy made outside Clone Keeper is tracked when the panel loads.
    shutil.copytree(audio_source, plugins / "demo.audio-copy")
    manifest.update(id="demo.audio-copy", name="Audio Copy")
    (plugins / "demo.audio-copy/manifest.json").write_text(json.dumps(manifest))
    before = original_qml.replace('"HH:mm"', '"hh:mm AP"')
    (plugins / "demo.clock/BarWidget.qml").write_text(before)
    (plugins / "demo.clock/assets/pixel.bin").write_bytes(b"\0local")
    (clock / "BarWidget.qml").write_text(original_qml.replace("refreshSeconds: 60", "refreshSeconds: 30"))
    (clock / "assets/pixel.bin").write_bytes(b"\0upstream")
    (omarchy / "version").write_text("ui-fixture-2\n")
    replacement = output / "resolved.bin"
    replacement.write_bytes(b"\0resolved")

    harness = output / "offscreen"
    harness.mkdir()
    for module in ("Commons", "Ui"):
        (harness / module).symlink_to(shell / module, target_is_directory=True)
    plugin = harness / "plugin"
    plugin.mkdir()
    for source in PROJECT.glob("*.qml"):
        shutil.copy(source, plugin / source.name)
    helper = output / "fixture-helper"
    helper.write_text("#!/usr/bin/env python3\nimport json,subprocess,sys\n"
                      "from pathlib import Path\n"
                      f"with Path({str(output / 'commands.jsonl')!r}).open('a') as log:\n"
                      "    log.write(json.dumps(sys.argv[1:])+'\\n')\n"
                      f"raise SystemExit(subprocess.run({command!r}+sys.argv[1:]).returncode)\n")
    helper.chmod(0o755)
    (plugin / "bin").mkdir()
    shutil.copy(helper, plugin / "bin/clone-keeper")
    qml = r'''
import QtQuick
import Quickshell
import qs.Commons
import "plugin" as Keeper

ShellRoot {
  id: test
  property int step: 0
  property int ticks: 0
  property int resolutionIndex: 0
  property bool capturing: false
  property bool failed: false
  property var resolutions: ["local", "upstream", "base", "delete", "file", "edited"]
  Keeper.KeeperController { id: keeper; helperPath: HELPER_PATH }
  FloatingWindow {
    id: window
    visible: true
    implicitWidth: 620
    implicitHeight: 700
    color: Color.background
    Rectangle {
      id: frame
      anchors.fill: parent
      anchors.margins: 16
      color: Color.background
      Keeper.KeeperView { anchors.fill: parent; keeper: keeper }
    }
  }
  function check(value, message) {
    if (!value) { failed = true; console.error("SMOKE_FAIL", message, keeper.errorText); Qt.quit(); return false }
    return true
  }
  function capture(name) {
    capturing = true
    frame.grabToImage(function(result) {
      if (!result.saveToFile(OUTPUT_PATH + "/" + name + ".png")) check(false, "capture failed: " + name)
      capturing = false
    })
  }
  function named(item, name) {
    if (item.objectName === name) return item
    var children = item.children || []
    for (var i = 0; i < children.length; i++) {
      var found = named(children[i], name)
      if (found) return found
    }
    return null
  }
  Timer {
    interval: 120
    repeat: true
    running: true
    onTriggered: {
      test.ticks++
      if (test.ticks > 400) { test.check(false, "timeout"); return }
      if (test.failed || keeper.busy || test.capturing) return
      switch (test.step) {
      case 0: keeper.startup(); test.step++; break
      case 1:
        if (!test.check(keeper.hasLoaded && keeper.clones.length === 3, "initial status")) return
        if (!test.check(keeper.clones.filter(function(item) { return item.id === "demo.audio-copy" && item.tracked }).length === 1
                        && keeper.notice.indexOf("demo.audio-copy") !== -1, "sync tracks unedited clone")) return
        test.capture("01-clones"); test.step++; break
      // Selecting an untracked clone searches for its original by itself.
      case 2: keeper.selectClone("demo.audio"); test.step = 100; break
      case 100:
        if (!test.check(keeper.origins && keeper.origins.cloneId === "demo.audio"
                        && keeper.origins.candidates.length === 2 && keeper.origins.candidates[0].suggested
                        && keeper.origins.candidates[0].versions[0] === "1.0.0-1", "find original release")) return
        test.capture("01-origin"); test.step++; break
      case 101: keeper.adoptOrigin(keeper.origins.candidates[0]); test.step++; break
      // A clean update goes from one button to a confirmation of the prepared plan.
      case 102:
        if (!test.check(keeper.selectedClone.tracked && keeper.selectedClone.status === "diverged"
                        && keeper.selectedClone.conflicts === 0 && !keeper.errorText, "adopt original release")) return
        keeper.update(); test.step++; break
      case 103:
        if (!test.check(keeper.confirmation && keeper.confirmation.button === "Update" && !keeper.confirmation.restart,
                        "one-click update confirmation")) return
        test.capture("01-update-confirmation"); test.step++; break
      case 104: keeper.confirmAction(); test.step++; break
      case 105:
        if (!test.check(keeper.selectedClone.status === "local-only" && !keeper.errorText, "one-click update applied")) return
        test.step = 3; break
      case 3:
        keeper.loadSources(); test.step++; break
      case 4:
        if (!test.check(keeper.sources.length === 2, "built-in sources")) return
        test.capture("02-new-clone"); test.step++; break
      case 5: keeper.createClone("omarchy.clock", "demo.extra-clock"); test.step++; break
      case 6:
        if (!test.check(keeper.selectedId === "demo.extra-clock" && keeper.selectedClone.tracked, "create tracked clone")) return
        keeper.askEnable(); test.capture("03-enable-confirmation"); test.step++; break
      case 7: keeper.confirmAction(); test.step++; break
      case 8:
        if (!test.check(keeper.errorText.indexOf("installed") !== -1, "isolated enable is refused")) return
        keeper.selectClone("demo.clock"); keeper.prepare(); test.step++; break
      case 9:
        if (!test.check(keeper.plan && keeper.plan.hasConflicts && !keeper.canApply, "conflict blocks apply")) return
        test.capture("04-conflict"); test.step++; break
      case 10:
        keeper.selectedFile = "BarWidget.qml"; keeper.diffView = "local"
        if (!test.check(keeper.changeDiff(keeper.selectedChange).indexOf("hh:mm AP") !== -1, "local diff")) return
        test.capture("05-your-changes"); test.step++; break
      case 11:
        keeper.diffView = "upstream"
        if (!test.check(keeper.changeDiff(keeper.selectedChange).indexOf("refreshSeconds: 30") !== -1, "upstream diff")) return
        test.capture("06-upstream-changes"); test.step++; break
      case 12:
        keeper.selectedFile = "assets/pixel.bin"; keeper.diffView = "merge"
        if (test.resolutionIndex < test.resolutions.length) {
          var take = test.resolutions[test.resolutionIndex++]
          keeper.resolve(take === "file" ? "" : take, take === "file" ? REPLACEMENT_PATH : "")
        } else test.step++
        break
      case 13:
        if (!test.check(keeper.canApply && !keeper.errorText, "resolved review")) return
        keeper.selectedFile = "BarWidget.qml"; keeper.diffView = "merge"
        test.capture("07-ready-to-apply"); test.step++; break
      case 14: keeper.askApply(); test.capture("08-apply-confirmation"); test.step++; break
      case 15:
        var changed = JSON.parse(JSON.stringify(keeper.plan)); changed.candidateHash = "changed-after-confirmation"
        keeper.plan = changed; keeper.confirmAction()
        if (!test.check(keeper.errorText.indexOf("review changed") !== -1, "changed confirmation is refused")) return
        keeper.review(); test.step++; break
      case 16: keeper.askApply(); keeper.confirmAction(); test.step++; break
      case 17:
        if (!test.check(keeper.selectedClone.canUndo && !keeper.errorText, "apply and refresh")) return
        keeper.history(); test.step++; break
      case 18:
        if (!test.check(keeper.transactions.length === 1, "transaction history")) return
        test.capture("09-history"); test.step++; break
      case 19: keeper.askUndo(); test.capture("10-undo-confirmation"); test.step++; break
      case 20: keeper.confirmAction(); test.step++; break
      case 21:
        if (!test.check(!keeper.errorText && !keeper.selectedClone.canUndo, "undo and refresh")) return
        var rows = JSON.parse(JSON.stringify(keeper.clones));
        for (var i = 0; i < rows.length; i++) if (rows[i].id === keeper.selectedId) rows[i].pendingTransaction = "fixture-pending"
        keeper.clones = rows; test.capture("11-recovery-guidance"); test.step++; break
      case 22:
        keeper.askRecover(); test.capture("12-recovery-confirmation"); test.step++; break
      case 23:
        keeper.confirmation = null; keeper.clones = []; keeper.selectedId = ""; keeper.notice = ""
        test.capture("13-empty-state"); test.step++; break
      case 24:
        frame.anchors.fill = null; frame.width = 388; frame.height = 648
        frame.x = 16; frame.y = 16
        Style.fontBaseSize = 18; keeper.loadSources(); test.step++; break
      case 25: test.capture("14-large-text"); test.step++; break
      case 26:
        var button = test.named(frame, "createTrackedClone")
        if (!test.check(!!button, "create action exists")) return
        button.forceActiveFocus(Qt.TabFocusReason); test.step++; break
      case 27:
        var focused = test.named(frame, "createTrackedClone")
        if (!test.check(focused.mapToItem(frame, 0, 0).y + focused.height < frame.height, "focused action scrolls into view")) return
        test.capture("15-large-text-focused"); test.step++; break
      case 28:
        // Outcomes recorded while Omarchy had reloaded the panel appear once it is open.
        keeper.page = "clones"; keeper.panelOpen = true; keeper.notice = ""; keeper.errorText = ""
        keeper.operations = [
          { requestId: "a".repeat(32), cloneId: "demo.clock", operation: "apply", status: "succeeded",
            message: "Candidate applied; the baseline now records upstream.", warning: null, reloadRequired: false },
          { requestId: "b".repeat(32), cloneId: "demo.audio", operation: "enable", status: "interrupted",
            message: "The enable request for demo.audio stopped before recording a result." }]
        keeper.reportOperations()
        if (!test.check(keeper.notice.indexOf("applied") !== -1 && keeper.errorText.indexOf("demo.audio") !== -1
            && keeper.pendingDismissals.length + (keeper.operation === "dismiss" ? 2 : 0) >= 2, "recorded outcomes shown")) return
        test.step++; break
      case 29:
        keeper.operations = [{ requestId: "c".repeat(32), cloneId: "demo.clock", operation: "apply",
          status: "running", message: "Finishing apply for demo.clock…" }]
        keeper.reportOperations()
        if (!test.check(keeper.locked && keeper.notice.indexOf("paused") !== -1, "background operation locks changes")) return
        test.capture("16-background-operation"); test.step++; break
      case 30: keeper.operations = []; keeper.notice = ""; keeper.refreshStatus(); test.step++; break
      case 31:
        // An applied change to an enabled clone offers a shell restart. The
        // fixture is never live, so pretend here; the CLI still refuses below.
        var enabledRows = JSON.parse(JSON.stringify(keeper.clones))
        for (var r = 0; r < enabledRows.length; r++) if (enabledRows[r].id === "demo.clock") enabledRows[r].enabled = true
        keeper.clones = enabledRows; keeper.live = true
        keeper.operations = [{ requestId: "d".repeat(32), cloneId: "demo.clock", operation: "apply", status: "succeeded",
          message: "Candidate applied; the baseline now records upstream.", warning: null, reloadRequired: false }]
        keeper.reportOperations()
        if (!test.check(keeper.restartSuggested, "enabled clone apply suggests a restart")) return
        test.step++; break
      case 32:
        var restart = test.named(frame, "restartShell")
        if (!test.check(!!restart && restart.visible, "restart action shown")) return
        restart.forceActiveFocus(Qt.TabFocusReason); keeper.askRestart()
        test.capture("17-restart-confirmation"); test.step++; break
      case 33: keeper.confirmation = null; test.step++; break
      case 34:
        if (!test.check(test.named(frame, "restartShell").activeFocus, "focus returns to the invoking control")) return
        keeper.askRestart(); keeper.confirmAction(); test.step++; break
      case 35:
        if (!test.check(keeper.errorText.indexOf("installed") !== -1, "isolated restart is refused")) return
        keeper.live = false; keeper.operations = []; test.step++; break
      case 36: console.log("SMOKE_OK real CLI create origins track resolve apply history undo; enable and restart refused in fixture; recorded outcomes; focus restored"); Qt.quit(); break
      }
    }
  }
}
'''
    qml = qml.replace("HELPER_PATH", json.dumps(str(helper)))
    qml = qml.replace("OUTPUT_PATH", json.dumps(str(output)))
    qml = qml.replace("REPLACEMENT_PATH", json.dumps(str(replacement)))
    (harness / "shell.qml").write_text(qml)
    environment = os.environ.copy()
    environment.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software")
    environment.pop("WAYLAND_DISPLAY", None)
    result = subprocess.run(["quickshell", "--no-color", "--path", str(harness)],
                            text=True, capture_output=True, timeout=65, env=environment)
    log = result.stdout + result.stderr
    (output / "runtime.log").write_text(log)
    print(log)
    bad_runtime = any(marker in log for marker in ("SMOKE_FAIL", "ERROR:", "ReferenceError:", "TypeError:", "Binding loop", "Unable to assign"))
    if result.returncode or "SMOKE_OK" not in log or bad_runtime:
        print(f"UI smoke failed; artifacts: {output}", file=sys.stderr)
        return 1
    if (plugins / "demo.clock/BarWidget.qml").read_text() != before:
        raise AssertionError("Undo did not restore the original local customization")
    if (plugins / "demo.clock/assets/pixel.bin").read_bytes() != b"\0local":
        raise AssertionError("Undo did not restore the original binary customization")
    commands = [json.loads(line) for line in (output / "commands.jsonl").read_text().splitlines()]
    applies = [item for item in commands if item[0] == "apply"]
    # The one-click update and the reviewed apply each bind both fingerprints.
    assert len(applies) == 2 and all("--expect" in item and "--expect-plan" in item for item in applies), applies
    assert "showDate: false" in (plugins / "demo.audio/BarWidget.qml").read_text()
    assert "refreshSeconds: 45" in (plugins / "demo.audio/BarWidget.qml").read_text()
    for item in commands:
        if item[0] in ("clone", "enable", "apply", "undo", "recover"):
            assert "--detach" in item, f"Panel must detach changes that reload it: {item}"
    restarts = [item for item in commands if item[0] == "restart-shell"]
    assert len(restarts) == 1, f"Expected one refused restart request: {restarts}"
    if list((state / "operations").glob("*.json")):
        raise AssertionError("Shown operation outcomes were not dismissed")

    if args.native_load:
        native = output / "native"
        native.mkdir()
        for module in ("Commons", "Ui"):
            (native / module).symlink_to(shell / module, target_is_directory=True)
        (native / "plugin").symlink_to(plugin, target_is_directory=True)
        (native / "shell.qml").write_text('''import QtQuick
import Quickshell
import "plugin" as Keeper
ShellRoot {
  property var widget: Keeper.BarWidget { }
  property var panel: Keeper.Panel {
    Component.onCompleted: keeper.helperPath = ''' + json.dumps(str(helper)) + '''
  }
  Timer { interval: 1500; running: true; onTriggered: {
    if (panel.keeper.busy || !panel.keeper.hasLoaded || panel.keeper.errorText || widget.updateCount !== 1)
      console.error("NATIVE_LOAD_FAIL", panel.keeper.errorText, widget.updateCount)
    else console.log("NATIVE_LOAD_OK");
    Qt.quit();
  } }
}
''')
        run = subprocess.run(["quickshell", "--no-color", "--path", str(native)],
                             capture_output=True, text=True, timeout=10)
        native_log = run.stdout + run.stderr
        (output / "native.log").write_text(native_log)
        print(native_log)
        if run.returncode or "NATIVE_LOAD_OK" not in native_log or "ERROR:" in native_log:
            return 1
    print(f"UI smoke passed. Screenshots, logs and disposable fixture: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
