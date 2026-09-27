"""Durable whole-directory transactions; no plugin code is ever executed.

The journal and immutable snapshots precede either live rename. Recovery compares
contents, not merely a recorded phase, since a process can die between rename and
journal publication. Advisory locks coordinate Clone Keeper writers; unrelated
editors must still be detected by the content checks.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid

from .core import (
    File, KeeperError, as_clone, canonical_tree, inventory, is_text, is_within,
    json_bytes, no_symlink_path, object_json, plugin_id, read_file, read_tree,
    styled_manifest, tree_hash, validate_candidate, write_tree,
)


FINAL = {"applied", "undone"}
JOURNAL_STATUSES = FINAL | {"prepared", "apply-clone", "apply-state",
                            "undo-prepared", "undo-clone", "undo-state"}
SNAPSHOT_NAMES = {"before-clone", "before-state", "after-clone", "after-state"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root):
    for parent, _, names in os.walk(root, topdown=False):
        for name in names:
            fd = os.open(Path(parent) / name, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        _fsync_directory(Path(parent))
    _fsync_directory(root.parent)


fsync_tree = _fsync_tree


def _atomic_json(path, value):
    no_symlink_path(path)
    temporary = path.parent / ("." + path.name + "." + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _exchange(left, right):
    """Linux renameat2(RENAME_EXCHANGE), never a non-atomic fallback."""
    no_symlink_path(left)
    no_symlink_path(right)
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise KeeperError("Atomic directory exchange requires Linux renameat2")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(left), -100, os.fsencode(right), 2) != 0:
        code = ctypes.get_errno()
        if code in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.EXDEV):
            raise KeeperError("This filesystem does not support atomic directory exchange on these paths")
        raise OSError(code, os.strerror(code), str(left))
    _fsync_directory(left.parent)
    if left.parent != right.parent:
        _fsync_directory(right.parent)


def exchange_directories(left, right):
    """Publish a caller's staged directory durably with an atomic exchange."""
    _fsync_tree(left)
    _exchange(left, right)


def publish_directory(stage, destination):
    """Durably publish a new directory without replacing any existing path.

    RENAME_NOREPLACE avoids an empty destination reservation that a crashed
    creator could otherwise mistake for a directory it owns during recovery.
    """
    no_symlink_path(stage)
    no_symlink_path(destination)
    _fsync_tree(stage)
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise KeeperError("Atomic directory publication requires Linux renameat2")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        if code in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.EXDEV):
            raise KeeperError("This filesystem does not support atomic no-replace directory publication")
        raise OSError(code, os.strerror(code), str(destination))
    _fsync_directory(destination.parent)
    if stage.parent != destination.parent:
        _fsync_directory(stage.parent)


@contextmanager
def clone_lock(keeper, clone_id):
    """Shared writer lock for planning, tracking, and filesystem transactions."""
    with Transactions(keeper)._lock(clone_id):
        yield


def _supported_metadata(path, *, directory=False):
    """Refuse metadata our byte-and-mode snapshots cannot faithfully restore."""
    info = path.stat(follow_symlinks=False)
    if not directory:
        if info.st_nlink > 1:
            raise KeeperError(f"Hard-linked files are unsupported; refusing to discard link relationships: {path}")
        if stat.S_IMODE(info.st_mode) & 0o7000:
            raise KeeperError(f"Special file permission bits are unsupported: {path}")
    try:
        attributes = os.listxattr(path, follow_symlinks=False)
    except OSError as exc:
        if exc.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP):
            raise
        attributes = []
    if attributes:
        raise KeeperError(f"Extended attributes or ACLs are unsupported; refusing to discard them: {path}")


def _directories(root):
    no_symlink_path(root)
    _supported_metadata(root, directory=True)
    result = {".": stat.S_IMODE(root.stat().st_mode)}
    def fail(error):
        raise error
    for parent, directories, filenames in os.walk(root, followlinks=False, onerror=fail):
        if ".git" in directories or ".git" in filenames:
            raise KeeperError("Git-managed clones are not supported for apply or undo; .git will not be discarded")
        for name in directories:
            path = Path(parent) / name
            no_symlink_path(path)
            _supported_metadata(path, directory=True)
            result[path.relative_to(root).as_posix()] = stat.S_IMODE(path.stat().st_mode)
    return result


def _snapshot(root):
    tree = read_tree(root)
    directories = _directories(root)
    for relative in tree:
        _supported_metadata(root / relative)
    digest = hashlib.sha256(json_bytes({"files": inventory(tree), "directories": directories})).hexdigest()
    return tree, directories, digest


