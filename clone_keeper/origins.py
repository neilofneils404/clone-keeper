"""Locate a clone's original source among Omarchy releases kept on this machine.

Evidence only suggests an origin; the owner confirms it. Package archives and
installed trees are read, never executed, and extracted into private scratch
directories that are removed afterwards.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import difflib
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .core import (File, KeeperError, Keeper, as_clone, atomic_new_tree, changed_files,
                   clone_identity, inventory, is_text, json_bytes, no_symlink_path,
                   object_json, plugin_id, read_file, read_tree, tree_hash)

PACKAGES = ("omarchy", "omarchy-dev")
PACKAGE_FILE = re.compile(r"^(omarchy(?:-dev)?)-([^-]+-[^-]+)-[^-]+\.pkg\.tar\.(?:zst|xz)$")
LOG_LINE = re.compile(r"^\[([^\]]+)\] \[ALPM\] (installed|upgraded|downgraded|reinstalled|removed) "
                      r"(omarchy(?:-dev)?) \(([^)]*)\)")
EXTRACT = ("usr/share/omarchy/shell/plugins", "usr/share/omarchy/version")


@dataclass
class Release:
    """One Omarchy tree that may hold a clone's original source."""
    version: str
    kind: str  # pacman-cache, installed, supplied
    path: Path
    sha256: str | None = None
    tree: dict | None = field(default=None, repr=False)


def pacman_timeline(log: Path) -> list[tuple[datetime, str | None]]:
    """Omarchy package versions in effect from each logged transaction onward."""
    events = []
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return events
    for line in lines:
        match = LOG_LINE.match(line)
        if not match:
            continue
        stamp, action, _, versions = match.groups()
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if when.tzinfo is None:
            continue
        events.append((when, None if action == "removed" else versions.split(" -> ")[-1].strip()))
    return sorted(events, key=lambda item: item[0])


def version_at(timeline, when: datetime) -> str | None:
    current = None
    for stamp, version in timeline:
        if stamp > when:
            break
        current = version
    return current


