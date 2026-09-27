# Validation

Recorded on 2026-09-22 for the 0.2.0 local beta, and updated on 2026-09-26 after
the pre-release review fixes (see the changelog's Unreleased section).

## Automated engine and CLI checks

- **125 tests pass on both Python 3.14.7 and Python 3.11.16**, with Git 2.55.0;
  no skips. Python 3.11 was provisioned only in a disposable temporary environment.
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
  version **0.2.0**; the Python package requires Python 3.11 or newer.
- A fresh source archive includes the native QML and manifest, bundled launcher,
  Python modules, complete tests, scripts, documentation/images and CI configuration.
  After unpacking that archive, **all 106 tests pass on Python 3.14.7 with no skips**;
  the executable launcher reports `Clone Keeper 0.2.0`, and the full demo passes.
  This archive check was recorded before the review fixes and has not been repeated.
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

## Remaining release checks

The engine, interface content and hidden native component loads are tested.
Actual bar installation, anchored popup interaction, horizontal/vertical and
replacement bar placement, keyboard behavior in the compositor, and the complete
disable/update/remove lifecycle still need a disposable desktop pilot.
The pilot must also verify, on the real shell, that Enable and Apply started from
the panel complete after Omarchy reloads Clone Keeper, and that reopening shows
their outcome. The mechanism is tested with SIGKILL; the real reload path is not.

A clean merge, valid manifest and syntax check do not prove correct plugin
behavior. Backups help undo recognized states; they do not make arbitrary QML
behavior safe or sandboxed.

Development has not enabled Clone Keeper in the active bar, changed installed
clones, rewritten shell settings, or modified the Omarchy package.
See [release preparation](RELEASE.md) for publication steps.
