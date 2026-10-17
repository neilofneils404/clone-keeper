"""Filesystem-only discovery, immutable baselines, and conservative tree merges.

Nothing in this module executes plugin code or writes to an installed plugin.
Omarchy's shipped cloning layout is materialized before comparing any files.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone


class KeeperError(Exception):
    """An actionable input, state, or filesystem error."""


@dataclass(frozen=True)
class File:
    data: bytes
    mode: int = 0o644


Tree = dict[str, File]
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TREE_BYTES = 50 * 1024 * 1024
MAX_FILES = 3000
MAX_DIFF_CHARS = 200_000
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
KINDS = {"bar": "bar", "bar-widget": "barWidget", "panel": "panel",
         "overlay": "overlay", "menu": "menu", "service": "service"}


def plugin_id(value: str) -> str:
    if not ID_PATTERN.fullmatch(value) or ".." in value:
        raise KeeperError(f"Invalid plugin ID: {value!r}")
    return value


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def object_json(data: bytes, label: str) -> dict:
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise KeeperError(f"Invalid JSON in {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise KeeperError(f"Expected a JSON object in {label}")
    return value


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        raise KeeperError(f"Invalid relative file path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value == ".":
        raise KeeperError(f"Path must stay inside the plugin: {value!r}")
    return str(path)


def is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def no_symlink_path(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise KeeperError(f"Symlinks are not supported: {part}")


def read_file(path: Path) -> File:
    # O_NOFOLLOW closes the final-component symlink race. Directory traversal is
    # rejected separately; this tool is not an isolation boundary for hostile peers.
    no_symlink_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise KeeperError(f"Only regular files are supported: {path}")
        if info.st_size > MAX_FILE_BYTES:
            raise KeeperError(f"File exceeds the 10 MiB limit: {path}")
        data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise KeeperError(f"File exceeds the 10 MiB limit: {path}")
        return File(data, stat.S_IMODE(info.st_mode) & 0o777)


def check_limits(tree: Tree) -> None:
    if len(tree) > MAX_FILES or sum(len(f.data) for f in tree.values()) > MAX_TREE_BYTES:
        raise KeeperError("Plugin exceeds the limit of 3,000 files or 50 MiB")


def read_tree(root: Path) -> Tree:
    no_symlink_path(root)
    if not root.is_dir():
        raise KeeperError(f"Plugin directory not found: {root}")
    tree = {}
    def raise_walk_error(error: OSError) -> None:
        raise error

    for parent, directories, filenames in os.walk(root, followlinks=False, onerror=raise_walk_error):
        directories[:] = sorted(d for d in directories if d != ".git")
        for name in directories:
            path = Path(parent) / name
            if path.is_symlink():
                raise KeeperError(f"Symlinks are not supported: {path}")
        for name in sorted(filenames):
            if name == ".git":
                continue
            path = Path(parent) / name
            tree[path.relative_to(root).as_posix()] = read_file(path)
            check_limits(tree)
    return tree


def manifest(tree: Tree) -> dict:
    if "manifest.json" not in tree:
        raise KeeperError("Plugin is missing manifest.json")
    return object_json(tree["manifest.json"].data, "manifest.json")


def canonical_tree(tree: Tree) -> Tree:
    result = dict(tree)
    value = manifest(tree)
    result["manifest.json"] = File(json_bytes(value), tree["manifest.json"].mode)
    return result


def _ordered_like(value, template):
    if isinstance(value, dict) and isinstance(template, dict):
        keys = [key for key in template if key in value] + [key for key in value if key not in template]
        return {key: _ordered_like(value[key], template.get(key)) for key in keys}
    if isinstance(value, list) and isinstance(template, list):
        return [_ordered_like(item, template[index] if index < len(template) else None)
                for index, item in enumerate(value)]
    return value


def styled_manifest(candidate: File, previous: File | None) -> File:
    """Write a generated manifest in the installed clone's key order and indentation.

    Comparisons use canonical JSON, so the reviewed content is unchanged. A
    candidate manifest whose bytes the user wrote by hand is kept exactly.
    """
    try:
        value = json.loads(candidate.data)
        template = json.loads(previous.data) if previous else None
    except (ValueError, UnicodeError):
        return candidate
    if template is None or candidate.data != json_bytes(value):
        return candidate
    text = previous.data.decode()
    indented = next((line for line in text.splitlines()[1:] if line[:1] in (" ", "\t")), None)
    if indented is None:
        indent = None
    elif indented.startswith("\t"):
        indent = "\t"
    else:
        indent = len(indented) - len(indented.lstrip(" "))
    data = json.dumps(_ordered_like(value, template), indent=indent, ensure_ascii=False)
    if text.endswith("\n"):
        data += "\n"
    return File(data.encode(), candidate.mode)


def inventory(tree: Tree) -> dict:
    return {path: {"sha256": hashlib.sha256(file.data).hexdigest(), "mode": file.mode}
            for path, file in sorted(tree.items())}


def tree_hash(tree: Tree) -> str:
    return hashlib.sha256(json_bytes(inventory(tree))).hexdigest()


def changed_files(before: Tree, after: Tree) -> list[str]:
    return sorted(path for path in before.keys() | after.keys()
                  if before.get(path) != after.get(path))


def write_tree(root: Path, tree: Tree) -> None:
    for name, file in sorted(tree.items()):
        path = root / safe_relative(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as out:
            out.write(file.data)
        path.chmod(file.mode)


def atomic_new_tree(destination: Path, tree: Tree) -> None:
    """Publish a complete durable directory without an empty reservation window."""
    from .transactions import _fsync_directory, publish_directory
    no_symlink_path(destination)
    if destination.exists():
        raise KeeperError(f"Destination already exists: {destination}")
    # Create one level at a time so an existing file or a symlink substituted by
    # another writer cannot silently become part of our destination hierarchy.
    missing = []
    parent = destination.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    no_symlink_path(parent)
    if not parent.is_dir():
        raise KeeperError(f"Destination parent is not a directory: {parent}")
    for directory in reversed(missing):
        no_symlink_path(directory)
        try:
            directory.mkdir()
        except FileExistsError:
            no_symlink_path(directory)
            if not directory.is_dir():
                raise KeeperError(f"Destination parent is not a directory: {directory}")
    # Persist every ancestor entry before publication. The existing part of the
    # chain matters too: callers may have just created state_dir themselves.
    for directory in (destination.parent, *destination.parent.parents):
        no_symlink_path(directory)
        _fsync_directory(directory)
    with tempfile.TemporaryDirectory(prefix=".clone-keeper-", dir=destination.parent) as tmp:
        stage = Path(tmp) / "tree"
        stage.mkdir(mode=0o700)
        write_tree(stage, tree)
        publish_directory(stage, destination)


@dataclass
class Source:
    path: Path
    root: Path

    def materialize(self) -> Tree:
        """Mirror the installed Omarchy clone command's two layout formats."""
        source_file = read_file(self.path)
        value = object_json(source_file.data, str(self.path))
        if self.path.name == "manifest.json":
            result = read_tree(self.path.parent)
        else:
            result = {"manifest.json": source_file}
            entries = value.get("entryPoints", {})
            omarchy = value.get("omarchy", {})
            if not isinstance(omarchy, dict):
                raise KeeperError(f"Manifest omarchy metadata must be an object: {self.path}")
            extra = omarchy.get("clonePaths", [])
            if not isinstance(entries, dict) or not isinstance(extra, list):
                raise KeeperError(f"Invalid clone paths in {self.path}")
            mappings = [{"source": item, "target": item} for item in entries.values()] + extra
            by_target = {}
            for mapping in mappings:
                if not isinstance(mapping, dict) or not isinstance(mapping.get("source"), str):
                    raise KeeperError(f"Invalid clone mapping in {self.path}")
                target = safe_relative(mapping.get("target"))
                # jq unique_by(.target) in Omarchy retains the first mapping.
                by_target.setdefault(target, mapping["source"])
            for target, source in sorted(by_target.items()):
                original_path = self.path.parent / source
                no_symlink_path(original_path)
                source_path = original_path.resolve()
                if not is_within(source_path, self.root.resolve()):
                    raise KeeperError(f"Clone source escapes the source tree: {source}")
                if source_path.is_dir():
                    files = {f"{target}/{name}": file for name, file in read_tree(source_path).items()}
                else:
                    files = {target: read_file(source_path)}
                result.update(files)
                if source != target:
                    for name, file in list(result.items()):
                        if is_text(file.data):
                            result[name] = File(file.data.replace(source.encode(), target.encode()), file.mode)
            check_limits(result)
        # A materialized source no longer requires clonePaths; keep its original ID.
        value = manifest(result)
        if isinstance(value.get("omarchy"), dict):
            value["omarchy"].pop("clonePaths", None)
        result["manifest.json"] = File(json_bytes(value), result["manifest.json"].mode)
        return result


