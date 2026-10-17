# Roadmap

## 0.3.0 local beta — implemented

- Directory and flat built-in source discovery, normalization and identity.
- Immutable, verifiable baselines with explicit provenance.
- Assisted disabled clone creation with ancestry captured first.
- Existing-clone tracking from current or historical original source.
- Persistent reviews, file navigation and three separate diff views.
- Text, binary, deletion, permission and file/directory conflict handling.
- Explicit side selection, replacement files and manual candidate editing.
- Stale-input detection, reviewed fingerprints and optional Qt 6 syntax checks.
- Atomic whole-directory apply, baseline advancement, durable undo and recovery.
- Native clone/create/review/history panel and bundled standalone CLI.
- Detached panel operations that survive plugin reload, with recorded outcomes.
- Isolated end-to-end, interruption, corruption and metadata tests.
- Disposable command-line demo, UI smoke harness and CI.

## Public release

See [the release checklist](RELEASE.md). A nested desktop pilot covered installation,
keyboard popup interaction, scaling, horizontal/vertical bars, disable/re-enable,
update and removal; address its findings in [validation](VALIDATION.md). Replacement
bars remain unchecked. Capture authentic release screenshots, then publish GitHub and
submit the marketplace listing.

## Later, driven by use

- Help locate original source from historical Omarchy releases.
- Explicit provenance for selected third-party forks.
- Contribute original source revision metadata to Omarchy's clone command.
- Optional update notifications with clear freshness and no automatic application.
- Richer semantic manifest merging if real conflicts justify it.

No cloud service or background auto-merge is planned.
