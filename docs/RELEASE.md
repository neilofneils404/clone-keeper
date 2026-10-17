# Release preparation

## Current status

0.4.0 is a local beta with the full maintenance workflow. No GitHub repository,
release tag or marketplace issue has been created by this development workflow.
The proposed public repository is neilofneils404/clone-keeper. The installed plugin
ID remains neil.clone-keeper; check catalog uniqueness before publication.
A nested desktop pilot ran on 2026-09-27; its results and open findings are in
[validation](VALIDATION.md).

## Final desktop pilot

Use a disposable Omarchy desktop and synthetic clones. Keep installed system
source read-only.

- Install the committed repository through omarchy plugin add.
- Open/close the real anchored panel; use keyboard-only navigation and confirmation.
- Check horizontal and vertical bars, supported replacement bars, narrow screens,
  larger text, scrolling, focus visibility and Escape behavior.
- Create/enable a clone, capture an existing baseline, save/resume a review, resolve
  conflicting files, apply, verify runtime behavior, undo and inspect history.
- Start Enable, Apply and Undo from the panel and let Omarchy reload it; reopen and
  confirm each outcome is shown once, the change completed and no recovery is pending.
  Repeat Apply under heavy disk load.
- Exercise disable/re-enable, update and removal; confirm state and other clones remain.
- Capture preview assets from the tested build. Label synthetic demonstration
  content honestly. Never publish real private clone diffs or filesystem data.

The automated harness complements this pilot; it does not replace actual native
popup, compositor, and installation lifecycle checks. Record findings in VALIDATION.md.

## Publish

1. Run the documented unit, CLI demo, native lint and UI smoke checks.
2. Review tracked files, dependencies, license and supported-version statements.
3. Create the public repository and push the reviewed commit. Add its actual URL to
   the README's installation command and verify a clean install.
4. Add authentic preview.png or preview.webp at the root if desired.
5. Tag the tested beta/release, describing known limits and backup retention.
6. Review and submit the marketplace issue below; track its outcome rather than
   equating submission with approval.

Current guidance was checked on 2026-10-17:
[Submission guide](https://github.com/omacom/omarchy-plugin-marketplace/blob/main/SUBMISSION.md)
and [security model](https://github.com/omacom/omarchy-plugin-marketplace/blob/main/SECURITY.md).
The marketplace is published at https://plugins.omarchy.org (moved there on
2026-08-30). It requires a public GitHub repository root, one root manifest, a root
README with installation and removal, a root license, documented dependencies and a
globally unique, permanent ID; `neil.clone-keeper` matches the owner's existing
`neil.*` listings and is unused. An optional root `preview.png` is processed by the
marketplace. Submissions get compatibility validation and an automated static
security baseline on the exact commit, then maintainer approval
(`approved-and-verified`). Neither is a security review. Updates to a listing go
through the Plugin verification issue form with the new full commit SHA.

## Marketplace issue

Target: omacom/omarchy-plugin-marketplace
Title: [Plugin]: Clone Keeper

The body follows SUBMISSION.md exactly: six headings in order, one category, one to
three allowed tags, and the five checklist statements, checked only after the owner
confirms each one.

```markdown
### Repository URL

https://github.com/neilofneils404/clone-keeper

### Category

System

### Tags

system, quickshell

### Suggest a missing tag

_No response_

### Maintainer notes

Clone Keeper keeps customized copies of Omarchy built-in plugins up to date. It records each clone's original source, merges newer built-ins with the owner's edits in a reviewed three-way merge with per-conflict choices, and applies the confirmed result atomically with backups, undo and crash recovery. It can find an existing clone's original among Omarchy releases on the machine (installed tree, pacman cache), and it also updates git-installed plugins the owner has edited, which `omarchy plugin update` refuses.

The root manifest declares one bar widget. A bundled Python CLI needs Linux, Python 3.11+, Git and bsdtar (libarchive, part of pacman); Qt 6 qmllint is optional. No Python packages, sudo or background network access. Its only network request is `git fetch origin HEAD` for an edited git-installed plugin, when the owner chooses Check for updates. It never executes plugin code itself; after an apply to an enabled clone it restarts the shell through omarchy-restart-shell, reads the shell log and undoes the update if the clone failed to load. Optional hand-offs open the owner's editor or default coding agent on request. State, backups and saved reviews stay local until pruned on request.

License: MIT. Independent community project. Tested on Omarchy 4.0.4; see README and docs/VALIDATION.md for exact tested behavior and limits.

### Submission checklist

- [ ] The repository is public and contains installation and removal instructions.
- [ ] I have documented the plugin license and any external dependencies.
- [ ] I confirm that I own or have permission to submit this plugin and its preview assets.
- [ ] The plugin does not overwrite user configuration without explicit consent.
- [ ] I understand that approval is for listing and is not a security review.
```

## Upstream contribution

A proposed change to basecamp/omarchy makes `omarchy plugin clone` record
`omarchy.clonedFromVersion`, the Omarchy release a clone was copied from. Clone
Keeper already reads it as clone identity and as origin evidence.
