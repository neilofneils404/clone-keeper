# Roadmap

## Local alpha — implemented

- Native clone discovery and source-layout normalization.
- Immutable baselines with recorded provenance and content hashes.
- Local/upstream change detection.
- Three-way preview with text, binary, delete/modify and mode handling.
- Conflict-free candidate export and structural validation.
- Read-only native panel with local, upstream, and proposed-merge diff views,
  CLI integration tests, and CI configuration.

## Next: complete the maintenance loop

1. Make baseline capture available from the panel, explaining historical sources.
2. Add an assisted new-clone workflow that records ancestry at creation time.
3. Design and test journaled apply/undo, including interruption and stale previews.
4. Advance the baseline to the new upstream after successful application.
5. Add conflict-resolution staging with an external editor and explicit validation.
6. Add a dedicated file navigator for larger multi-file previews.

## Before GitHub release and marketplace submission

- Review the current official plugin submission and registry requirements.
- Verify the complete workflow in a disposable real Omarchy desktop: startup,
  native bar, replacement bars where supported, vertical layouts, text scaling,
  keyboard access, disable/re-enable, update and removal.
- Test apply/undo on several representative clones and malformed/crash scenarios.
- Add authentic screenshots and a short demonstration; do not present fixtures as
  screenshots of a production-ready release.
- Finalize repository name, namespace, release notes and supported versions.
- Publish the repository and submit its reviewed commit to the marketplace.

## Later

- Assist with locating original baselines in historical Omarchy releases.
- Support selected third-party plugin forks with explicit upstream provenance.
- Contribute source-revision metadata to Omarchy's native clone command.
- Optional update notifications with explicit freshness and minimal background work.

No GitHub remote or marketplace listing is created by the local alpha workflow.