def _write_snapshot(destination, tree, directories):
    destination.mkdir(mode=0o700)
    write_tree(destination, tree)
    for relative in sorted(directories, key=lambda name: len(Path(name).parts)):
        path = destination if relative == "." else destination / relative
        path.mkdir(parents=True, exist_ok=True)
    for relative in sorted(directories, key=lambda name: len(Path(name).parts), reverse=True):
        (destination if relative == "." else destination / relative).chmod(directories[relative])
    _fsync_tree(destination)


class Transactions:
    def __init__(self, keeper):
        self.keeper = keeper

    def _checkpoint(self, name, record):
        """Tests may mock this hook to simulate process death after durable work."""

    def _roots(self):
        k = self.keeper
        for root in (k.omarchy_path, k.plugins_dir, k.state_dir):
            no_symlink_path(root)
        roots = [p.resolve() for p in (k.omarchy_path, k.plugins_dir, k.state_dir)]
        for index, left in enumerate(roots):
            for right in roots[index + 1:]:
                if is_within(left, right) or is_within(right, left):
                    raise KeeperError("Omarchy sources, installed plugins, and Clone Keeper state must be separate trees")
        return roots

    @contextmanager
    def _lock(self, ident):
        plugin_id(ident)
        if ident.startswith("omarchy."):
            raise KeeperError("Transactions only support personal clone IDs")
        self._roots()
        directory = self.keeper.state_dir / "locks"
        no_symlink_path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(directory / (ident + ".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _directory(self, ident):
        return self.keeper.state_dir / "transactions" / plugin_id(ident)

    def _records(self, ident):
        directory = self._directory(ident)
        no_symlink_path(directory)
        if not directory.exists():
            return []
        records = []
        for path in sorted(directory.iterdir()):
            no_symlink_path(path)
            if not path.is_dir() or not (path / "record.json").exists():
                continue
            record = object_json(read_file(path / "record.json").data, str(path / "record.json"))
            if record.get("schemaVersion") != 1 or record.get("cloneId") != ident or record.get("id") != path.name:
                raise KeeperError(f"Invalid transaction journal: {path}")
            self._validate_record_paths(record)
            records.append(record)
        return sorted(records, key=lambda item: (item["createdAt"], item["id"]))

    def _validate_record_paths(self, record):
        if not isinstance(record.get("cloneId"), str):
            raise KeeperError("Invalid transaction clone ID")
        ident = plugin_id(record["cloneId"])
        transaction_id = record.get("id")
        if not isinstance(transaction_id, str) or len(transaction_id) != 32 or any(c not in "0123456789abcdef" for c in transaction_id):
            raise KeeperError("Invalid transaction ID")
        if not isinstance(record.get("clonePath"), str) or Path(record["clonePath"]) != self.keeper.plugins_dir / ident:
            raise KeeperError("Transaction belongs to a different installed clone path")
        if not isinstance(record.get("status"), str) or record["status"] not in JOURNAL_STATUSES:
            raise KeeperError("Invalid transaction status; journal requires inspection")
        if not isinstance(record.get("createdAt"), str):
            raise KeeperError("Invalid transaction creation timestamp")
        snapshots = record.get("snapshots")
        if not isinstance(snapshots, dict) or set(snapshots) != SNAPSHOT_NAMES:
            raise KeeperError("Invalid transaction snapshot inventory")
        for name, digest in snapshots.items():
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise KeeperError(f"Invalid transaction snapshot hash: {name}")

    def _persist(self, record, status):
        record["status"] = status
        record["updatedAt"] = _now()
        if status in FINAL:
            record["completedAt"] = record["updatedAt"]
        _atomic_json(self._directory(record["cloneId"]) / record["id"] / "record.json", record)
        self._checkpoint(status, record)

    def _pending(self, records):
        pending = [item for item in records if item["status"] not in FINAL]
        if len(pending) > 1:
            raise KeeperError("Multiple unfinished transactions require manual inspection")
        return pending[0] if pending else None

    def _paths(self, record):
        transaction = self._directory(record["cloneId"]) / record["id"]
        clone_stage_root = self.keeper.plugins_dir.parent / ".clone-keeper-staging" / record["id"]
        state_stage_root = self.keeper.state_dir / ".staging" / record["id"]
        for root in (clone_stage_root, state_stage_root):
            no_symlink_path(root)
        self.keeper.protect_destination(clone_stage_root)
        return transaction, clone_stage_root / "clone", state_stage_root / "state"

    def _probe_exchange(self, root, live):
        """Fail before journaling if this filesystem cannot perform our commit."""
        no_symlink_path(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.stat().st_dev != live.stat().st_dev:
            raise KeeperError("Atomic staging and the installed directory are on different filesystems")
        with tempfile.TemporaryDirectory(prefix=".probe-", dir=root) as temporary:
            left, right = Path(temporary) / "left", Path(temporary) / "right"
            left.mkdir(mode=0o700)
            right.mkdir(mode=0o700)
            _exchange(left, right)

    def _load_snapshot(self, record, name):
        transaction, _, _ = self._paths(record)
        tree, directories, digest = _snapshot(transaction / name)
        expected = record["snapshots"][name]
        if digest != expected:
            raise KeeperError(f"Transaction backup integrity check failed: {name}")
        return tree, directories, digest

    def _stage(self, path, record, target_name, other_name):
        tree, directories, target_hash = self._load_snapshot(record, target_name)
        if path.exists():
            _, _, digest = _snapshot(path)
            if digest == target_hash:
                return
            if digest != record["snapshots"][other_name]:
                raise KeeperError(f"Transaction staging contents changed; preserving {path}")
            shutil.rmtree(path)
            _fsync_directory(path.parent)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write_snapshot(path, tree, directories)

    def _move_to(self, live, stage, record, target_name, other_name, phase):
        _, _, actual = _snapshot(live)
        target_hash = record["snapshots"][target_name]
        other_hash = record["snapshots"][other_name]
        if actual == target_hash:
            self._persist(record, phase)
            return
        if actual != other_hash:
            raise KeeperError(f"Files changed outside this transaction; refusing to replace {live}")
        self._stage(stage, record, target_name, other_name)
        if stage.stat().st_dev != live.stat().st_dev:
            raise KeeperError("Atomic staging and the installed directory are on different filesystems")
        if _snapshot(live)[2] != other_hash:
            raise KeeperError(f"Files changed while staging: {live}")
        _exchange(stage, live)
        self._checkpoint(phase + "-exchanged", record)
        if _snapshot(stage)[2] != other_hash:
            # Preserve edits arriving between the last check and the exchange.
            if _snapshot(live)[2] == target_hash:
                _exchange(stage, live)
            raise KeeperError("Concurrent edits detected during directory exchange; preserved for inspection")
        self._persist(record, phase)

    def _finish(self, record, undo=False):
        transaction, clone_stage, state_stage = self._paths(record)
        del transaction
        clone = self.keeper.plugins_dir / record["cloneId"]
        state = self.keeper.state_path(record["cloneId"])
        desired, previous = ("before", "after") if undo else ("after", "before")
        # Validate BOTH live directories before changing either of them.
        for path, suffix in ((clone, "clone"), (state, "state")):
            digest = _snapshot(path)[2]
            if digest not in {record["snapshots"][f"before-{suffix}"], record["snapshots"][f"after-{suffix}"]}:
                raise KeeperError(f"Unrecognized edits prevent recovery; preserving {path}")
        for name in ("before-clone", "before-state", "after-clone", "after-state"):
            self._load_snapshot(record, name)
        for path, suffix in ((clone_stage, "clone"), (state_stage, "state")):
            if path.exists() and _snapshot(path)[2] not in {
                    record["snapshots"][f"before-{suffix}"], record["snapshots"][f"after-{suffix}"]}:
                raise KeeperError(f"Unrecognized edits in displaced directory; preserving {path}")
        prefix = "undo" if undo else "apply"
        self._move_to(clone, clone_stage, record, desired + "-clone", previous + "-clone", prefix + "-clone")
        self._move_to(state, state_stage, record, desired + "-state", previous + "-state", prefix + "-state")
        if _snapshot(clone)[2] != record["snapshots"][desired + "-clone"] or _snapshot(state)[2] != record["snapshots"][desired + "-state"]:
            raise KeeperError("Files changed before transaction completion; use recover after inspecting edits")
        self._persist(record, "undone" if undo else "applied")
        return self._result(record)

    def _result(self, record):
        return {"cloneId": record["cloneId"], "transactionId": record["id"],
                "planId": record["planId"], "status": record["status"],
                "candidateHash": record["candidateHash"], "reloadRequired": True,
                "message": "Clone and recorded baseline restored." if record["status"] == "undone" else "Candidate applied; the baseline now records upstream."}

    def history(self, clone_id):
        self._roots()
        records = self._records(clone_id)
        pending = self._pending(records)
        return {"cloneId": clone_id, "transactions": records,
                "pendingTransaction": pending["id"] if pending else None}

    def recover(self, clone_id):
        with self._lock(clone_id):
            record = self._pending(self._records(clone_id))
            if record is None:
                return {"cloneId": clone_id, "status": "clean", "transactionId": None,
                        "reloadRequired": False, "message": "No unfinished transaction."}
            undo = record["status"].startswith("undo")
            result = self._finish(record, undo=undo)
            if not undo:
                self._discard_plans(record)
            return result

    def _discard_plans(self, record):
        """Best-effort cleanup; a committed apply never fails because of it."""
        from .plans import Plans
        try:
            plans = Plans(self.keeper)
            meta, _ = self.keeper.load_baseline(record["cloneId"])
            applied = object_json(read_file(plans.path(record["planId"]) / "record.json").data, "plan record")
            plans.discard_superseded(record["cloneId"], {meta["baselineHash"], applied["baselineHash"]})
        except (KeeperError, OSError, ValueError, KeyError):
            pass

    def undo(self, clone_id):
        with self._lock(clone_id):
            records = self._records(clone_id)
            if self._pending(records):
                raise KeeperError("An unfinished transaction must be recovered before undo")
            applied = [record for record in records if record["status"] == "applied"]
            if not applied:
                raise KeeperError("There is no applied transaction to undo")
            record = applied[-1]
            for live, name in ((self.keeper.plugins_dir / clone_id, "after-clone"),
                               (self.keeper.state_path(clone_id), "after-state")):
                if _snapshot(live)[2] != record["snapshots"][name]:
                    raise KeeperError("Clone or baseline changed since this transaction; refusing undo")
            for name in ("before-clone", "before-state", "after-clone", "after-state"):
                self._load_snapshot(record, name)
            self._persist(record, "undo-prepared")
            return self._finish(record, undo=True)

    def apply(self, plan_id, expected_candidate_hash, expected_plan_hash=None):
        # Imported lazily to keep planning independent of filesystem mutation.
        from .plans import Plans
        plans = Plans(self.keeper)
        plan, trees = plans.load(plan_id)
        with self._lock(plan["cloneId"]):
            plan, trees = plans.load(plan_id)
            review = plans.review(plan_id)
            return self._apply_loaded(plan, trees, review, expected_candidate_hash, expected_plan_hash)

    def _apply_loaded(self, plan, trees, review, expected_candidate_hash, expected_plan_hash):
        k = self.keeper
        ident = plan["cloneId"]
        if self._pending(self._records(ident)):
            raise KeeperError("An unfinished transaction must be recovered before apply")
        if review["candidateHash"] != expected_candidate_hash:
            raise KeeperError("Candidate hash differs from the reviewed candidate")
        if expected_plan_hash is not None and review["planHash"] != expected_plan_hash:
            raise KeeperError("Plan changed since review")
        if review.get("hasConflicts") or review.get("unresolved"):
            raise KeeperError("Resolve every conflict before applying")
        if review.get("stale"):
            raise KeeperError("Merge inputs changed; prepare and review a new plan")
        if not review.get("validation", {}).get("valid"):
            raise KeeperError("Candidate failed plan validation")
        candidate, upstream = trees["candidate"], trees["upstream"]
        if tree_hash(candidate) != expected_candidate_hash:
            raise KeeperError("Candidate integrity check failed")
        errors = validate_candidate(candidate, ident, plan["sourceId"])
        for name, file in candidate.items():
            if is_text(file.data) and any(line.startswith((b"<<<<<<< ", b"||||||| ", b">>>>>>> ")) for line in file.data.splitlines()):
                errors.append(f"Unresolved merge markers in {name}")
        if errors:
            raise KeeperError("Candidate is invalid: " + "; ".join(errors))
        clone_path, local, _, source_id = k.clone(ident)
        if str(clone_path) != plan["clonePath"] or source_id != plan["sourceId"]:
            raise KeeperError("Plan belongs to a different clone or source")
        meta, base = k.load_baseline(ident)
        if meta.get("creationPending"):
            raise KeeperError("Clone creation is unfinished; run recover before applying a merge")
        if meta.get("sourceId") != source_id or meta.get("identity") != plan["identity"]:
            raise KeeperError("Recorded baseline identity changed since this plan was prepared")
        if tree_hash(local) != plan["localHash"] or tree_hash(base) != plan["baselineHash"]:
            raise KeeperError("Clone or baseline changed since this plan was prepared")
        source = k.sources().get(source_id)
        if source is None or tree_hash(as_clone(source.materialize(), meta["identity"], source_id)) != plan["upstreamHash"]:
            raise KeeperError("Upstream changed since this plan was prepared")
        if tree_hash(upstream) != plan["upstreamHash"]:
            raise KeeperError("Prepared upstream integrity check failed")
        before_clone = _snapshot(clone_path)
        before_state = _snapshot(k.state_path(ident))
        if tree_hash(canonical_tree(before_clone[0])) != plan["localHash"]:
            raise KeeperError("Clone changed while capturing the transaction backup")
        if object_json(before_state[0]["record.json"].data, "baseline record") != meta:
            raise KeeperError("Baseline metadata changed while capturing the transaction backup")
        captured_base = {name.removeprefix("baseline/"): file for name, file in before_state[0].items() if name.startswith("baseline/")}
        if tree_hash(captured_base) != plan["baselineHash"]:
            raise KeeperError("Baseline files changed while capturing the transaction backup")
        transaction_id = uuid.uuid4().hex
        record = {"schemaVersion": 1, "id": transaction_id, "planId": plan["id"],
                  "cloneId": ident, "sourceId": source_id, "clonePath": str(clone_path),
                  "createdAt": _now(), "status": "prepared", "candidateHash": tree_hash(candidate),
                  "snapshots": {}}
        _, clone_stage, state_stage = self._paths(record)
        self._probe_exchange(clone_stage.parent, clone_path)
        self._probe_exchange(state_stage.parent, k.state_path(ident))
        after_meta = {**meta, "baselineVersion": plan["upstreamVersion"],
                      "provenance": "applied-plan", "capturedAt": _now(),
                      "baselineHash": tree_hash(upstream), "files": inventory(upstream),
                      "lastTransaction": transaction_id}
        after_state_tree = {"record.json": File(json_bytes(after_meta), 0o600),
                            **{"baseline/" + name: file for name, file in upstream.items()}}
        transaction = self._directory(ident) / transaction_id
        no_symlink_path(transaction)
        transaction.mkdir(parents=True, mode=0o700)
        _fsync_directory(transaction.parent)
        _fsync_directory(transaction.parent.parent)
        _fsync_directory(transaction.parent.parent.parent)
        # Preserve existing directory permissions and harmless empty local
        # directories. A file introduced by the candidate supersedes its old
        # directory, so those colliding directories must not be recreated.
        candidate_directories = {name: mode for name, mode in before_clone[1].items()
                                 if name == "." or not any(str(parent) in candidate
                                     for parent in (Path(name), *Path(name).parents))}
        # Keep the clone's manifest layout; its parsed content is the reviewed one.
        applied = {**candidate, "manifest.json": styled_manifest(candidate["manifest.json"],
                                                                 before_clone[0].get("manifest.json"))}
        snapshots = {"before-clone": before_clone[:2], "before-state": before_state[:2],
                     "after-clone": (applied, candidate_directories),
                     "after-state": (after_state_tree, {".": before_state[1]["."], "baseline": 0o700})}
        for name, (tree, directories) in snapshots.items():
            _write_snapshot(transaction / name, tree, directories)
            record["snapshots"][name] = _snapshot(transaction / name)[2]
        # Snapshot creation may take time. Check again immediately before the
        # durable intent record makes this an operation recovery can complete.
        if _snapshot(clone_path)[2] != before_clone[2] or _snapshot(k.state_path(ident))[2] != before_state[2]:
            raise KeeperError("Clone or baseline changed while preparing the transaction")
        current_source = k.sources().get(source_id)
        if current_source is None or tree_hash(as_clone(current_source.materialize(), meta["identity"], source_id)) != plan["upstreamHash"]:
            raise KeeperError("Upstream changed while preparing the transaction")
        from .plans import Plans
        final_review = Plans(k).review(plan["id"])
        if (final_review["candidateHash"] != expected_candidate_hash
                or final_review["planHash"] != review["planHash"]
                or final_review["hasConflicts"] or final_review["stale"]
                or not final_review["validation"]["valid"]):
            raise KeeperError("Plan changed while preparing the transaction; review it again")
        # No live changes occur until the complete immutable journal is durable.
        self._persist(record, "prepared")
        result = self._finish(record)
        self._discard_plans(record)
        return result
