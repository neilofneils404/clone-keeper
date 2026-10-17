"""Update installed third-party plugins that carry local edits.

`omarchy plugin update` refuses a git-installed plugin with uncommitted edits.
Here git itself records the ancestry: HEAD is the original, the working tree is
the owner's version and FETCH_HEAD, which `omarchy plugin update` or an explicit
fetch leaves, is upstream. Only fast-forward updates are supported; local commits
are left to git.

Apply stages a copy of the whole plugin directory, .git included, moves the copy's
HEAD and index to upstream with git plumbing (no hooks, filters or checkout run),
writes the reviewed candidate as the working tree and exchanges the directories
atomically. The displaced directory is the undo backup.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from .core import (File, KeeperError, Tree, changed_files, merge_file, merge_trees,
                   no_symlink_path, object_json, plugin_id, read_file, read_tree, safe_relative, tree_hash)
from .plans import atomic_json, conflict_groups

# Plugin repositories are not trusted to run code: no hooks, no fsmonitor, no prompts.
GIT = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
PENDING = {"prepared", "undo-prepared"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def git(directory: Path, *args: str, timeout=30, check=True, input=None) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
           "GIT_SSH_COMMAND": os.environ.get("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")}
    try:
        process = subprocess.run([*GIT, "-C", str(directory), *args], capture_output=True, timeout=timeout,
                                 input=input, env=env)
    except FileNotFoundError as exc:
        raise KeeperError("Git is required for git-installed plugins") from exc
    except subprocess.TimeoutExpired as exc:
        raise KeeperError(f"git {args[0]} timed out") from exc
    if check and process.returncode:
        raise KeeperError(f"git {args[0]} failed: " + process.stderr.decode(errors="replace").strip())
    return process


def commit(directory: Path, ref: str) -> str | None:
    process = git(directory, "rev-parse", "--verify", "-q", ref + "^{commit}", check=False)
    if process.returncode:
        return None
    return process.stdout.decode().strip() or None


def canonical(tree: Tree) -> Tree:
    """Git tracks only the executable bit; compare working files the same way."""
    return {name: File(file.data, 0o755 if file.mode & 0o111 else 0o644) for name, file in tree.items()}


def working_tree(directory: Path) -> Tree:
    """The owner's version: tracked and untracked files, without git-ignored ones.

    Ignored files (caches, local settings) are not edits; apply leaves them as they are.
    """
    ignored = set(filter(None, git(directory, "ls-files", "-z", "--others", "--ignored", "--exclude-standard")
                         .stdout.decode(errors="surrogateescape").split("\0")))
    return canonical({name: file for name, file in read_tree(directory).items() if name not in ignored})


def commit_tree(directory: Path, ref: str) -> Tree:
    listing = git(directory, "ls-tree", "-r", "-z", "--full-tree", ref).stdout
    entries = []
    for item in filter(None, listing.split(b"\0")):
        meta, name = item.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        path = safe_relative(name.decode())
        if kind != "blob" or mode not in ("100644", "100755"):
            raise KeeperError(f"Unsupported git entry {path} ({mode} {kind}); submodules and symlinks are not handled")
        entries.append((path, sha, 0o755 if mode == "100755" else 0o644))
    if not entries:
        return {}
    output = git(directory, "cat-file", "--batch", input=b"".join(sha.encode() + b"\n" for _, sha, _ in entries),
                 timeout=60).stdout
    tree, offset = {}, 0
    for path, sha, mode in entries:
        header_end = output.index(b"\n", offset)
        _, kind, size = output[offset:header_end].decode().split()
        if kind != "blob":
            raise KeeperError(f"Unexpected git object for {path}")
        start = header_end + 1
        tree[path] = File(output[start:start + int(size)], mode)
        offset = start + int(size) + 1
    return tree


class GitPlugins:
    def __init__(self, work):
        self.work = work
        self.keeper = work.keeper
        self.plans = work.plans

    # Discovery and status

    def path(self, ident: str) -> Path:
        return self.keeper.plugins_dir / plugin_id(ident)

    def is_git_plugin(self, ident: str) -> bool:
        path = self.path(ident)
        if path.is_symlink() or not (path / "manifest.json").is_file() or not (path / ".git").is_dir() \
                or (path / ".git").is_symlink():
            return False
        try:
            value = object_json(read_file(path / "manifest.json").data, str(path))
        except KeeperError:
            return False
        info = value.get("omarchy")
        return value.get("id") == ident and not (isinstance(info, dict) and info.get("clonedFrom"))

    def plugins(self) -> list[str]:
        if not self.keeper.plugins_dir.is_dir():
            return []
        return [path.name for path in sorted(self.keeper.plugins_dir.iterdir())
                if not path.name.startswith(".") and self.is_git_plugin(path.name)]

    def inputs(self, ident: str):
        """(directory, head, target, local, base, upstream); upstream is None without a newer fetch."""
        path = self.path(ident)
        no_symlink_path(path)
        head = commit(path, "HEAD")
        if head is None:
            raise KeeperError("This plugin's git checkout has no commit")
        target = commit(path, "FETCH_HEAD")
        local = working_tree(path)
        base = commit_tree(path, head)
        if target is None or target == head:
            return path, head, None, local, base, None
        if git(path, "merge-base", "--is-ancestor", head, target, check=False).returncode != 0:
            if git(path, "merge-base", "--is-ancestor", target, head, check=False).returncode == 0:
                return path, head, None, local, base, None
            raise KeeperError("This plugin has local commits that upstream does not have; update it with git")
        return path, head, target, local, base, commit_tree(path, target)

    def row(self, ident: str) -> dict | None:
        """A status row, or None for an unedited plugin Omarchy can update by itself."""
        path = self.path(ident)
        value = object_json(read_file(path / "manifest.json").data, str(path))
        row = {"id": ident, "name": value.get("name", ident), "kind": "git", "sourceId": None, "path": str(path),
               "tracked": True, "status": "up-to-date", "localChangedFiles": 0, "upstreamChangedFiles": 0,
               "conflicts": 0, "baselineVersion": None, "message": ""}
        history = self.history(ident)
        try:
            _, head, target, local, base, upstream = self.inputs(ident)
        except KeeperError as exc:
            row.update(status="error", message=str(exc))
            return row
        fetch_head = path / ".git" / "FETCH_HEAD"
        row["checkedAt"] = (datetime.fromtimestamp(fetch_head.stat().st_mtime, timezone.utc).isoformat()
                            if fetch_head.exists() else None)
        left = len(changed_files(base, local))
        right = len(changed_files(base, upstream)) if upstream is not None else 0
        if not left and not history["transactions"]:
            return None
        conflicts = sum(merge_file(base.get(name), local.get(name), upstream.get(name))[1]
                        for name in changed_files(base, upstream)) if right else 0
        status = "diverged" if left and right else "local-only" if left else "upstream-only" if right else "up-to-date"
        messages = {"diverged": "An update merges cleanly with your edits." if not conflicts
                    else f"An update and your edits overlap in {conflicts} file(s). Review to choose.",
                    "local-only": "Your edits are kept on top of the installed version. Check for updates to merge newer ones.",
                    "upstream-only": "An update is available.",
                    "up-to-date": "Matches its installed version."}
        row.update(status=status, message=messages[status], localChangedFiles=left, upstreamChangedFiles=right,
                   conflicts=conflicts, baselineVersion=head[:7], upstreamVersion=target[:7] if target else None,
                   baselineHash=tree_hash(base), upstreamHash=tree_hash(upstream) if upstream is not None else None)
        return row

    def fetch(self, ident: str) -> dict:
        """Ask the plugin's origin for its newest commit, as omarchy plugin update does."""
        if not self.is_git_plugin(ident):
            raise KeeperError(f"{ident} is not a git-installed plugin")
        git(self.path(ident), "fetch", "--quiet", "origin", "HEAD", timeout=90)
        row = self.row(ident) or {"id": ident, "status": "up-to-date", "message": "No local edits; Omarchy can update it."}
        return {**row, "cloneId": ident, "message": row["message"] or "Checked for updates."}

    # Plans

    def prepare(self, ident: str) -> dict:
        from .transactions import clone_lock
        with clone_lock(self.keeper, ident):
            if self._pending(self.history(ident)["transactions"]):
                raise KeeperError("Recover the interrupted update before preparing a new review")
            path, head, target, local, base, upstream = self.inputs(ident)
            if upstream is None:
                raise KeeperError("No newer version has been fetched. Check for updates first.")
            changes, candidate = merge_trees(base, local, upstream)
            conflicts, candidate = conflict_groups(changes, candidate, local)
            inputs = {"baselineHash": tree_hash(base), "localHash": tree_hash(local), "upstreamHash": tree_hash(upstream)}
            self.plans.discard_duplicates(ident, inputs)
            branch = git(path, "symbolic-ref", "-q", "HEAD", check=False).stdout.decode().strip() or None
            plan_id = uuid.uuid4().hex
            record = {"schemaVersion": 1, "kind": "git", "id": plan_id, "cloneId": ident, "sourceId": None,
                      "clonePath": str(path), "createdAt": _now(), **inputs, "candidateHash": tree_hash(candidate),
                      "baseVersion": head[:7], "upstreamVersion": target[:7], "head": head, "target": target,
                      "branch": branch, "identity": None, "conflicts": conflicts}
            self.plans.store(plan_id, record, base, local, upstream, candidate)
        return self.plans.review(plan_id)

    def stale(self, record: dict) -> list[str]:
        reasons = []
        path = self.path(record["cloneId"])
        if commit(path, "HEAD") != record["head"]:
            reasons.append("The plugin's installed commit changed after this plan was prepared.")
        if commit(path, record["target"]) != record["target"]:
            reasons.append("The update's commit is no longer available.")
        if tree_hash(working_tree(path)) != record["localHash"]:
            reasons.append("Your plugin changed after this plan was prepared.")
        return reasons

    # Transactions

    def _directory(self, ident):
        return self.keeper.state_dir / "git-transactions" / plugin_id(ident)

    def history(self, ident: str) -> dict:
        directory = self._directory(ident)
        no_symlink_path(directory)
        records = []
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if path.name.startswith("."):
                    continue
                record = object_json(read_file(path / "record.json").data, str(path))
                if record.get("schemaVersion") != 1 or record.get("cloneId") != ident or record.get("id") != path.name:
                    raise KeeperError(f"Invalid update journal: {path}")
                records.append(record)
        records.sort(key=lambda item: (item["createdAt"], item["id"]))
        pending = self._pending(records)
        return {"cloneId": ident, "transactions": records, "pendingTransaction": pending["id"] if pending else None}

    @staticmethod
    def _pending(records):
        pending = [item for item in records if item["status"] in PENDING]
        if len(pending) > 1:
            raise KeeperError("Multiple unfinished updates require manual inspection")
        return pending[0] if pending else None

    def _persist(self, record, status):
        record.update(status=status, updatedAt=_now())
        if status in ("applied", "undone", "abandoned"):
            record["completedAt"] = record["updatedAt"]
        atomic_json(self._directory(record["cloneId"]) / record["id"] / "record.json", record)

    def _state(self, path: Path) -> tuple[str | None, str]:
        return commit(path, "HEAD"), tree_hash(working_tree(path))

    def apply(self, plan_id: str, expect: str, expect_plan: str | None = None) -> dict:
        from .transactions import Transactions, clone_lock, exchange_directories
        record_plan, trees = self.plans.load(plan_id)
        ident = record_plan["cloneId"]
        with clone_lock(self.keeper, ident):
            review = self.plans.review(plan_id)
            record_plan, trees = self.plans.load(plan_id)
            if review["candidateHash"] != expect or (expect_plan and review["planHash"] != expect_plan):
                raise KeeperError("The candidate is not the one you reviewed; review it again")
            if review["stale"] or review["hasConflicts"] or not review["validation"]["valid"]:
                raise KeeperError("Review the plan again: " + "; ".join(review["staleReasons"] + review["validation"]["errors"]))
            if self._pending(self.history(ident)["transactions"]):
                raise KeeperError("Recover the interrupted update first")
            live = self.path(ident)
            candidate = trees["candidate"]
            transaction_id = uuid.uuid4().hex
            stage_root = self.keeper.protect_destination(self.keeper.plugins_dir.parent / ".clone-keeper-staging" / transaction_id)
            Transactions(self.keeper)._probe_exchange(stage_root, live)
            stage = stage_root / "plugin"
            try:
                shutil.copytree(live, stage, symlinks=True)
                self._build(stage, record_plan, candidate)
                if self._state(stage) != (record_plan["target"], tree_hash(candidate)):
                    raise KeeperError("The staged update does not match the review")
            except BaseException:
                # Nothing is journaled or live yet; the staged copy is disposable.
                shutil.rmtree(stage_root, ignore_errors=True)
                raise
            record = {"schemaVersion": 1, "id": transaction_id, "cloneId": ident, "planId": plan_id,
                      "clonePath": str(live), "stage": str(stage), "createdAt": _now(),
                      "head": record_plan["head"], "target": record_plan["target"],
                      "beforeHash": record_plan["localHash"], "candidateHash": tree_hash(candidate)}
            self._persist(record, "prepared")
            exchange_directories(stage, live)
            self._persist(record, "applied")
            self.plans.discard(ident, lambda other, _: other.get("kind") == "git")
        return self._result(record)

    def _build(self, stage: Path, plan: dict, candidate: Tree) -> None:
        """Point the staged copy at upstream and make its working tree the candidate."""
        owned = working_tree(stage)
        if plan["branch"]:
            git(stage, "update-ref", plan["branch"], plan["target"], plan["head"])
        else:
            git(stage, "update-ref", "--no-deref", "HEAD", plan["target"], plan["head"])
        git(stage, "read-tree", plan["target"])
        for name in owned:
            if name not in candidate:
                (stage / name).unlink()
        for parent, directories, _ in os.walk(stage, topdown=False):
            for name in directories:
                directory = Path(parent) / name
                if ".git" not in directory.relative_to(stage).parts and not any(directory.iterdir()):
                    directory.rmdir()
        for name, file in candidate.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(file.data)
            target.chmod(file.mode)
        git(stage, "update-index", "-q", "--refresh", check=False)

    def undo(self, ident: str) -> dict:
        from .transactions import clone_lock, exchange_directories
        with clone_lock(self.keeper, ident):
            records = self.history(ident)["transactions"]
            if self._pending(records):
                raise KeeperError("Recover the interrupted update first")
            applied = [item for item in records if item["status"] == "applied"]
            if not applied:
                raise KeeperError("There is no applied update to undo")
            record = applied[-1]
            live, backup = self.path(ident), Path(record["stage"])
            if self._state(live) != (record["target"], record["candidateHash"]):
                raise KeeperError("The plugin changed since this update; refusing undo")
            if not backup.is_dir() or self._state(backup) != (record["head"], record["beforeHash"]):
                raise KeeperError(f"The saved previous version is missing or changed: {backup}")
            self._persist(record, "undo-prepared")
            exchange_directories(backup, live)
            self._persist(record, "undone")
        return self._result(record)

    def recover(self, ident: str) -> dict:
        """Finish or abandon an update interrupted around its directory exchange."""
        from .transactions import clone_lock
        with clone_lock(self.keeper, ident):
            record = self._pending(self.history(ident)["transactions"])
            if record is None:
                return {"cloneId": ident, "status": "clean", "transactionId": None, "reloadRequired": False,
                        "message": "No unfinished update."}
            state = self._state(self.path(ident))
            after, before = (record["target"], record["candidateHash"]), (record["head"], record["beforeHash"])
            if record["status"] == "prepared":
                self._persist(record, "applied" if state == after else "abandoned" if state == before else record["status"])
            else:
                self._persist(record, "undone" if state == before else "applied" if state == after else record["status"])
            if record["status"] in PENDING:
                raise KeeperError("The plugin matches neither side of the interrupted update; it is preserved for inspection")
        return self._result(record)

    def _result(self, record):
        messages = {"applied": "Updated; your edits are kept as uncommitted changes. The previous version is in Undo.",
                    "undone": "Previous version and your edits restored.",
                    "abandoned": "The interrupted update had not been applied; nothing changed."}
        return {"cloneId": record["cloneId"], "transactionId": record["id"], "planId": record["planId"],
                "status": record["status"], "candidateHash": record["candidateHash"], "reloadRequired": True,
                "message": messages[record["status"]]}
