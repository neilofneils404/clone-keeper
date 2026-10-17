# Design

## Components and command boundary

The Python CLI is independently usable without a running desktop. The native QML
panel invokes it with argv arrays and consumes JSON; no user text is interpolated
into shell commands. The UI keeps a reviewed candidate and plan fingerprint for
its explicit apply confirmation.

| Module | Responsibility |
| --- | --- |
| core.py | Source layouts, canonical trees, baselines, diffs and three-way merges |
| plans.py | Persistent review snapshots and conflict choices |
| validation.py | Optional Qt 6 syntax checking without executing candidate code |
| transactions.py | Locks, metadata-preserving snapshots, atomic swaps, undo and recovery |
| service.py | Creation, tracking, status, history and bounded shell IPC |
| operations.py | Detached execution and recorded outcomes of panel-initiated changes |
| cli.py | Human/JSON command boundary |
| KeeperController.qml | Process lifecycle, review state and confirmation actions |
| KeeperView.qml and page components | Native clone, creation, review and history views |

All commands accept --json. Runtime errors return nonzero and include structured
JSON when requested; argument-parser usage errors are plain stderr diagnostics.
A successfully inspected plan may contain conflicts or validation
errors; callers must inspect hasConflicts, stale and validation.valid.

## Source layouts and identity

Discovery mirrors the installed catalog's manifest depth. Directory manifests
read a whole standalone plugin. Flat *.manifest.json sources materialize entry
points and clonePaths in target order; mapped source paths may reference siblings
inside the built-in source tree. Text references follow native clone layout rules.
Symlinks and special files are rejected; Git metadata is ignored for comparison.

Manifests are canonicalized as JSON before comparisons. The captured clone ID,
name and bar label normalize upstream manifests to the clone's identity. Later
local name changes remain merge inputs. Built-in IDs inside QML remain unchanged
to preserve Omarchy's clone IPC routing. Other manifest fields merge as text;
there is no semantic QML or JSON merge.

## Baseline provenance

State lives at clones/<id>/record.json alongside baseline/. Records contain IDs,
identity, capture time, version context, provenance, per-file hashes/modes and an
aggregate hash. Every baseline read verifies its inventory.

- created-from-source: clone creation records the ancestor before publication.
- verified-current-source: an unchanged existing clone matches current source.
- user-supplied-original: the owner supplies its known historical ancestor.
- historical-release-source: the owner confirms one of the Omarchy releases found
  on this machine (installed tree, recorded releases, pacman package cache, or a
  supplied tree or package). The record keeps the evidence shown: release versions and archive
  hashes, the clone's creation time, the release installed then according to
  pacman's log, and which files still matched.

- verified-release-source: an untracked clone is byte-identical to a release's
  source. sync records this without asking: an ancestor equal to the clone merges
  as a fast-forward whichever release it is labelled with.

sync runs when the panel loads. It records the installed release's materialized
sources at releases/<source id>/<tree hash>/, appending the version to an existing
identical tree, under a non-blocking lock so several bars do not repeat the work.
It then checks untracked clones against the installed tree and recorded releases
only; reading package archives is left to an explicit origins search. sync writes
only Clone Keeper state. Recorded trees are verified against their inventory on
every read.

Finding origins is read-only. Candidates are ranked by whether their release was
installed when the clone directory was created, then by fewest differing lines.
Those are suggestions; adoption requires the version and the baselineHash shown,
and re-derives the tree so the stored baseline is exactly what was reviewed.
Packages are extracted with bsdtar, which refuses absolute and parent-relative
members, into private scratch directories. Releases are materialized with the
current clone rules, which match Omarchy 4.x.
- applied-plan: a reviewed apply advances the ancestor to that plan's upstream.

