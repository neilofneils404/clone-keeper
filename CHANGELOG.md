# Changelog

## 0.4.0 — local beta, 2026-10-17

- Find an existing clone's original among Omarchy releases on this machine:
  the installed tree, pacman's package cache, or supplied trees and packages.
  `origins` ranks them using pacman's log and the files that still match;
  `track --origin VERSION --expect HASH` records the confirmed one with its evidence.
- The panel searches automatically for an untracked clone and offers the best match.
- `sync` runs whenever the panel loads: it keeps each running release's built-in
  sources so they survive upgrades, and tracks clones identical to a recorded
  release, including clones made from Omarchy's own menu.
- Status dry-runs each update's merge: the panel and bar tooltip say which clones
  are ready to update and which need choices.
- After an Omarchy update changes a plugin you customized, a desktop notification
  says so once; clicking it opens Clone Keeper. Turn it off with the widget's
  Update notifications setting. `omarchy-shell neil.clone-keeper open` opens the panel.
- Linked development plugins that are not clones no longer produce status errors.
- Failed and undone background outcomes stay visible for the panel viewing instead
  of being cleared by the next status refresh.
- **Update** applies a clean update in one step: it prepares the plan and confirms
  that exact result, or opens the review when choices are needed.
- Applying to an enabled clone restarts the shell, checks its log for the clone's
  load failures and undoes the update automatically if it failed to load
  (`apply --restart-shell`).
- Resolve overlapping lines one conflict at a time with Keep yours, Take upstream,
  Keep both or Use original (`resolve --conflict N`); the rest of the file stays
  merged. **Open in editor** hands a candidate file to your Omarchy editor (`edit`).
- Git-installed plugins you have edited, which `omarchy plugin update` refuses to
  update, get the same review, conflict choices, apply, undo and recovery. Their
  ancestry comes from git; **Check for updates** fetches only on request; your
  edits stay as uncommitted changes and ignored files are left alone.
- Status warns when Omarchy removed a shell component or member a tracked clone
  uses since its baseline's release; the panel shows it with the clone.
- Reviews use the installed Omarchy's own manifest validator, and no longer refuse
  the bar sections newer releases add (`centerBegin`, `centerEnd`, `tray`).
- **Ask my agent…** opens your Omarchy default coding agent on a conflicting file,
  with the original, your and upstream's versions for context; you accept and
  review its edit like any manual resolution (`ask-agent`).
- `prune` lists, and with `--yes` removes, old transactions, reviews of removed
  clones and old recorded releases; **Clean up old history…** in the panel.

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
- The installed Omarchy version comes from its package, as `omarchy version` reports
  it; the packaged version file can be stale.
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
