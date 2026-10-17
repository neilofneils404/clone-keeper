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


MARK = {"start": re.compile(rb"^<{7}(?: |\r?$)"), "base": re.compile(rb"^\|{7}(?: |\r?$)"),
        "split": re.compile(rb"^={7}\r?$"), "end": re.compile(rb"^>{7}(?: |\r?$)")}


def conflict_blocks(data: bytes) -> tuple[list[bytes | dict], int]:
    """Split git merge-file --diff3 output into plain text and conflict blocks.

    Returns the parts in order and how many blocks were found. A malformed
    block is left as plain text, so its markers still block apply.
    """
    lines = data.splitlines(keepends=True)
    parts, plain, index, count = [], [], 0, 0
    while index < len(lines):
        if not MARK["start"].match(lines[index]):
            plain.append(lines[index])
            index += 1
            continue
        sections, current, cursor = {"local": [], "base": [], "upstream": []}, "local", index + 1
        while cursor < len(lines) and not MARK["end"].match(lines[cursor]):
            line = lines[cursor]
            if current == "local" and MARK["base"].match(line):
                current = "base"
            elif current in ("local", "base") and MARK["split"].match(line):
                current = "upstream"
            elif MARK["start"].match(line):
                break
            else:
                sections[current].append(line)
            cursor += 1
        if cursor >= len(lines) or not MARK["end"].match(lines[cursor]) or current != "upstream":
            plain.append(lines[index])
            index += 1
            continue
        parts.append(b"".join(plain))
        plain = []
        parts.append({name: b"".join(value) for name, value in sections.items()})
        count += 1
        index = cursor + 1
    parts.append(b"".join(plain))
    return parts, count


def resolve_block(data: bytes, number: int, take: str) -> bytes:
    parts, count = conflict_blocks(data)
    if not 0 <= number < count:
        raise KeeperError(f"Choose a conflict between 0 and {count - 1}" if count else "This file has no conflict blocks left")
    seen, output = -1, []
    for part in parts:
        if isinstance(part, bytes):
            output.append(part)
            continue
        seen += 1
        if seen != number:
            output.append(b"<<<<<<< Your clone\n" + part["local"] + b"||||||| Recorded baseline\n" + part["base"]
                          + b"=======\n" + part["upstream"] + b">>>>>>> Current upstream\n")
        elif take == "both":
            output.append(part["local"] + part["upstream"])
        else:
            output.append(part[take])
    return b"".join(output)


def describe_blocks(file: File | None) -> list[dict]:
    if file is None or not is_text(file.data):
        return []
    parts, _ = conflict_blocks(file.data)
    blocks, offset = [], 1
    for part in parts:
        if isinstance(part, bytes):
            offset += part.count(b"\n")
            continue
        blocks.append({"index": len(blocks), "line": offset,
                       **{name: part[name].decode(errors="replace") for name in ("local", "base", "upstream")}})
        offset += 4 + sum(part[name].count(b"\n") for name in ("local", "base", "upstream"))
    return blocks


