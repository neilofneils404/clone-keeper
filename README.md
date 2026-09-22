# Clone Keeper

**Your customizations. Upstream improvements.**

Clone Keeper tracks the original source of your customized Omarchy plugins,
compares your edits with newer built-ins, and prepares three-way merge candidates
for review.

**Local alpha · 0.1.0.** Baseline capture, change detection, merge preview, and
candidate export work. This version does not apply changes to installed plugins
or advance an existing baseline. The Omarchy panel is a read-only companion.

## Why it exists

Omarchy makes it easy to copy a built-in plugin and make it your own. The clone
remembers its source ID, but it does not retain the original source files.
After an Omarchy update, your copy and the built-in can evolve independently.

Clone Keeper preserves that missing ancestor. It compares three trees:

```text
Recorded original ──┬── Your customized clone
                    └── Current Omarchy built-in
                              ↓
                     Reviewable merge candidate
```

File contents and permissions determine changes; manifest versions alone do not.
Clone IDs and IPC source relationships remain intact.

## What works today

- Discover personal plugins with `omarchy.clonedFrom` metadata.
- Capture an unchanged clone's baseline, or adopt a known historical original.
- Show local changes, upstream changes, and their proposed combined result.
- Merge independent text edits with Git's three-way merge engine.
- Surface overlapping edits, binary changes, and deletion conflicts.
- Preserve added files, deleted files, and executable permissions.
- Validate candidate manifests and export clean candidates outside live folders.
- Browse clone status and merge previews in a native Omarchy bar panel.

Regular plugin directories and Omarchy's flat `*.manifest.json` built-ins are
supported, including their declared `clonePaths` mappings.

## Run from this checkout

Requirements: Python 3.11 or newer and Git. Omarchy is needed for the native UI
and real source discovery; the tests and demo work without a running desktop.
There are no Python runtime packages to install.

```bash
./bin/clone-keeper --help
./bin/clone-keeper status
```

### Start tracking a new clone

Use Omarchy to create your clone, then record its baseline **before editing**:

```bash
omarchy plugin clone omarchy.clock
./bin/clone-keeper track "$USER.clock"
```

The first command is Omarchy's normal cloning workflow: it creates and enables
your clock clone. Clone Keeper's `track` command writes only its own state.

After you customize the plugin and Omarchy updates its original:

```bash
./bin/clone-keeper status
./bin/clone-keeper preview "$USER.clock"
./bin/clone-keeper export "$USER.clock" --output /tmp/clock-candidate
omarchy plugin validate /tmp/clock-candidate
```

The export directory must not exist. Export refuses conflicts, invalid manifests,
and destinations within installed plugins, Omarchy source, or Clone Keeper state.
It does not install the candidate or reset your baseline. Validation covers the
manifest and file layout; a clean merge still needs QML and runtime review.

### Adopt an already customized clone

Provide the original source directory from the version you actually cloned:

```bash
./bin/clone-keeper track "$USER.clock" --baseline /path/to/original-clock
```

That directory must contain the original `manifest.json`, original source ID,
and original files. It must be a materialized standalone plugin directory. For
historical flat manifests, recreate their original clone layout first.

Clone Keeper records this as a user-supplied baseline; it cannot verify its
historical ancestry. If you do not have the original, keep the clone untracked
until its ancestry can be established. Today's upstream source is not a reliable
substitute for a missing historical original.

Repeated `track` calls refuse to overwrite a recorded baseline.

### Native panel

The root manifest declares the `neil.clone-keeper` bar widget. Its panel supports
refresh, clone selection, and merge previews. Baseline capture and export are
currently CLI operations. The widget refreshes at startup and when opened;
it does not poll, fetch code, or install anything in the background.

Development checks do not require installing the plugin:

```bash
omarchy plugin validate .
scripts/check-ui.sh
```

The check script selects Qt 6's linter; the unqualified `qmllint` can be Qt 5 on
Arch. Some dynamic Omarchy/Quickshell objects produce type warnings, so runtime
verification is still needed.

An installed copy must contain the entire repository's runtime files, including
the executable `bin/clone-keeper` and `clone_keeper/` Python module. Normal
Omarchy installation instructions will be added with the public repository.

## State and boundaries

| Location | Use |
| --- | --- |
| `$OMARCHY_PATH/shell/plugins` (default `/usr/share/omarchy/shell/plugins`) | Read built-in source |
| `~/.config/omarchy/plugins` | Read your installed clones |
| `$XDG_STATE_HOME/clone-keeper` (default `~/.local/state/clone-keeper`) | Store immutable baselines and provenance |
| An explicit new export directory | Write a review candidate |

Override paths for testing, before the subcommand:

```bash
./bin/clone-keeper --omarchy-path /tmp/example/omarchy \
  --plugins-dir /tmp/example/plugins --state-dir /tmp/example/state status --json
```

Status and previews leave sources, installed plugins, and baseline state unchanged.
The CLI does not execute plugin code, make network requests, use sudo, or modify
`shell.json`. Git internals are excluded from comparisons and exports. Symlinks
and special files are rejected. The initial limits are 3,000 files, 10 MiB per
file, and 50 MiB per plugin.

The displayed Omarchy source-version string is diagnostic context. Some packages
retain an old version-file label; recorded content hashes determine changes.

## Development

```bash
python -m unittest discover -s tests -v
python scripts/demo.py
```

The demo builds a disposable synthetic Omarchy tree, introduces local and
upstream edits, and demonstrates a merge without using your installed plugins.
CI runs the integration suite on Python 3.11 and 3.14.

See [the design](docs/DESIGN.md) and [the roadmap](docs/ROADMAP.md) for the next
steps toward a public release.

MIT licensed. Independent community project; not an official Omarchy component.