Version labels and modification times never establish ancestry by themselves.
Clones may carry omarchy.clonedFromVersion, the release Omarchy's clone command
(and Clone Keeper's) copied them from. It is part of the clone's identity, like
its ID and clonedFrom, and origin searches use it in place of pacman's log to
rank candidates; the owner still confirms the original. Repeated tracking
refuses to replace an existing record.

Creation saves a verified baseline with creationPending=true, then publishes the
complete new clone with Linux RENAME_NOREPLACE, then clears the marker atomically.
Missing parent entries, files and containing directories are fsynced before
publication. Recovery can finish after any boundary. A pre-existing conflicting
clone is preserved; it must exactly match the saved creation to be accepted.
IDs already referenced in shell configuration are refused so a newly created
clone cannot silently become enabled. Normal creation stays disabled.

## Three-way merging and review

For each path in the union of original, local and upstream trees:

1. Equal local/upstream results are accepted.
2. If one side equals the original, accept the other side, including deletion.
3. Concurrent text edits use git merge-file --diff3.
4. Concurrent additions, delete/modify and binary divergence require resolution.
5. Ordinary permission changes merge independently from file contents.

Plans at plans/<uuid>/ retain immutable base, local and upstream snapshots, plus
an editable candidate, record.json and resolutions.json. Saved plan reads verify
immutable hashes and the configured clone path. Reviews show three diffs: original
to local, original to upstream, and local to candidate. Displayed diffs can be
truncated; full candidate files remain available.

Preparing again removes an older plan only when it has identical inputs, no
recorded resolutions and an unedited candidate. After an apply, plans prepared
against baselines other than the new one and the one it replaced are removed;
status offers to resume only a plan matching the current baseline.

Overlapping text edits reach the candidate as git merge-file --diff3 blocks.
Reviews list each block's local, original and upstream lines. Resolving block N
with local, upstream, base or both (local then upstream) rewrites only that block
through the same candidate exchange; the file's resolution, recorded as
"conflicts", is saved once no block remains. Malformed blocks are left as text,
so their markers still block apply.

File/directory collisions resolve as a path family. Choices are local, upstream,
original, deletion, an external replacement file, or an edited candidate. Resolution
hashes bind each conflict to its accepted file family; editing it again invalidates
the acceptance. Candidate updates use a whole-directory exchange. A crash before
the resolution record leaves an unresolved choice, not an accepted unknown result.
Resolution checks the candidate before and after exchange, restoring it when a
concurrent edit is detected and the replacement is still unchanged. Displaced
trees remain at `plans/<id>/.resolution-*/candidate` for the saved plan's lifetime,
so an editor holding an old file open can still save recoverable work. Such plans
are excluded from untouched-duplicate cleanup.

Structural validation checks schema, required fields, entry points, identity,
layout and bar placement. Conflict markers block apply. Qt 6 qmllint optionally
checks actual parse failures, without importing or executing candidate code.
Dynamic-host type warnings are not runtime validation. A missing linter is reported
explicitly. Failed/incomplete linter execution blocks review rather than implying
success.

Reviews compare current clone, baseline and upstream hashes with their snapshots
and mark changed inputs stale. Apply requires the candidate hash the user reviewed;
the panel additionally supplies a plan metadata hash.

## Git-installed plugins

A plugin directory with a .git directory, a manifest and no omarchy.clonedFrom is
a git plugin. Its original is the HEAD commit's tree, its local version is the
working tree without git-ignored files, and upstream is FETCH_HEAD when HEAD is
its ancestor; local commits upstream lacks are refused. Executable bits are
compared as git records them. Git runs with hooks and fsmonitor disabled and no
terminal prompts; fetch runs `git fetch origin HEAD`, as omarchy-plugin-update does.

Plans use the shared plan store with kind "git", recording HEAD, the target commit
and the branch; staleness compares those and the working tree. Apply copies the
whole directory, .git included, into a staging directory on the same filesystem,
updates the copy's branch (or detached HEAD) with update-ref from the reviewed
HEAD, resets its index with read-tree, replaces non-ignored working files with
the candidate, and verifies the copy before journaling. The journal at
git-transactions/<id>/<transaction>/ is written as prepared, the directories are
exchanged atomically, and it is marked applied; the displaced directory is the
undo backup. Undo requires the live plugin to still match the applied state and
the backup its original. Recovery marks an interrupted update applied or
abandoned (or undone) by comparing HEAD and the working tree with both sides.

## Agent-assisted resolution

ask-agent launches omarchy-agent-prompt, which opens the owner's chosen agent in
a terminal, with the plan's candidate directory as its working directory. The
prompt names the candidate file to edit and the base, local and upstream
snapshots as read-only context. Clone Keeper does not read the agent's output or
hold credentials; the owner accepts the edited candidate, which is reviewed,
syntax checked and confirmed before apply. It runs only on request, in the live
desktop, and requires a default agent.

## Shell changes and manifest validation

sync also records, once per release, the names each shell/Ui and shell/Commons
component declares (properties, signals and functions) at releases/.host/. Status
compares the fingerprint of a tracked clone's baseline release with the installed
shell and lists components the clone uses that are gone, or removed members it
uses as `.name`, `name:` or `name(`. This is a name comparison, not type checking;
additions are not reported. Baselines without a known release are not checked.

Reviews run the matching Omarchy's omarchy-plugin-validate on the candidate when
it is installed; it reads files with jq. Without it, the built-in check accepts
every bar section any release accepts, so newer layouts are not refused.

## Pruning

prune is a dry run unless --yes is given. Transactions beyond the newest N per
clone (default 3) are removed with their staging trees; undo steps back through
applied transactions, so fewer remain undoable. A clone with an unfinished journal
keeps all of them, and each removal rechecks its journal under the clone lock.
Plans for clones no longer installed are removed. Release records are kept when
they list any of the newest N versions in releases/index.json (default 3), or when
any untracked clone is cloned from their source. Removals rename to a hidden name
before deleting.

## Loading check after apply

apply --restart-shell, which the panel passes for an enabled live clone, runs in
the detached worker after a successful apply: Omarchy reloads the panel during
apply, and the worker's own session survives `quickshell kill`. It waits for
omarchy-restart-shell, allows plugin components time to load, then reads the new
instance's log with `qs log -p <omarchy>/shell`. Lines naming the clone's
directory or the shell's plugin-failure messages for its ID (bounded so a longer
ID does not match) are split into load failures and other messages. Load failures
undo the transaction and restart again; undo's own checks still refuse edits made
since apply. Other messages are reported only. A refused restart, such as while
the session is locked, keeps the update with a warning.