def conflict_groups(changes: list[dict], candidate: Tree, local: Tree) -> tuple[list[dict], Tree]:
    """Group conflicting paths for resolution, keeping the local side of collisions."""
    conflicts = {change["path"]: change["reason"] for change in changes if change["conflict"]}
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
    return [{"path": name, "reason": conflicts[name]} for name in roots], candidate


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
        conflicts, candidate = conflict_groups(report["changes"], candidate, local)
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
                  "conflicts": conflicts}
        self.store(plan_id, record, base, local, upstream, candidate)
        return self.review(plan_id)

    def store(self, plan_id: str, record: dict, base: Tree, local: Tree, upstream: Tree, candidate: Tree) -> None:
        output = self.keeper.protect_destination(self.path(plan_id), state=True)
        contents = {"record.json": File(json_bytes(record), 0o600),
                    "resolutions.json": File(json_bytes({}), 0o600)}
        for name, tree in (("base", base), ("local", local), ("upstream", upstream), ("candidate", candidate)):
            contents.update({f"{name}/{file}": blob for file, blob in tree.items()})
        self.keeper.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_new_tree(output, contents)

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
                              "resolution": resolution.get("take") if resolved else None,
                              "blocks": [] if resolved else describe_blocks(candidate.get(path))})
        markers = marker_files(candidate)
        git_plugin = record.get("kind") == "git"
        errors = validate_candidate(candidate, record["cloneId"], None if git_plugin else record["sourceId"])
        if markers:
            errors.append("Unresolved conflict markers in: " + ", ".join(markers))
        from .validation import check_manifest, check_qml
        if not errors:
            errors.extend(check_manifest(candidate, self.keeper.omarchy_path))
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
        try:
            stale = self._stale(record)
        except (KeeperError, OSError, KeyError, TypeError, ValueError) as exc:
            stale = [str(exc)]
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

    def _stale(self, record: dict) -> list[str]:
        """Why a plan's inputs no longer match the installed plugin, its baseline or upstream."""
        if record.get("kind") == "git":
            from .gitplugins import GitPlugins
            from .service import Workflow
            return GitPlugins(Workflow(self.keeper)).stale(record)
        stale = []
        _, live, _, source_id = self.keeper.clone(record["cloneId"])
        meta, _ = self.keeper.load_baseline(record["cloneId"])
        source = self.keeper.sources().get(source_id)
        if tree_hash(live) != record["localHash"]:
            stale.append("Your clone changed after this plan was prepared.")
        if meta["baselineHash"] != record["baselineHash"]:
            stale.append("The baseline changed after this plan was prepared.")
        if source is None or tree_hash(as_clone(source.materialize(), record["identity"], record["sourceId"])) != record["upstreamHash"]:
            stale.append("Upstream changed after this plan was prepared.")
        return stale

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
            # Displaced candidates may still receive saves through an editor's
            # open file descriptor; a resolved plan is no longer untouched.
            if any(root.glob(".resolution-*")):
                return False
            resolutions = object_json(read_file(root / "resolutions.json").data, "resolutions")
            return not resolutions and tree_hash(read_tree(root / "candidate")) == record.get("candidateHash")
        return self.discard(clone_id, untouched)

    def resolve(self, plan_id: str, path: str, take: str | None = None, replacement_file: Path | None = None,
                block: int | None = None) -> dict:
        from .transactions import clone_lock, exchange_directories
        record, _ = self.load(plan_id)
        with clone_lock(self.keeper, record["cloneId"]):
            record, trees = self.load(plan_id)
            path = safe_relative(path)
            known = set().union(*(tree.keys() for tree in trees.values()))
            if path not in known and not any(name.startswith(path + "/") for name in known):
                raise KeeperError("Choose a path present in this merge plan")
            candidate = trees["candidate"]
            if block is not None:
                current = candidate.get(path)
                if current is None or take not in ("local", "upstream", "base", "both"):
                    raise KeeperError("Choose local, upstream, base or both for a conflict in a candidate file")
                replacement = {path: File(resolve_block(current.data, block, take), current.mode)}
                choice = "conflicts"
            elif replacement_file is not None:
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
            if marker_files(replacement) and choice != "conflicts":
                raise KeeperError("Remove conflict markers before accepting this resolution")
            proposed = replace_family(candidate, path, replacement)
            root = self.path(plan_id)
            temporary = Path(tempfile.mkdtemp(prefix=".resolution-", dir=root))
            stage = temporary / "candidate"
            live = root / "candidate"
            exchange_attempted = False

            def matches(directory, expected):
                refuse_git_metadata(directory)
                return tree_hash(read_tree(directory)) == tree_hash(expected)

            try:
                stage.mkdir(mode=0o700)
                write_tree(stage, proposed)
                if not matches(live, candidate):
                    raise KeeperError("Candidate changed while preparing the resolution; review it again")
                # Retain the displaced tree once an exchange is attempted,
                # even if fsync fails after the rename or an editor still has
                # an old file open. Backups share the saved plan's lifetime.
                exchange_attempted = True
                exchange_directories(stage, live)
                if not matches(stage, candidate):
                    if matches(live, proposed):
                        exchange_directories(stage, live)
                    raise KeeperError(f"Candidate changed during resolution; files preserved at {live} and {stage}")
            finally:
                if not exchange_attempted:
                    shutil.rmtree(temporary)
            if choice == "conflicts" and marker_files(replacement):
                # Choices remain in this file; it is accepted once the last is made.
                return self.review(plan_id)
            resolutions = object_json(read_file(root / "resolutions.json").data, "resolutions")
            # Resolving a parent covers its known descendant conflict groups.
            for conflict in record["conflicts"]:
                conflict_path = conflict["path"]
                if conflict_path == path or conflict_path.startswith(path + "/"):
                    resolutions[conflict_path] = {"take": choice, "hash": tree_hash(family(proposed, conflict_path))}
            atomic_json(root / "resolutions.json", resolutions)
            return self.review(plan_id)