def clone_identity(value: dict) -> dict:
    identity = {"id": value["id"], "name": value.get("name", value["id"])}
    if isinstance(value.get("barWidget"), dict) and "displayName" in value["barWidget"]:
        identity["displayName"] = value["barWidget"]["displayName"]
    return identity


def as_clone(source: Tree, identity: dict, source_id: str) -> Tree:
    result = dict(source)
    value = manifest(source)
    if value.get("id") != source_id:
        raise KeeperError(f"Baseline/source ID {value.get('id')!r} does not match {source_id}")
    value["id"] = identity["id"]
    value["name"] = identity["name"]
    if isinstance(value.get("barWidget"), dict) and "displayName" in identity:
        value["barWidget"]["displayName"] = identity["displayName"]
    if not isinstance(value.get("omarchy"), dict):
        value["omarchy"] = {}
    value["omarchy"]["clonedFrom"] = source_id
    value["omarchy"].pop("clonePaths", None)
    result["manifest.json"] = File(json_bytes(value), result["manifest.json"].mode)
    return result


def is_text(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeError:
        return False
    return True


def merge_scalar(base, local, upstream):
    if local == upstream or upstream == base:
        return local, False
    if local == base:
        return upstream, False
    return local, True


def merge_file(base: File | None, local: File | None, upstream: File | None):
    selected, conflict = merge_scalar(base, local, upstream)
    if not conflict:
        return selected, False, ""
    if local is None or upstream is None:
        return local, True, "One side deleted a file the other side changed."
    if base is None:
        return local, True, "Both sides added different files at the same path."
    mode, mode_conflict = merge_scalar(base.mode, local.mode, upstream.mode)
    data, content_conflict = merge_scalar(base.data, local.data, upstream.data)
    reason = "Both sides changed file permissions differently." if mode_conflict else ""
    if not content_conflict:
        return File(data, mode), mode_conflict, reason
    if not all(is_text(f.data) for f in (base, local, upstream)):
        return local, True, "Both sides changed binary or non-UTF-8 content."
    with tempfile.TemporaryDirectory(prefix="clone-keeper-merge-") as tmp:
        paths = [Path(tmp) / name for name in ("local", "base", "upstream")]
        for path, content in zip(paths, (local.data, base.data, upstream.data)):
            path.write_bytes(content)
        try:
            process = subprocess.run(
                ["git", "merge-file", "-p", "--diff3", "-L", "Your clone", "-L",
                 "Recorded baseline", "-L", "Current upstream", *map(str, paths)],
                capture_output=True, timeout=15, check=False,
            )
        except FileNotFoundError as exc:
            raise KeeperError("Git is required to preview overlapping changes") from exc
        except subprocess.TimeoutExpired as exc:
            raise KeeperError("Git merge-file exceeded its 15-second time limit") from exc
        if process.returncode < 0 or process.returncode > 127:
            raise KeeperError("git merge-file failed: " + process.stderr.decode(errors="replace").strip())
        conflict = process.returncode != 0 or mode_conflict
        if process.returncode:
            reason = "Both sides changed overlapping lines."
        return File(process.stdout, mode), conflict, reason


def file_diff(path: str, local: File | None, candidate: File | None) -> str:
    old, new = local.data if local else b"", candidate.data if candidate else b""
    mode_diff = ""
    if local and candidate and local.mode != candidate.mode:
        mode_diff = f"mode {local.mode:04o} -> {candidate.mode:04o}\n"
    if not is_text(old) or not is_text(new):
        return mode_diff + ("Binary content differs." if old != new else "")
    diff = "".join(difflib.unified_diff(
        old.decode().splitlines(keepends=True), new.decode().splitlines(keepends=True),
        fromfile=f"your-clone/{path}" if local else "/dev/null",
        tofile=f"candidate/{path}" if candidate else "/dev/null"))
    diff = mode_diff + diff
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n[Preview truncated; exported file remains complete.]\n"
    return diff


def validate_candidate(tree: Tree, clone_id: str, source_id: str) -> list[str]:
    errors = []
    for path in tree:
        for parent in PurePosixPath(path).parents:
            if str(parent) in tree:
                errors.append(f"File/directory collision at {parent}")
    try:
        value = manifest(tree)
    except KeeperError as exc:
        return errors + [str(exc)]
    if type(value.get("schemaVersion")) is not int or value.get("schemaVersion") != 1:
        errors.append("Manifest schemaVersion must be the number 1")
    if value.get("id") != clone_id or clone_id.startswith("omarchy."):
        errors.append("Candidate must retain the non-reserved clone ID")
    metadata = value.get("omarchy")
    if not isinstance(metadata, dict) or metadata.get("clonedFrom") != source_id:
        errors.append("Candidate must retain omarchy.clonedFrom")
    for field in ("name", "version"):
        if not isinstance(value.get(field), str) or not value[field]:
            errors.append(f"Manifest {field} must be a nonempty string")
    kinds, entries = value.get("kinds"), value.get("entryPoints")
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) for k in kinds):
        errors.append("Manifest kinds must be a nonempty string array")
        kinds = []
    if not isinstance(entries, dict):
        errors.append("Manifest entryPoints must be an object")
        entries = {}
    for kind in kinds:
        if kind in KINDS and KINDS[kind] not in entries:
            errors.append(f"Manifest kind {kind} requires entryPoints.{KINDS[kind]}")
    for entry in entries.values():
        try:
            path = safe_relative(entry)
            if ".." in path or path not in tree:
                errors.append(f"Entry point file is missing or unsafe: {entry}")
        except KeeperError as exc:
            errors.append(str(exc))
    widget = value.get("barWidget")
    if isinstance(widget, dict) and widget.get("defaultSection", "right") not in ("left", "center", "right"):
        errors.append("barWidget.defaultSection must be left, center, or right")
    return sorted(set(errors))


