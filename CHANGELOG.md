# Changelog

## 0.3.0 — local beta, 2026-09-27

- Publish complete transaction staging trees atomically so interrupted copies can
  be recovered, including after the clone was changed but before baseline replacement.
- Detect concurrent candidate edits during resolution and retain displaced candidate
  trees with their saved plan, including files still open in an editor.
- Validate detached-command root separation before writing operation records.
- Panel changes (create, enable, apply, undo, recover) run with `--detach`, so the
  plugin reload they trigger can no longer kill them partway. Outcomes are recorded,
  shown when the panel reopens and dismissed once seen; changes pause while one runs.
- Added `--detach`, status `operations`, and `dismiss` to the CLI.
- Status reports whether each live clone is enabled; the panel hides Enable for it.
- Saved reviews no longer accumulate: untouched duplicates are replaced, reviews for
  superseded baselines are removed after apply, and resume only offers current ones.
- Apply keeps the clone manifest's key order and indentation.
- Path arguments expand `~/`; the panel rejects relative paths.
- Documented the tested Omarchy version. CI also runs the demo.
- After apply, undo or recovery of an enabled clone, the panel offers to restart
  the Omarchy shell so the new version loads; added `restart-shell` to the CLI.
  Status reports whether it is using the live plugin directory.
- Conflict choices say they use a whole file and point to manual resolution.
- Keyboard focus returns to the control that opened a confirmation.
- The panel header keeps its title on one line at larger text sizes, and shown
  outcomes are cleared when the panel closes.

## 0.2.0 — local beta, 2026-09-22

The complete maintenance cycle is available from the native panel and CLI.

- Added baseline-first disabled clone creation and interrupted-creation recovery.
- Added persistent merge plans, per-file review and saved-review resume.
- Added side selection, deletion, replacement files and manual conflict resolution.
- Added reviewed-hash apply, stale-input checks and optional Qt 6 syntax validation.
- Added whole-directory swaps, durable transaction history, recovery and exact undo.
- Advance ancestry to upstream after apply while preserving local customizations.
- Refuse unsupported metadata and unrecognized changes instead of discarding them.
- Added explicit shell activation with bounded discovery polling.
- Expanded disposable integration, interruption, UI and compatibility checks.

Backups and staging trees are retained; pruning is not implemented. Candidate
syntax checking does not establish correct runtime behavior. Complete the native
desktop pilot before marketplace publication.

## 0.1.0 — local alpha, 2026-09-22

Initial baseline capture, clone status, three-way preview, clean candidate export
and read-only native panel.
