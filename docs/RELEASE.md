# Release preparation

## Current status

0.3.0 is a local beta with the full maintenance workflow. No GitHub repository,
release tag or marketplace issue has been created by this development workflow.
The proposed public repository is neilofneils404/clone-keeper. The installed plugin
ID remains neil.clone-keeper; check catalog uniqueness before publication.

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

Current guidance was checked on 2026-09-22:
[Submission guide](https://github.com/omacom/omarchy-plugin-marketplace/blob/main/SUBMISSION.md)
and [security model](https://github.com/omacom/omarchy-plugin-marketplace/blob/main/SECURITY.md).
The catalog requires a public GitHub repository, one root manifest, license and
dependency documentation, installation/removal instructions and a unique ID.
The proposed category is System, with tags system and quickshell.
An optional preview must meet the guide's current image limits.

## Marketplace issue draft

Target: omacom/omarchy-plugin-marketplace  
Title: [Plugin]: Clone Keeper

The URL below is proposed, not currently live. Check each statement against the
published repository before submitting; the owner must attest to ownership and
submission rights. The blank checklist is intentional until that review.

### Repository URL

https://github.com/neilofneils404/clone-keeper

### Category

System

### Tags

system, quickshell

### Suggest a missing tag

_No response_

### Maintainer notes

Clone Keeper maintains customized copies of Omarchy built-in plugins. It records
their original source, prepares persistent three-way reviews, supports explicit
conflict resolution, and applies the reviewed result with backups, undo and crash
recovery. New clones are disabled until explicitly enabled.

The root manifest declares one bar widget. The bundled Python CLI requires Linux,
Python 3.11+ and Git; Qt 6 qmllint is optional for candidate syntax checking.
There are no Python runtime packages, network requests or privileged operations.
Apply and undo affect only the selected clone and its own recorded state.
Activation uses supported Omarchy shell IPC after an explicit action.
Backups and saved reviews stay local and are retained until deliberately removed.

License: MIT. Independent community project. See README and docs/VALIDATION.md for
installation, removal, exact tested behavior and current limitations.

### Submission checklist

- [ ] The repository is public and contains installation and removal instructions.
- [ ] I have documented the plugin license and any external dependencies.
- [ ] I confirm that I own or have permission to submit this plugin and its preview assets.
- [ ] The plugin does not overwrite user configuration without explicit consent.
- [ ] I understand that approval is for listing and is not a security review.