class Keeper:
    def __init__(self, omarchy_path: Path, plugins_dir: Path, state_dir: Path):
        self.omarchy_path = omarchy_path.absolute()
        self.plugins_dir = plugins_dir.absolute()
        self.state_dir = state_dir.absolute()

    def version(self) -> str:
        """Describe the Omarchy release for display; content hashes decide changes.

        The packaged tree's version file can lag its package, so ask pacman as
        `omarchy version` does. Development trees and fixtures use the file.
        """
        if self.omarchy_path.resolve() == Path("/usr/share/omarchy"):
            for package in ("omarchy-dev", "omarchy"):
                try:
                    process = subprocess.run(["pacman", "-Q", package], capture_output=True, text=True, timeout=5)
                except (OSError, subprocess.TimeoutExpired, UnicodeError):
                    break
                fields = process.stdout.split()
                if process.returncode == 0 and len(fields) == 2:
                    return fields[1]
        path = self.omarchy_path / "version"
        return read_file(path).data.decode().strip() if path.exists() else "unknown"

    def sources(self) -> dict[str, Source]:
        root = self.omarchy_path / "shell" / "plugins"
        if not root.is_dir():
            raise KeeperError(f"Omarchy plugin sources not found: {root}")
        no_symlink_path(root)
        result = {}
        # Equivalent scope to omarchy-plugin-catalog: depth 2..4, sorted paths.
        for path in sorted(root.rglob("*.json")):
            if len(path.relative_to(root).parts) not in (2, 3, 4):
                continue
            if path.name != "manifest.json" and not path.name.endswith(".manifest.json"):
                continue
            value = object_json(read_file(path).data, str(path))
            ident = value.get("id")
            if isinstance(ident, str) and ident.startswith("omarchy."):
                plugin_id(ident)
                if ident in result:
                    raise KeeperError(f"Duplicate upstream plugin ID: {ident}")
                result[ident] = Source(path, root)
        return result

    def clone(self, ident: str) -> tuple[Path, Tree, dict, str]:
        plugin_id(ident)
        if ident.startswith("omarchy."):
            raise KeeperError("Choose a personal clone ID, not an omarchy.* built-in")
        path = self.plugins_dir / ident
        tree = canonical_tree(read_tree(path))
        value = manifest(tree)
        if value.get("id") != ident:
            raise KeeperError(f"Folder name and manifest ID disagree for {ident}")
        info = value.get("omarchy")
        source_id = info.get("clonedFrom") if isinstance(info, dict) else None
        if not isinstance(source_id, str) or not source_id.startswith("omarchy."):
            raise KeeperError(f"{ident} does not declare a built-in omarchy.clonedFrom")
        plugin_id(source_id)
        return path, tree, value, source_id

    def state_path(self, ident: str) -> Path:
        return self.state_dir / "clones" / plugin_id(ident)

    def load_baseline(self, ident: str) -> tuple[dict, Tree]:
        path = self.state_path(ident)
        if not path.exists():
            raise KeeperError(f"{ident} is not tracked. Run track before making changes, or supply a known baseline.")
        meta = object_json(read_file(path / "record.json").data, str(path / "record.json"))
        if meta.get("schemaVersion") != 1 or meta.get("cloneId") != ident:
            raise KeeperError(f"Unsupported or invalid baseline record for {ident}")
        base = read_tree(path / "baseline")
        if inventory(base) != meta.get("files") or tree_hash(base) != meta.get("baselineHash"):
            raise KeeperError(f"Baseline integrity check failed for {ident}; files were changed")
        return meta, base

    def protect_destination(self, path: Path, *, state: bool = False) -> Path:
        no_symlink_path(path.absolute())
        target = path.resolve()
        protected = [self.plugins_dir.resolve(), self.omarchy_path.resolve()]
        if not state:
            protected.append(self.state_dir.resolve())
        for root in protected:
            if is_within(target, root) or is_within(root, target):
                raise KeeperError(f"Destination must be outside installed plugins, Omarchy sources, and managed state: {target}")
        return target

    def track(self, ident: str, baseline: Path | None = None, origin=None) -> dict:
        """Record a clone's ancestor.

        origin is a confirmed (original tree, release version, evidence) found
        among recorded Omarchy releases; see origins.Origins.baseline.
        """
        path, local, value, source_id = self.clone(ident)
        destination = self.protect_destination(self.state_path(ident), state=True)
        if destination.exists():
            raise KeeperError(f"{ident} is already tracked; its baseline will not be overwritten")
        identity = clone_identity(value)
        evidence = None
        if origin is not None:
            base, version, evidence = origin
            provenance = "historical-release-source"
        elif baseline is None:
            source = self.sources().get(source_id)
            if source is None:
                raise KeeperError(f"Upstream source is missing: {source_id}")
            base = as_clone(source.materialize(), identity, source_id)
            differences = changed_files(base, local)
            if differences:
                raise KeeperError(
                    "Clone already differs from current upstream; historical baseline is unknown. "
                    "Supply --baseline with the original source directory. Changed files: " + ", ".join(differences[:8]))
            provenance, version = "verified-current-source", self.version()
        else:
            # A supplied historical baseline must be an original, fully materialized
            # plugin, not an already customized clone or a guessed merge ancestor.
            original = canonical_tree(read_tree(baseline.absolute()))
            base = as_clone(original, identity, source_id)
            provenance = "user-supplied-original"
            version = f"provided baseline (plugin {manifest(original).get('version', 'unknown')})"
        errors = validate_candidate(base, ident, source_id)
        if errors:
            raise KeeperError("Invalid baseline: " + "; ".join(errors))
        meta = {"schemaVersion": 1, "cloneId": ident, "sourceId": source_id,
                "clonePath": str(path), "identity": identity,
                "capturedAt": datetime.now(timezone.utc).isoformat(),
                "baselineVersion": version, "provenance": provenance,
                "baselineHash": tree_hash(base), "files": inventory(base)}
        if evidence is not None:
            meta["evidence"] = evidence
        stored = {f"baseline/{name}": file for name, file in base.items()}
        stored["record.json"] = File(json_bytes(meta), 0o600)
        # Ensure directory privacy even when the user's umask is permissive.
        no_symlink_path(self.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_new_tree(destination, stored)
        return {"cloneId": ident, "tracked": True, "baselineVersion": version,
                "baselineHash": meta["baselineHash"], "provenance": provenance,
                "statePath": str(destination)}

    def status(self) -> dict:
        sources = self.sources()
        result = {"schemaVersion": 1, "upstreamVersion": self.version(), "clones": [], "errors": []}
        if not self.plugins_dir.exists():
            return result
        no_symlink_path(self.plugins_dir)
        for directory in sorted(self.plugins_dir.iterdir()):
            if directory.name.startswith("."):
                continue
            try:
                if directory.is_symlink():
                    raise KeeperError(f"Symlinked plugin directory is unsupported: {directory}")
                if not directory.is_dir() or not (directory / "manifest.json").exists():
                    continue
                info = object_json(read_file(directory / "manifest.json").data, str(directory / "manifest.json"))
                if not isinstance(info.get("omarchy"), dict) or not info["omarchy"].get("clonedFrom"):
                    continue
                ident = directory.name
                row = {"id": ident, "name": info.get("name", ident),
                       "sourceId": info["omarchy"]["clonedFrom"], "path": str(directory),
                       "tracked": self.state_path(ident).exists(), "status": "untracked",
                       "message": "Its original source is not recorded yet, so updates cannot be merged.",
                       "localChangedFiles": 0, "upstreamChangedFiles": 0, "baselineVersion": None}
                result["clones"].append(row)
                try:
                    _, local, _, source_id = self.clone(ident)
                    source = sources.get(source_id)
                    if source is None:
                        row.update(status="missing-source", message="The built-in source is no longer installed.")
                        continue
                    if not row["tracked"]:
                        continue
                    meta, base = self.load_baseline(ident)
                    if meta.get("sourceId") != source_id:
                        raise KeeperError("Clone source changed since the baseline was captured")
                    upstream = as_clone(source.materialize(), meta["identity"], source_id)
                    left, right = len(changed_files(base, local)), len(changed_files(base, upstream))
                    status = "diverged" if left and right else "local-only" if left else "upstream-only" if right else "up-to-date"
                    messages = {"diverged": "Both your clone and upstream changed. Preview a merge.",
                                "local-only": "Your customizations are recorded; upstream is unchanged.",
                                "upstream-only": "Upstream changes are ready to review.",
                                "up-to-date": "Clone and upstream match the recorded baseline."}
                    row.update(status=status, message=messages[status], localChangedFiles=left,
                               upstreamChangedFiles=right, baselineVersion=meta["baselineVersion"])
                except (KeeperError, OSError, ValueError, KeyError, TypeError) as exc:
                    row.update(status="error", message=str(exc))
            except (KeeperError, OSError, ValueError, KeyError, TypeError) as exc:
                result["errors"].append({"path": str(directory), "message": str(exc)})
        return result

    def preview(self, ident: str) -> tuple[dict, Tree]:
        _, local, _, source_id = self.clone(ident)
        meta, base = self.load_baseline(ident)
        if meta.get("sourceId") != source_id:
            raise KeeperError("Clone source changed since the baseline was captured")
        source = self.sources().get(source_id)
        if source is None:
            raise KeeperError(f"Upstream source is missing: {source_id}")
        upstream = as_clone(source.materialize(), meta["identity"], source_id)
        changes, candidate = [], {}
        for path in sorted(base.keys() | local.keys() | upstream.keys()):
            original, ours, theirs = base.get(path), local.get(path), upstream.get(path)
            merged, conflict, reason = merge_file(original, ours, theirs)
            if merged is not None:
                candidate[path] = merged
            if original == ours == theirs:
                continue
            status = ("conflict" if conflict else "local-only" if theirs == original
                      else "upstream-only" if ours == original else "merged")
            changes.append({"path": path, "status": status, "conflict": conflict,
                            "reason": reason, "diff": file_diff(path, ours, merged),
                            "localDiff": file_diff(path, original, ours),
                            "upstreamDiff": file_diff(path, original, theirs)})
        validation = validate_candidate(candidate, ident, source_id)
        conflicts = sum(change["conflict"] for change in changes)
        report = {"schemaVersion": 1, "cloneId": ident, "sourceId": source_id,
                  "baseVersion": meta["baselineVersion"], "upstreamVersion": self.version(),
                  "baselineHash": meta["baselineHash"], "localHash": tree_hash(local),
                  "upstreamHash": tree_hash(upstream), "candidateHash": tree_hash(candidate),
                  "hasConflicts": bool(conflicts), "changes": changes,
                  "summary": {"files": len(changes), "conflicts": conflicts},
                  "validation": {"valid": not validation, "errors": validation}}
        return report, candidate

    def export(self, ident: str, destination: Path) -> dict:
        output = self.protect_destination(destination)
        if output.exists():
            raise KeeperError(f"Destination already exists: {output}")
        report, candidate = self.preview(ident)
        if report["hasConflicts"]:
            raise KeeperError("Merge has conflicts. Review the preview; no candidate was exported.")
        if not report["validation"]["valid"]:
            raise KeeperError("Candidate is invalid: " + "; ".join(report["validation"]["errors"]))
        # Re-check current inputs before publishing the reviewed candidate. This
        # is deliberately not an apply operation and never advances the baseline.
        _, local, _, _ = self.clone(ident)
        if tree_hash(local) != report["localHash"]:
            raise KeeperError("Your clone changed during export; retry the preview")
        meta, _ = self.load_baseline(ident)
        if meta["baselineHash"] != report["baselineHash"]:
            raise KeeperError("The baseline changed during export; retry the preview")
        source = self.sources().get(report["sourceId"])
        if source is None:
            raise KeeperError("The upstream source disappeared during export; retry the preview")
        upstream = as_clone(source.materialize(), meta["identity"], report["sourceId"])
        if tree_hash(upstream) != report["upstreamHash"]:
            raise KeeperError("The upstream source changed during export; retry the preview")
        atomic_new_tree(output, candidate)
        return {"cloneId": ident, "output": str(output), "candidateHash": report["candidateHash"],
                "files": len(candidate), "validation": report["validation"],
                "message": "Candidate exported for review. Installed clone and baseline are unchanged."}
