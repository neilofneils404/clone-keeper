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
- applied-plan: a reviewed apply advances the ancestor to that plan's upstream.

Version labels and modification times never establish ancestry. Repeated tracking
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

Completed journals and staging directories are retained. There is no automatic
pruning, selective undo, force overwrite, or redo command in this version.

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