def birth_time(path: Path) -> datetime | None:
    """Creation time of a directory, when the filesystem records one."""
    try:
        process = subprocess.run(["stat", "-c", "%W", str(path)], capture_output=True, text=True, timeout=5)
        seconds = int(process.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return datetime.fromtimestamp(seconds, timezone.utc) if process.returncode == 0 and seconds > 0 else None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def cached_packages(cache: Path) -> list[Release]:
    releases = []
    if cache.is_dir():
        for path in sorted(cache.iterdir()):
            match = PACKAGE_FILE.match(path.name)
            if match and path.is_file() and not path.is_symlink():
                releases.append(Release(match.group(2), "pacman-cache", path))
    return releases


def supplied(path: Path) -> Release:
    """An Omarchy tree (such as a release checkout) or a package archive."""
    path = path.absolute()
    match = PACKAGE_FILE.match(path.name)
    if match and path.is_file():
        return Release(match.group(2), "supplied", path)
    if (path / "shell" / "plugins").is_dir():
        version = path / "version"
        label = version.read_text(encoding="utf-8").strip() if version.is_file() else path.name
        return Release(label, "supplied", path)
    raise KeeperError(f"Not an Omarchy tree or package archive: {path}")


def _release_sources(release: Release, scratch: Path):
    if release.path.is_dir():
        return Keeper(release.path, scratch / "unused-plugins", scratch / "unused-state").sources()
    if not shutil.which("bsdtar"):
        raise KeeperError("bsdtar (libarchive) is required to read package archives")
    target = scratch / "extract"
    target.mkdir()
    # bsdtar refuses absolute and parent-relative members unless -P is given.
    process = subprocess.run(["bsdtar", "-xf", str(release.path), "-C", str(target), *EXTRACT],
                             capture_output=True, text=True, timeout=120)
    if process.returncode:
        raise KeeperError(f"Could not read {release.path.name}: {process.stderr.strip()}")
    release.sha256 = sha256(release.path)
    return Keeper(target / "usr/share/omarchy", scratch / "unused-plugins", scratch / "unused-state").sources()


def changed_lines(before, after) -> int:
    total = 0
    for name in changed_files(before, after):
        old, new = before.get(name), after.get(name)
        if (old and not is_text(old.data)) or (new and not is_text(new.data)):
            total += 1
            continue
        a = old.data.decode(errors="replace").splitlines() if old else []
        b = new.data.decode(errors="replace").splitlines() if new else []
        total += sum(1 for line in difflib.unified_diff(a, b, lineterm="", n=0)
                     if line[:1] in "+-" and line[:3] not in ("+++", "---"))
    return total


class ReleaseStore:
    """Built-in sources of each Omarchy release seen running, kept past upgrades.

    Trees are stored once per content hash at releases/<source id>/<hash>/ and
    list every release version that shipped them.
    """

    def __init__(self, keeper: Keeper):
        self.keeper = keeper
        self.root = keeper.state_dir / "releases"

    def record(self) -> dict:
        """Save the installed release's sources; another recorder running is fine."""
        self.keeper.protect_destination(self.root, state=True)
        no_symlink_path(self.keeper.state_dir)
        self.keeper.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        version = self.keeper.version()
        added, errors = [], []
        lock = os.open(self.keeper.state_dir / ".releases.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"version": version, "recorded": [], "errors": [], "skipped": True}
            for source_id, source in sorted(self.keeper.sources().items()):
                try:
                    if self._record_one(source_id, source.materialize(), version):
                        added.append(source_id)
                except (KeeperError, OSError, ValueError) as exc:
                    errors.append({"path": str(source.path), "message": str(exc)})
            try:
                from . import host
                host.record(self.keeper, version)
            except (KeeperError, OSError, ValueError) as exc:
                errors.append({"path": str(self.keeper.omarchy_path / "shell"), "message": str(exc)})
            try:
                order = self.versions()
                if version not in order:
                    from .plans import atomic_json
                    atomic_json(self.root / "index.json", {"schemaVersion": 1, "versions": order + [version]})
            except (KeeperError, OSError, ValueError) as exc:
                errors.append({"path": str(self.root / "index.json"), "message": str(exc)})
        finally:
            os.close(lock)
        return {"version": version, "recorded": added, "errors": errors, "skipped": False}

    def versions(self) -> list[str]:
        """Release versions in the order they were first seen running."""
        path = self.root / "index.json"
        no_symlink_path(path)
        if not path.exists():
            return []
        index = object_json(read_file(path).data, str(path))
        if index.get("schemaVersion") != 1 or not isinstance(index.get("versions"), list):
            raise KeeperError(f"Invalid release index: {path}")
        return [str(item) for item in index["versions"]]

    def records(self):
        """Every stored release record as (path, source id, metadata)."""
        no_symlink_path(self.root)
        if not self.root.is_dir():
            return
        for source in sorted(self.root.iterdir()):
            if source.name.startswith(".") or not source.is_dir():
                continue
            for path in sorted(source.iterdir()):
                if not path.name.startswith("."):
                    yield path, source.name, self._meta(path, source.name, path.name)

    def _record_one(self, source_id, tree, version):
        digest = tree_hash(tree)
        path = self.root / plugin_id(source_id) / digest
        if path.exists():
            meta = self._meta(path, source_id, digest)
            if version in meta["versions"]:
                return False
            from .plans import atomic_json
            meta["versions"].append(version)
            atomic_json(path / "record.json", meta)
            return False
        meta = {"schemaVersion": 1, "sourceId": source_id, "treeHash": digest, "versions": [version],
                "recordedAt": datetime.now(timezone.utc).isoformat(), "files": inventory(tree)}
        atomic_new_tree(path, {"record.json": File(json_bytes(meta), 0o600),
                               **{"tree/" + name: file for name, file in tree.items()}})
        return True

    @staticmethod
    def _meta(path, source_id, digest):
        meta = object_json(read_file(path / "record.json").data, str(path))
        if meta.get("schemaVersion") != 1 or meta.get("sourceId") != source_id or meta.get("treeHash") != digest \
                or not isinstance(meta.get("versions"), list):
            raise KeeperError(f"Invalid release record: {path}")
        return meta

    def trees(self, source_id):
        """Verified (record, tree) pairs saved for one built-in."""
        directory = self.root / plugin_id(source_id)
        no_symlink_path(directory)
        if not directory.is_dir():
            return
        for path in sorted(directory.iterdir()):
            if path.name.startswith("."):
                continue
            meta = self._meta(path, source_id, path.name)
            tree = read_tree(path / "tree")
            if inventory(tree) != meta["files"] or tree_hash(tree) != meta["treeHash"]:
                raise KeeperError(f"Release record integrity check failed: {path}")
            yield path, meta, tree


class Origins:
    def __init__(self, keeper: Keeper, package_cache: Path, pacman_log: Path):
        self.keeper = keeper
        self.package_cache = package_cache
        self.pacman_log = pacman_log

    def releases(self, extra=(), packages=True) -> list[Release]:
        installed = Release(self.keeper.version(), "installed", self.keeper.omarchy_path)
        return [installed, *(cached_packages(self.package_cache) if packages else ()), *map(supplied, extra)]

    def _originals(self, source_id, extra, packages, problems):
        """Yield (release description, materialized source) for every place it is kept."""
        for release in self.releases(extra, packages):
            with tempfile.TemporaryDirectory(prefix="clone-keeper-origin-") as scratch:
                try:
                    source = _release_sources(release, Path(scratch)).get(source_id)
                    tree = source.materialize() if source else None
                except (KeeperError, OSError, subprocess.TimeoutExpired) as exc:
                    problems.append({"path": str(release.path), "message": str(exc)})
                    continue
            if tree is not None:
                yield [{"version": release.version, "kind": release.kind, "path": str(release.path),
                        "sha256": release.sha256}], tree
        try:
            for path, meta, tree in ReleaseStore(self.keeper).trees(source_id):
                yield [{"version": version, "kind": "recorded", "path": str(path), "sha256": None}
                       for version in meta["versions"]], tree
        except (KeeperError, OSError) as exc:
            problems.append({"path": str(self.keeper.state_dir / "releases"), "message": str(exc)})

    def find(self, clone_id: str, extra=()) -> dict:
        """Rank original candidates for a clone by how much of it they explain."""
        return self._find(clone_id, extra)[0]

    def _find(self, clone_id, extra, packages=True):
        path, local, value, source_id = self.keeper.clone(clone_id)
        identity = clone_identity(value)
        timeline = pacman_timeline(self.pacman_log)
        created = birth_time(path)
        expected = version_at(timeline, created) if created and timeline else None
        groups, trees, problems = {}, {}, []
        for releases, original in self._originals(source_id, extra, packages, problems):
            try:
                base = as_clone(original, identity, source_id)
            except KeeperError as exc:
                problems.append({"path": releases[0]["path"], "message": str(exc)})
                continue
            digest = tree_hash(base)
            trees[digest] = base
            groups.setdefault(digest, {"baselineHash": digest, "releases": []})["releases"].extend(releases)
        candidates = []
        for group in groups.values():
            base = trees[group["baselineHash"]]
            differing = changed_files(base, local)
            versions = {item["version"] for item in group["releases"]}
            candidates.append({**group, "versions": sorted(versions),
                               "timelineMatch": expected in versions if expected else None,
                               "unchangedFiles": sum(1 for name in base if local.get(name) == base[name]),
                               "files": len(base), "changedFiles": differing,
                               "changedLines": changed_lines(base, local)})
        candidates.sort(key=lambda item: (not item["timelineMatch"], item["changedLines"], -item["unchangedFiles"]))
        if candidates and (expected is None or candidates[0]["timelineMatch"]):
            candidates[0]["suggested"] = True
        if expected and not any(item["timelineMatch"] for item in candidates):
            problems.append({"path": str(self.package_cache),
                             "message": f"Omarchy {expected} was installed when this clone was created, but its "
                                        "source is not available here. Supply that release's tree or package."})
        return ({"cloneId": clone_id, "sourceId": source_id, "clonePath": str(path),
                 "cloneCreatedAt": created.isoformat() if created else None,
                 "installedAtCreation": expected, "candidates": candidates, "errors": problems}, trees)

    def exact(self, clone_id: str):
        """A recorded release this clone is byte-identical to, without reading packages.

        Merging from an ancestor equal to the clone is a fast-forward whichever
        release that tree is labelled with, so no confirmation is needed.
        """
        found, trees = self._find(clone_id, (), packages=False)
        match = next((item for item in found["candidates"] if not item["changedFiles"]), None)
        if match is None:
            return None, found["errors"]
        evidence = {"versions": match["versions"], "releases": match["releases"], "exactMatch": True}
        return (trees[match["baselineHash"]], "Omarchy " + match["versions"][-1], evidence), found["errors"]

    def baseline(self, clone_id: str, version: str, expect: str, extra=()):
        """Re-find the confirmed candidate so the stored tree is exactly what was shown."""
        found, trees = self._find(clone_id, extra)
        chosen = next((item for item in found["candidates"] if version in item["versions"]), None)
        if chosen is None:
            raise KeeperError(f"No original source for Omarchy {version} was found for {clone_id}")
        if chosen["baselineHash"] != expect:
            raise KeeperError("That release's source no longer matches the reviewed candidate; look again")
        evidence = {"versions": chosen["versions"], "releases": chosen["releases"],
                    "cloneCreatedAt": found["cloneCreatedAt"], "installedAtCreation": found["installedAtCreation"],
                    "timelineMatch": chosen["timelineMatch"], "unchangedFiles": chosen["unchangedFiles"],
                    "changedFiles": chosen["changedFiles"]}
        return trees[chosen["baselineHash"]], evidence