## Apply transaction

Per-clone flock locks serialize Clone Keeper writers. Omarchy source, installed
plugins and state must be separate trees. The implementation assumes a normal
single-user environment, not a sandbox against other processes with the same
permissions. It detects concurrent editor changes at the commit boundaries.
Staging copies are built under separate `.stage-*` scratch directories and only
published to the journal's staging paths once complete. Interrupted copies remain
outside those paths, allowing recovery to reconstruct them from verified backups.
Replacing an existing staging tree also uses an atomic exchange and retains the
displaced tree. Detached commands validate root separation before their worker
writes its first operation record.

Before durable intent:

1. Reload and validate the plan, resolutions, candidate hash and optional plan hash.
2. Reject unfinished operations; recheck clone, source and baseline.
3. Capture exact before-clone and before-state snapshots.
4. Build after-clone from the candidate and after-state from **upstream**.
5. Validate supported metadata and atomic-exchange capability.
6. Save and fsync all snapshots; recheck live inputs and the review.

Transaction journals live at transactions/<clone>/<uuid>/. They retain immutable
before-clone, before-state, after-clone and after-state inventories. Snapshot
hashes include ordinary file modes, directory modes and empty directories. Undo
restores exact pre-apply bytes, including noncanonical manifest formatting. When
the reviewed manifest is the generated canonical form, apply writes it with the
installed clone's key order and indentation; its parsed content is unchanged.

Git metadata, hardlinks, special file mode bits, xattrs/ACLs, symlinks and special files
are refused on apply because this version cannot preserve them faithfully.
Ordinary binary files and executable modes are supported. Ownership and timestamps
are not captured; snapshots and replacements are created as the current user.

The new clone stages beside the installed plugins directory, outside its watched
children; the new baseline stages under state/.staging. Linux RENAME_EXCHANGE
swaps each complete directory. Live clone and baseline swaps are individually
atomic; their combination is journaled and recoverable, not a single filesystem
transaction. Clone and baseline may temporarily represent different steps after
a crash. Further mutation is blocked until recovery.

Journal states:
```text
prepared → apply-clone → apply-state → applied
applied → undo-prepared → undo-clone → undo-state → undone
```

Recovery inspects actual live and displaced hashes, including interruption after
a swap but before its journal update. It finishes the intended operation only
when both live trees and all backups are recognized. Unknown edits or damaged
backups are left for inspection. Undo validates the latest applied transaction's
after-state before writing its own intent, then restores both before snapshots.

Completed journals and staging directories are retained until a reviewed prune.
There is no selective undo, force overwrite, or redo command in this version.

## Shell integration

After live mutations, a rescan request lets Omarchy reload enabled clones. The
shell also watches the installed plugins directory with inotify and reloads
plugins 150 ms after a change. A reload unloads all plugin widgets and panels,
including Clone Keeper, and Quickshell kills the process started by a destroyed
Process object. A panel request that changes a clone would therefore be killed
by its own first live swap or rescan.

The panel runs clone, enable, apply, undo and recover with --detach. The CLI
forks a worker into a new session, points its standard streams at /dev/null and
waits for it. The worker durably records operations/<request>.json as running
(with its PID and process start time) before the caller continues, then records
success or failure. If the caller is killed, the worker finishes and its record
remains. Status reports running, finished and interrupted (a running record whose
process no longer exists) operations; a reopened panel shows finished outcomes,
pauses changes while one is running, and dismisses outcomes once shown. The
worker still takes the clone lock and journals exactly as a foreground command.
A transport failure is a warning on an otherwise successful saved operation.
Custom plugin roots never issue live shell mutations. For the live plugins
directory, status reads activation state through the read-only listPlugins IPC
with a two-second timeout and reports it as unknown when unavailable.

Enable is separate and explicit. It checks pending operations under the clone
lock, requests a rescan, polls read-only registry discovery, and sends exactly
one enable request after the ID appears. The entire IPC interval has an eight-
second deadline. An unconfirmed activation is not reported as definitely disabled.

No automatic update applies, no network requests, no sudo and no candidate code
execution occur in the maintenance engine. Plugin execution begins through normal
Omarchy enable/reload behavior.

## Legacy preview/export

Preview remains an ephemeral three-way inspection. Export refuses conflicts and
structural errors, rechecks inputs and publishes a new directory outside managed
trees; neither command changes ancestry. Export does not perform the saved
review's optional syntax check.
