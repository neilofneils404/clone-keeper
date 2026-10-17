"""Reviewable cleanup of history that no longer serves undo, recovery or ancestry."""
import os
from pathlib import Path
import shutil

from .core import KeeperError, no_symlink_path, object_json, read_file
from .origins import ReleaseStore
from .transactions import FINAL, clone_lock


def size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def remove(path: Path) -> None:
    """Hide atomically first, so an interrupted removal never looks like live state."""
    if not path.exists():
        return
    hidden = path.with_name(".pruned-" + path.name)
    os.rename(path, hidden)
    shutil.rmtree(hidden)


class Pruner:
    def __init__(self, work):
        self.work = work
        self.keeper = work.keeper

    def candidates(self, keep_transactions=3, keep_releases=3) -> tuple[list[dict], list[dict]]:
        if keep_transactions < 1 or keep_releases < 1:
            raise KeeperError("Keep at least one transaction and one release")
        items, errors = [], []
        status = self.keeper.status()
        clones = {row["id"]: row for row in status["clones"]}
        transactions = self.keeper.state_dir / "transactions"
        no_symlink_path(transactions)
        if transactions.is_dir():
            for directory in sorted(transactions.iterdir()):
                if directory.name.startswith("."):
                    continue
                try:
                    records = self.work.transactions._records(directory.name)
                except (KeeperError, OSError, ValueError) as exc:
                    errors.append({"path": str(directory), "message": str(exc)})
                    continue
                if any(record["status"] not in FINAL for record in records):
                    continue  # Recovery needs every journal of a clone with unfinished work.
                for record in records[:-keep_transactions]:
                    transaction, clone_stage, state_stage = self.work.transactions._paths(record)
                    paths = [path for path in (transaction, clone_stage.parent, state_stage.parent) if path.exists()]
                    items.append({"kind": "transaction", "cloneId": record["cloneId"], "id": record["id"],
                                  "paths": [str(path) for path in paths], "bytes": sum(map(size, paths)),
                                  "reason": f"Older than the newest {keep_transactions} for this clone; "
                                            "undo cannot step back this far after it is removed."})
        for plan in self.work.saved_plans()["plans"]:
            if plan["cloneId"] not in clones:
                path = self.work.plans.path(plan["planId"])
                items.append({"kind": "plan", "cloneId": plan["cloneId"], "id": plan["planId"],
                              "paths": [str(path)], "bytes": size(path), "reason": "Its clone no longer exists."})
        store = ReleaseStore(self.keeper)
        try:
            recent = set(store.versions()[-keep_releases:])
            needed = {row["sourceId"] for row in clones.values() if not row["tracked"]}
            for path, source_id, meta in store.records():
                if source_id in needed or recent.intersection(meta["versions"]) or not recent:
                    continue
                items.append({"kind": "release", "sourceId": source_id, "id": meta["treeHash"],
                              "versions": meta["versions"], "paths": [str(path)], "bytes": size(path),
                              "reason": f"Not shipped by the newest {keep_releases} recorded releases, "
                                        "and no untracked clone needs it."})
        except (KeeperError, OSError, ValueError) as exc:
            errors.append({"path": str(store.root), "message": str(exc)})
        return items, errors

    def run(self, keep_transactions=3, keep_releases=3, confirm=False) -> dict:
        items, errors = self.candidates(keep_transactions, keep_releases)
        removed = []
        if confirm:
            for item in items:
                try:
                    if item["kind"] == "transaction":
                        # Recheck under the clone lock that the journal is still final.
                        with clone_lock(self.keeper, item["cloneId"]):
                            records = {record["id"]: record for record in self.work.transactions._records(item["cloneId"])}
                            if records.get(item["id"], {}).get("status") not in FINAL:
                                continue
                            for path in item["paths"]:
                                remove(Path(path))
                    elif item["kind"] == "plan":
                        with clone_lock(self.keeper, item["cloneId"]):
                            remove(Path(item["paths"][0]))
                    else:
                        remove(Path(item["paths"][0]))
                    removed.append(item["id"])
                except (KeeperError, OSError) as exc:
                    errors.append({"path": item["paths"][0] if item["paths"] else "", "message": str(exc)})
        return {"items": items, "bytes": sum(item["bytes"] for item in items), "removed": removed,
                "dryRun": not confirm, "errors": errors}
