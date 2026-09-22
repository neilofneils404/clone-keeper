# Design

## Scope of the first alpha

The core CLI is independently useful without a running shell. The QML widget
uses its JSON output and has no write operations. This keeps desktop integration
separate from merge correctness.

Commands:

| Command | Reads | Writes |
| --- | --- | --- |
| `status` | Source manifests, clones, existing baselines | Nothing |
| `track` | Clone plus verified current source or supplied original | New baseline record |
| `preview` | Baseline, clone, current source | Temporary merge inputs only |
| `export` | Same inputs as preview | New review directory |

`--json` sends structured results to stdout. Failures return nonzero with a useful
stderr message. A preview containing merge conflicts is still a successful
inspection and returns zero; inspect `hasConflicts` and `validation.valid`.

## Source layouts

The installed `omarchy-plugin-catalog` discovers manifests at depths two through
four below `shell/plugins`. Clone Keeper mirrors that scope directly so it can
also inspect supplied fixture trees without shell IPC or invoking live commands.

For a standard `manifest.json`, copy the whole source tree into memory. For a flat
`*.manifest.json`, materialize declared entry points and `omarchy.clonePaths` in
target-name order. Mapped sources may reference sibling directories, but must
remain inside the built-in plugin tree. Targets must remain inside the candidate.
Text references are adjusted to match the native clone layout.

Unlike native `cp -aL`, the alpha rejects symlinks instead of dereferencing them.
This is an explicit compatibility limit. `.git` metadata is ignored.

## Baseline provenance

An unchanged clone can be verified against the current built-in after normalizing
generated clone identity fields. Its record says `verified-current-source`.
Already modified clones require a user-supplied original; their provenance says
`user-supplied-original`. Neither version labels nor file modification times prove
ancestry.

Each record lives at `state/clones/<id>/record.json`, alongside a `baseline/`
directory. It contains the clone/source IDs, capture time, baseline version label,
the normalized identity, every file's SHA-256 and mode, and an aggregate hash.
Reads verify the inventory before using a saved baseline.

The captured clone ID, display name and bar label are applied to upstream source
manifests for comparison. Later local name changes remain actual merge inputs.
The built-in identifiers inside QML are left unchanged, preserving Omarchy's
clone IPC routing. All other manifest fields participate in the merge.

## Merge semantics

For each path in the union of the three trees:

1. Identical local/upstream results are accepted.
2. If one side equals the baseline, take the other side, including a deletion.
3. Concurrent text changes use `git merge-file --diff3` with readable labels.
4. Different concurrent additions, delete/modify, and binary divergence conflict.
5. Permission changes are merged independently from content changes.

Manifest JSON is canonicalized before comparison, then merged as text. Adjacent
independent JSON edits can therefore require manual review. The alpha does not
attempt semantic QML or JSON merges.

The preview includes baseline-to-local, baseline-to-upstream, and local-to-candidate
diffs. Large displayed diffs are truncated; candidate contents remain complete.
Manifest validation checks schema, required fields, clone identity, entry points,
layout collisions, and bar placement. It does not prove the resulting QML runs.

## Export boundaries

Export recalculates a candidate, refuses conflicts and invalid structure, and
rechecks the clone, upstream, and baseline hashes before writing. The output is a new directory outside
managed trees. Files are staged privately, then published with an exclusive
directory reservation. Baseline state is never advanced by an export.

The alpha assumes the user's files are not being maliciously replaced by another
process during inspection. It rejects detected symlinks and uses `O_NOFOLLOW`
for file reads; it is not a sandbox against other code running as the same user.

## Future apply transaction

Applying needs a separate design because Omarchy reloads watched plugin files.
A correct implementation must stage the whole validated candidate, recheck local,
baseline, and upstream hashes, retain the original tree, commit without exposing
a partial directory, and advance the baseline to the *new upstream*, not the
merged local result. A persistent journal must permit recovery after interruption.
Undo must refuse to overwrite edits made after the apply operation.

These requirements are release work, not promises made by the current export
command. No alpha UI button implies that changes have been applied.
