# Local alpha validation

Recorded during initial development on 2026-09-22.

## Completed

- 43 automated tests pass with Python 3.14.7 and Git 2.55.0.
- Tests use disposable source, clone and state directories. CLI tests compare
  source and installed-clone contents/modes before and after every command.
- Coverage includes historical ancestry, immutable baseline records, concurrent
  edits, additions, deletions, binary conflicts, executable modes, missing source,
  invalid metadata, symlink rejection, path boundaries, unreadable directories,
  stale exports and flat source mappings.
- `scripts/demo.py` completes track → diverged status → clean merge preview →
  export and verifies both local and upstream changes survive.
- `omarchy plugin validate .` passes against the locally installed validator.
- `scripts/check-ui.sh` exits zero with Qt 6.11.2. It retains the linter's dynamic
  QObject/Quickshell type warnings. The machine's unqualified Qt 5 `qmllint`
  cannot parse the current QML syntax, so it is intentionally not used.
- All 37 installed first-party plugin layouts can be materialized and structurally
  validated as clones. This was a read-only compatibility inspection.
- Real local discovery finds the existing customized audio clone and reports that
  no baseline is recorded. No baseline was guessed or captured for it.
- An isolated temporary Quickshell harness loaded the widget and panel and checked
  fixture status, preview, selection, empty state and helper-start failure handling.

## Remaining release validation

- A clean merge and valid manifest do not establish working QML behavior.
- Verify actual installed-shell popup behavior, keyboard focus, scaling, placement,
  alternative bar support, disable/re-enable and removal in a disposable desktop.
- The Python 3.11 CI configuration is checked in; that version has not been run
  locally on this machine.
- Apply and undo are not present in this alpha, and need their own transaction,
  interruption and stale-state tests before release.

The development workflow has not enabled Clone Keeper in the user's active bar
or modified installed clones or the Omarchy source package.
