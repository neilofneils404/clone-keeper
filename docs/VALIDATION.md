# Validation

Recorded on 2026-09-22 for the 0.2.0 local beta and rerun on 2026-09-27 for the
0.3.0 local beta, after the pre-release review fixes.

## Automated engine and CLI checks

- **131 tests pass on Python 3.14.7 and on Python 3.11.16**, both with Git 2.55.0
  and no skips. The 3.11 runtime was a uv-managed interpreter, not the system Python.
- The new regressions terminate a process during clone/state staging for both
  apply and undo, then verify recovery. They also cover interrupted replacement
  of an existing stage, concurrent candidate edits before/during exchange, late
  editor saves through open files, and detached root rejection before any writes.
- The demo, `scripts/check-ui.sh` and `scripts/smoke-ui.py --native-load` were
  rerun on 2026-09-27 and pass. The UI check still reports 21 type warnings.
- Tests use disposable source, plugin and state roots. Read-only commands are
  checked against before/after snapshots; fixture commands cannot call live shell IPC.
- Baseline coverage includes current-source verification, known historical originals,
  corrupted records/snapshots, cross-root plans, path boundaries and source disappearance.
- Merge coverage includes independent and overlapping edits, additions, deletions,
  binary conflicts, modes and file/directory collisions.
- Saved-review coverage includes every resolution route, candidate edits after review,
  stale clone/upstream/baseline inputs, conflict markers and invalid QML syntax.
- Apply tests verify that the baseline advances to upstream while local edits remain
  distinct across multiple subsequent updates.
- Undo verifies exact file bytes/modes, directories, empty directories and baseline
  state; intervening edits, changed backups and unsupported metadata are refused.
- Fault injection covers apply/undo journal stages, swaps before journal updates,
  interrupted creation, durable parent publication and recovery preserving unknown edits.
  These are simulated interruption tests, not a hardware power-loss experiment.
- Mocked shell tests cover delayed asynchronous discovery, a bounded deadline,
  malformed registry output, transport errors, one activation attempt and pending guards.
