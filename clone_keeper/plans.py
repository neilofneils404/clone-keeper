"""Persistent, reviewable merge plans and explicit conflict resolutions."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone

from .core import (File, Keeper, KeeperError, Tree, as_clone, atomic_new_tree,
                   canonical_tree, file_diff, is_text, json_bytes, manifest,
                   no_symlink_path, object_json, read_file, read_tree,
                   safe_relative, tree_hash, validate_candidate, write_tree)


def plan_identifier(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise KeeperError("Invalid plan ID; use the ID returned by prepare")
    return value


def atomic_json(path: Path, value: object) -> None:
    no_symlink_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def family(tree: Tree, path: str) -> Tree:
    return {name: file for name, file in tree.items()
            if name == path or name.startswith(path + "/")}


def replace_family(tree: Tree, path: str, replacement: Tree) -> Tree:
    result = {name: file for name, file in tree.items()
              if name != path and not name.startswith(path + "/")}
    for name in replacement:
        for parent in PurePosixPath(name).parents:
            if str(parent) in result:
                raise KeeperError(f"Resolve the file/directory conflict at {parent} first")
    result.update(replacement)
    return result


def marker_files(tree: Tree) -> list[str]:
    markers = re.compile(rb"^(?:<{7}(?: |$)|\|{7}(?: |$)|>{7}(?: |$))", re.MULTILINE)
    return sorted(path for path, file in tree.items()
                  if is_text(file.data) and markers.search(file.data))


def refuse_git_metadata(path: Path) -> None:
    if any(part.name == ".git" for part in path.rglob(".git")):
        raise KeeperError("Git metadata is not supported in merge candidates")


class Plans:
    def __init__(self, keeper: Keeper):
        self.keeper = keeper

    def path(self, plan_id: str) -> Path:
        return self.keeper.state_dir / "plans" / plan_identifier(plan_id)

    def load(self, plan_id: str) -> tuple[dict, dict[str, Tree]]:
        root = self.path(plan_id)
        record = object_json(read_file(root / "record.json").data, "plan record")
        if record.get("schemaVersion") != 1 or record.get("id") != plan_id:
            raise KeeperError("Invalid or unsupported merge plan")
        expected_path = self.keeper.plugins_dir / record["cloneId"]
        if record.get("clonePath") != str(expected_path):
            raise KeeperError("This plan belongs to a different plugin directory")
        trees = {}
        for name, key in (("base", "baselineHash"), ("local", "localHash"),
                          ("upstream", "upstreamHash"), ("candidate", "candidateHash")):
            refuse_git_metadata(root / name)
            trees[name] = read_tree(root / name)
            if name != "candidate" and tree_hash(trees[name]) != record.get(key):
                raise KeeperError(f"Plan's immutable {name} snapshot was changed")
        return record, trees

    def prepare(self, clone_id: str) -> dict:
        from .transactions import clone_lock
        with clone_lock(self.keeper, clone_id):
            return self._prepare(clone_id)

    def _prepare(self, clone_id: str) -> dict:
        keeper = self.keeper
        from .transactions import Transactions
        if Transactions(keeper).history(clone_id)["pendingTransaction"]:
            raise KeeperError("Recover the interrupted transaction before preparing a new plan")
        meta, base = keeper.load_baseline(clone_id)
        if meta.get("creationPending"):
            raise KeeperError("Recover the interrupted creation before preparing a new plan")
        report, candidate = keeper.preview(clone_id)
        path, local, _, source_id = keeper.clone(clone_id)
        source = keeper.sources().get(source_id)
        if source is None:
            raise KeeperError("Upstream source disappeared; prepare a new plan")
        upstream = as_clone(source.materialize(), meta["identity"], source_id)
        for tree, key in ((base, "baselineHash"), (local, "localHash"), (upstream, "upstreamHash")):
            if tree_hash(tree) != report[key]:
                raise KeeperError("Merge inputs changed while preparing; try again")
        conflicts = {change["path"]: change["reason"] for change in report["changes"] if change["conflict"]}
        # A candidate cannot physically contain both `file` and `file/child`.
        # Keep the local family for this group and require an explicit choice.
        for name in list(candidate):
            for parent in PurePosixPath(name).parents:
                root = str(parent)
                if root in candidate:
                    conflicts[root] = "File/directory collision. Choose the complete path from one side."
        roots = []
        for name in sorted(conflicts, key=lambda item: (len(PurePosixPath(item).parts), item)):
            if not any(name.startswith(parent + "/") for parent in roots):
                roots.append(name)
        for name in roots:
            if conflicts[name].startswith("File/directory"):
                candidate = replace_family(candidate, name, family(local, name))
        # Preparing again replaces untouched reviews of the same inputs.
        self.discard_duplicates(clone_id, {key: report[key] for key in ("baselineHash", "localHash", "upstreamHash")})
        plan_id = uuid.uuid4().hex
        record = {"schemaVersion": 1, "id": plan_id, "cloneId": clone_id,
                  "sourceId": source_id, "clonePath": str(path),
                  "createdAt": datetime.now(timezone.utc).isoformat(),
                  "baselineHash": report["baselineHash"], "localHash": report["localHash"],
                  "upstreamHash": report["upstreamHash"], "candidateHash": tree_hash(candidate),
                  "baseVersion": report["baseVersion"], "upstreamVersion": report["upstreamVersion"],
                  "identity": meta["identity"],
                  "conflicts": [{"path": name, "reason": conflicts[name]} for name in roots]}
        output = keeper.protect_destination(self.path(plan_id), state=True)
        contents = {"record.json": File(json_bytes(record), 0o600),
                    "resolutions.json": File(json_bytes({}), 0o600)}
        for name, tree in (("base", base), ("local", local), ("upstream", upstream), ("candidate", candidate)):
            contents.update({f"{name}/{file}": blob for file, blob in tree.items()})
        keeper.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_new_tree(output, contents)
        return self.review(plan_id)

    def review(self, plan_id: str) -> dict:
        record, trees = self.load(plan_id)
        resolutions = object_json(read_file(self.path(plan_id) / "resolutions.json").data, "resolutions")
        candidate, base, local, upstream = (trees[key] for key in ("candidate", "base", "local", "upstream"))
        conflicts = []
        for conflict in record["conflicts"]:
            path = safe_relative(conflict["path"])
            resolution = resolutions.get(path, {})
            resolved = isinstance(resolution, dict) and resolution.get("hash") == tree_hash(family(candidate, path))
            conflicts.append({**conflict, "resolved": resolved,
                              "resolution": resolution.get("take") if resolved else None})
        markers = marker_files(candidate)
        errors = validate_candidate(candidate, record["cloneId"], record["sourceId"])
        if markers:
            errors.append("Unresolved conflict markers in: " + ", ".join(markers))
        from .validation import check_qml
        syntax = check_qml(candidate) if not errors else {"available": False, "checked": 0, "errors": [], "message": "Resolve structural errors and conflict markers before syntax checking."}
        errors.extend(syntax["errors"])
        changes = []
        for path in sorted(base.keys() | local.keys() | upstream.keys() | candidate.keys()):
            original, ours, theirs, proposed = (tree.get(path) for tree in (base, local, upstream, candidate))
            if original == ours == theirs == proposed:
                continue
            conflict = next((item for item in conflicts if path == item["path"] or path.startswith(item["path"] + "/")), None)
            unresolved = bool(conflict and not conflict["resolved"])
            status = "conflict" if unresolved else "resolved" if conflict else "local-only" if original == theirs else "upstream-only" if original == ours else "merged"
            changes.append({"path": path, "status": status, "conflict": unresolved,
                            "reason": conflict["reason"] if conflict else "",
                            "diff": file_diff(path, ours, proposed),
                            "localDiff": file_diff(path, original, ours),
                            "upstreamDiff": file_diff(path, original, theirs)})
        stale = []
        try:
            _, live, _, source_id = self.keeper.clone(record["cloneId"])
            meta, _ = self.keeper.load_baseline(record["cloneId"])
            source = self.keeper.sources().get(source_id)
            if tree_hash(live) != record["localHash"]:
                stale.append("Your clone changed after this plan was prepared.")
            if meta["baselineHash"] != record["baselineHash"]:
                stale.append("The baseline changed after this plan was prepared.")
            if source is None or tree_hash(as_clone(source.materialize(), record["identity"], record["sourceId"])) != record["upstreamHash"]:
                stale.append("Upstream changed after this plan was prepared.")
        except (KeeperError, OSError, KeyError, TypeError, ValueError) as exc:
            stale.append(str(exc))
        unresolved_count = sum(not item["resolved"] for item in conflicts)
        return {"schemaVersion": 1, "planId": plan_id, "cloneId": record["cloneId"],
                "sourceId": record["sourceId"], "baseVersion": record["baseVersion"],
                "upstreamVersion": record["upstreamVersion"],
                "candidateHash": tree_hash(candidate),
                "planHash": hashlib.sha256(json_bytes(record)).hexdigest(),
                "hasConflicts": unresolved_count > 0 or bool(markers), "conflicts": conflicts,
                "changes": changes, "summary": {"files": len(changes), "conflicts": unresolved_count},
                "validation": {"valid": not errors, "errors": errors, "syntax": syntax},
                "stale": bool(stale), "staleReasons": stale,
                "editablePath": str(self.path(plan_id) / "candidate")}

    def discard(self, clone_id: str, obsolete) -> list[str]:
        """Remove a clone's saved plans for which obsolete(record, root) is true.

        Callers hold the clone lock. Removal is best-effort cleanup: a plan is
        hidden atomically, then deleted, and unreadable plans are left alone.
        """
        directory = self.keeper.state_dir / "plans"
        no_symlink_path(directory)
        if not directory.exists():
            return []
        removed = []
        for path in sorted(directory.iterdir()):
            if path.name.startswith(".") or not path.is_dir():
                continue
            try:
                record = object_json(read_file(path / "record.json").data, str(path))
                if record.get("cloneId") != clone_id or record.get("id") != path.name:
                    continue
                if not obsolete(record, path):
                    continue
                hidden = directory / (".discarded-" + path.name)
                os.rename(path, hidden)
                shutil.rmtree(hidden)
                removed.append(path.name)
            except (KeeperError, OSError, ValueError):
                continue
        return removed

    def discard_superseded(self, clone_id: str, baseline_hashes: set[str]) -> list[str]:
        """After an apply, keep plans for the current and previous baselines only.

        A plan binds its baseline; older ones can never become applicable again
        unless every later apply is undone, and can then be prepared anew.
        """
        return self.discard(clone_id, lambda record, _: record.get("baselineHash") not in baseline_hashes)

    def discard_duplicates(self, clone_id: str, inputs: dict) -> list[str]:
        """Remove untouched plans prepared from exactly the same inputs."""
        def untouched(record, root):
            if any(record.get(key) != value for key, value in inputs.items()):
                return False
            resolutions = object_json(read_file(root / "resolutions.json").data, "resolutions")
            return not resolutions and tree_hash(read_tree(root / "candidate")) == record.get("candidateHash")
        return self.discard(clone_id, untouched)

    def resolve(self, plan_id: str, path: str, take: str | None = None, replacement_file: Path | None = None) -> dict:
        from .transactions import clone_lock, exchange_directories
        record, _ = self.load(plan_id)
        with clone_lock(self.keeper, record["cloneId"]):
            record, trees = self.load(plan_id)
            path = safe_relative(path)
            known = set().union(*(tree.keys() for tree in trees.values()))
            if path not in known and not any(name.startswith(path + "/") for name in known):
                raise KeeperError("Choose a path present in this merge plan")
            candidate = trees["candidate"]
            if replacement_file is not None:
                replacement = {path: read_file(replacement_file.absolute())}
                choice = "file"
            elif take == "delete":
                replacement, choice = {}, "delete"
            elif take == "edited":
                replacement, choice = family(candidate, path), "edited"
            elif take in ("local", "upstream", "base"):
                replacement, choice = family(trees[take], path), take
            else:
                raise KeeperError("Choose local, upstream, base, delete, edited, or a replacement file")
            if marker_files(replacement):
                raise KeeperError("Remove conflict markers before accepting this resolution")
            proposed = replace_family(candidate, path, replacement)
            root = self.path(plan_id)
            with tempfile.TemporaryDirectory(prefix=".resolution-", dir=root.parent) as temporary:
                stage = Path(temporary) / "candidate"
                stage.mkdir(mode=0o700)
                write_tree(stage, proposed)
                exchange_directories(stage, root / "candidate")
            resolutions = object_json(read_file(root / "resolutions.json").data, "resolutions")
            # Resolving a parent covers its known descendant conflict groups.
            for conflict in record["conflicts"]:
                conflict_path = conflict["path"]
                if conflict_path == path or conflict_path.startswith(path + "/"):
                    resolutions[conflict_path] = {"take": choice, "hash": tree_hash(family(proposed, conflict_path))}
            atomic_json(root / "resolutions.json", resolutions)
            return self.review(plan_id)