- Detached operations: a caller killed with SIGKILL mid-operation (as Quickshell kills
  a destroyed Process's child) leaves its worker running; the worker finishes, records
  the outcome, and does not hold the caller's output pipes. Dead-worker, PID-reuse,
  malformed-record, failure and CLI round-trip cases are covered, including status
  reporting and dismissal.
- Saved-review cleanup, current-baseline resume, manifest layout preservation, `~/`
  path expansion and live activation reporting (mocked shell) are covered.
- `python scripts/demo.py` passes create → prepare → apply → undo and asserts both
  the local customization and correct upstream ancestry at each step.

## Packaging

- The project metadata, plugin manifest, Python module and CLI consistently report
  version **0.3.0**; the Python package requires Python 3.11 or newer.
- A fresh source archive includes the native QML and manifest, bundled launcher,
  Python modules, complete tests, scripts, documentation/images and CI configuration.
  After unpacking that archive, **all 131 tests pass on Python 3.14.7 with no skips**;
  the executable launcher reports `Clone Keeper 0.3.0`, and the full demo passes.
- The Python wheel contains the CLI and its modules. Building it and installing it
  in an isolated Python 3.11.16 environment passes the console version/help checks.
  Temporary runtimes, installations and build artifacts were removed afterward.

The wheel is CLI-only. Native Omarchy installation uses the Git repository, which
also supplies the plugin manifest, QML interface and bundled launcher; installing
the Python wheel does not add the bar widget.

## Native interface

- `scripts/check-ui.sh` exits zero with Qt 6.11.2 and the installed Omarchy validator.
  It checks every root QML component and retains 21 dynamic QObject/Quickshell type
  warnings; there are no lint errors.
- `python scripts/smoke-ui.py --native-load` exits zero. It runs the actual QML
  controller against the real CLI with disposable roots and captures the actual
  KeeperView, rather than replacing its contents with a mock interface.
- The UI smoke exercises creation, baseline capture, all six conflict resolution
  routes, reviewed apply with both fingerprints, transaction history and undo. It
  asserts every live-changing panel command is detached and that shown outcomes are
  dismissed, and checks recorded outcomes and the paused state of a running one.
  Final filesystem checks verify text and binary customizations were restored.
- It also loads the real hidden BarWidget and Panel against installed Omarchy
  modules in a separate Quickshell instance, including the widget's update count.
- Captures cover clone list, creation, activation confirmation, conflict review,
  three diff views, apply confirmation, history, undo, recovery guidance/confirmation,
  empty state and narrow/larger-text layouts. Keyboard focus scrolls controls into view.
- Activation is deliberately refused on fixture roots. Recovery interface states
  are synthetic; actual recovery behavior is tested separately in the backend.
- All 37 installed first-party source layouts were materialized and structurally
  validated in a read-only compatibility inspection during initial development.
  This does not establish runtime correctness of every resulting clone.
- Real local discovery correctly leaves the existing customized audio clone
  untracked because no trustworthy baseline is recorded.

Reproduce the UI check on an Omarchy development machine:

```bash
scripts/check-ui.sh
python scripts/smoke-ui.py --native-load --output /tmp/clone-keeper-ui-review
```

The output directory must not exist. The smoke leaves its disposable fixtures,
logs and screenshots there for inspection. Omit --native-load for offscreen-only
testing. The native load requires an existing Wayland session.

## Desktop pilot (2026-09-27)

Run on Omarchy 4.0.0.alpha with Hyprland 0.56.2, inside nested Hyprland sessions
with a disposable HOME, a copied OMARCHY_PATH (so a built-in could receive a
simulated 4.0.1.alpha update) and a private D-Bus session. The installed system,
the live shell and the user's configuration were not changed. One session lived
on tmpfs, a second on btrfs. The panel was driven with the keyboard only (wtype);
mouse interaction was not tested.

Passed:

- `omarchy plugin add` of the committed repository installs and enables the widget;
  shell IPC opens the real anchored popup. Escape closes the panel, and closes only
  the dialog when a confirmation is open. Confirmations default to Cancel.
- Create, Enable, Apply and Undo started from the panel all complete after Omarchy
  reloads Clone Keeper. Each outcome is shown on reopening, including after a full
  shell restart, removal and reinstall, and is dismissed once seen.
- Enable swaps the built-in for the clone in the bar layout and keeps its settings.
- Review detects both sides changing, blocks Apply on conflict markers, and shows
  the three-way conflict. Manual resolution through the edited candidate combines
  both sides. Apply advances the baseline to upstream while the customization stays
  recorded; after a shell restart the merged clone runs with both changes.
- Undo restores the clone byte-for-byte (including modes) and the old baseline.
- On btrfs under sustained disk load (IO pressure about 60%), panel Apply and Undo
  complete in about 2.5 s instead of 0.5 s, pass through a pending journal, and
  finish with no pending transaction and no stray staging trees.
- Clone and Clone Keeper disable/re-enable, `omarchy plugin update` from a newer
  commit, and removal keep clone state intact; removal leaves the enabled clone
  running and every state file unchanged. Reinstalling resumes tracking.
- Horizontal and vertical (left) bars, a 734x815 output and a larger base font size.
- The unit suite and demo also pass with TMPDIR on btrfs.

Findings:

- After a dismissed confirmation, focus returns to the top of the panel instead
  of the invoking button.
- Conflict choices (Keep yours, Take upstream, Use baseline) replace the whole
  file, so non-conflicting upstream changes in that file are dropped. The panel
  says the choice covers the file, but only below the buttons.
- Applied or undone QML did not always render until the shell restarted, although
  the confirmation only says enabled plugins may reload.
- At a larger font size in a narrow panel, the header tagline breaks mid-word.
- The outcome notice stays visible when the panel is reopened in the same session.
- Omarchy, not Clone Keeper: any change inside a local plugin triggers a live
  reload after which the indicators widget can overlap the workspaces until the
  shell restarts (reproduced with a plain file touch and no enabled clones);
  enabling a clone does not update a `centerAnchor` naming the built-in; the
  dropdown popup can extend past the panel on short outputs.

Not covered: replacement bars, multiple monitors, mouse interaction, a physical
(non-nested) session and hardware power loss.

A clean merge, valid manifest and syntax check do not prove correct plugin
behavior. Backups help undo recognized states; they do not make arbitrary QML
behavior safe or sandboxed.

Development and the pilot have not enabled Clone Keeper in the active bar, changed
installed clones, rewritten shell settings, or modified the Omarchy package.
See [release preparation](RELEASE.md) for publication steps.
