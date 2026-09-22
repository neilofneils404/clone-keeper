"""Application workflows around discovery, creation and transaction history."""
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import re
import subprocess
import time

from .core import (File, KeeperError, as_clone, atomic_new_tree, inventory,
                   json_bytes, manifest, no_symlink_path, object_json,
                   plugin_id, read_file, tree_hash, validate_candidate)
from .plans import Plans, atomic_json
from .transactions import Transactions, clone_lock


class Workflow:
    def __init__(self, keeper):
        self.keeper = keeper
        self.plans = Plans(keeper)
        self.transactions = Transactions(keeper)

    def sources(self):
        prefix = re.sub(r"[^A-Za-z0-9_-]", "-", getpass.getuser()).strip("-") or "user"
        return {"sources": [{"id": ident,
                             "name": object_json(read_file(source.path).data, str(source.path)).get("name", ident),
                             "suggestedCloneId": prefix + "." + ident.removeprefix("omarchy.")}
                            for ident, source in sorted(self.keeper.sources().items())],
                "upstreamVersion": self.keeper.version()}

    def saved_plans(self, clone_id=None):
        if clone_id is not None:
            plugin_id(clone_id)
        directory = self.keeper.state_dir / "plans"
        no_symlink_path(directory)
        plans, errors = [], []
        if directory.exists():
            for path in sorted(directory.iterdir()):
                if path.name.startswith("."):
                    continue
                try:
                    record = object_json(read_file(path / "record.json").data, str(path))
                    if record.get("id") != path.name or record.get("schemaVersion") != 1:
                        raise KeeperError("Invalid merge plan record")
                    if clone_id is None or record["cloneId"] == clone_id:
                        plans.append({"planId": path.name, "cloneId": record["cloneId"], "createdAt": record["createdAt"]})
                except (KeeperError, OSError, ValueError, KeyError, TypeError) as exc:
                    errors.append({"path": str(path), "message": str(exc)})
        return {"plans": sorted(plans, key=lambda item: item["createdAt"], reverse=True), "errors": errors}

    def status(self):
        result = self.keeper.status()
        known = {row["id"] for row in result["clones"]}
        directory = self.keeper.state_dir / "clones"
        no_symlink_path(directory)
        if directory.exists():
            for entry in directory.iterdir():
                if entry.name in known or entry.name.startswith("."):
                    continue
                try:
                    record = object_json(read_file(entry / "record.json").data, str(entry))
                    if record.get("creationPending"):
                        result["clones"].append({"id": record["cloneId"], "name": record["identity"]["name"],
                            "sourceId": record["sourceId"], "path": str(self.keeper.plugins_dir / record["cloneId"]),
                            "tracked": True, "status": "error", "message": "Clone creation was interrupted. Recover to finish.",
                            "localChangedFiles": 0, "upstreamChangedFiles": 0, "baselineVersion": record["baselineVersion"]})
                except (KeeperError, OSError, ValueError, KeyError, TypeError) as exc:
                    result["errors"].append({"path": str(entry), "message": str(exc)})
        plans = self.saved_plans()["plans"]
        for row in result["clones"]:
            row.update(latestTransaction=None, pendingTransaction=None, canUndo=False,
                       latestPlan=next((plan for plan in plans if plan["cloneId"] == row["id"]), None))
            try:
                history = self.transactions.history(row["id"])
                records = history["transactions"]
                row["latestTransaction"] = records[-1] if records else None
                row["pendingTransaction"] = history["pendingTransaction"]
                row["canUndo"] = any(item["status"] == "applied" for item in records) and not history["pendingTransaction"]
                if row["tracked"]:
                    meta, _ = self.keeper.load_baseline(row["id"])
                    if meta.get("creationPending"):
                        row["pendingTransaction"] = "creation"
                if row["pendingTransaction"]:
                    row.update(status="error", message="An interrupted operation needs recovery before further changes.", canUndo=False)
            except (KeeperError, OSError, ValueError, KeyError, TypeError) as exc:
                row.update(status="error", message=str(exc), canUndo=False)
        return result

    def track(self, ident, baseline=None):
        with clone_lock(self.keeper, ident):
            return self.keeper.track(ident, baseline)

    def create(self, source_id, clone_id=None):
        plugin_id(source_id)
        if clone_id is None:
            selected = next((item for item in self.sources()["sources"] if item["id"] == source_id), None)
            if selected is None:
                raise KeeperError(f"Unknown built-in source: {source_id}")
            clone_id = selected["suggestedCloneId"]
        plugin_id(clone_id)
        with clone_lock(self.keeper, clone_id):
            path = self.keeper.plugins_dir / clone_id
            no_symlink_path(path)
            if path.exists() or self.keeper.state_path(clone_id).exists():
                raise KeeperError("Clone or baseline already exists. Use recover for an interrupted creation.")
            self._check_unconfigured(clone_id)
            source = self.keeper.sources().get(source_id)
            if source is None:
                raise KeeperError(f"Unknown built-in source: {source_id}")
            original = source.materialize()
            name = "My " + manifest(original).get("name", source_id)
            identity = {"id": clone_id, "name": name, "displayName": name}
            baseline = as_clone(original, identity, source_id)
            errors = validate_candidate(baseline, clone_id, source_id)
            if errors:
                raise KeeperError("Invalid clone: " + "; ".join(errors))
            record = {"schemaVersion": 1, "cloneId": clone_id, "sourceId": source_id,
                      "clonePath": str(path), "identity": identity, "creationPending": True,
                      "capturedAt": datetime.now(timezone.utc).isoformat(),
                      "baselineVersion": self.keeper.version(), "provenance": "created-from-source",
                      "baselineHash": tree_hash(baseline), "files": inventory(baseline)}
            contents = {"record.json": File(json_bytes(record), 0o600),
                        **{"baseline/" + name: file for name, file in baseline.items()}}
            atomic_new_tree(self.keeper.protect_destination(self.keeper.state_path(clone_id), state=True), contents)
            return self._finish_creation(clone_id)

    def _finish_creation(self, clone_id):
        record, baseline = self.keeper.load_baseline(clone_id)
        if not record.get("creationPending"):
            raise KeeperError("This clone has no unfinished creation")
        path = self.keeper.plugins_dir / clone_id
        no_symlink_path(path)
        if path.exists():
            _, clone, _, source_id = self.keeper.clone(clone_id)
            if source_id != record["sourceId"] or tree_hash(clone) != record["baselineHash"]:
                raise KeeperError("Clone changed during interrupted creation; preserving it for inspection")
        else:
            self._check_unconfigured(clone_id)
            atomic_new_tree(path, baseline)
        record["creationPending"] = False
        atomic_json(self.keeper.state_path(clone_id) / "record.json", record)
        return {"cloneId": clone_id, "sourceId": record["sourceId"], "path": str(path),
                "tracked": True, "enabled": False, "status": "created", "reloadRequired": True,
                "message": "Clone created and baseline saved. Enable it when you are ready."}

    def _check_unconfigured(self, clone_id):
        config_path = self.keeper.plugins_dir.parent / "shell.json"
        if not config_path.exists():
            return
        config = object_json(read_file(config_path).data, str(config_path))
        bar = config.get("bar", {})
        entries = list(config.get("plugins", []))
        if isinstance(bar, dict):
            if bar.get("id") == clone_id:
                raise KeeperError("That ID is already referenced by the shell. Choose a new ID before creating a disabled clone.")
            layout = bar.get("layout", {})
            if isinstance(layout, dict):
                for section in layout.values():
                    if isinstance(section, list):
                        entries.extend(section)
        if any((item.get("id") if isinstance(item, dict) else item) == clone_id for item in entries):
            raise KeeperError("That ID is already referenced by the shell. Choose a new ID before creating a disabled clone.")

    def recover(self, clone_id):
        with clone_lock(self.keeper, clone_id):
            state = self.keeper.state_path(clone_id)
            if state.exists():
                record, _ = self.keeper.load_baseline(clone_id)
                if record.get("creationPending"):
                    return self._finish_creation(clone_id)
        return self.transactions.recover(clone_id)

    def is_live(self):
        return self.keeper.plugins_dir.resolve() == (Path.home() / ".config/omarchy/plugins").resolve()

    def refresh(self, result):
        if not result.get("reloadRequired") or not self.is_live():
            return result
        try:
            self._shell("shell", "rescanPlugins")
            result["reloadRequired"] = False
        except KeeperError as exc:
            result["warning"] = "Files were saved, but the shell could not refresh: " + str(exc)
        return result

    def _shell(self, *args, timeout=8):
        try:
            process = subprocess.run(["omarchy-shell", *args], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            raise KeeperError(str(exc)) from exc
        if process.returncode or process.stdout.strip() in ("unknown", "error"):
            raise KeeperError(process.stderr.strip() or process.stdout.strip() or "Omarchy shell request failed")
        return process.stdout.strip()

    def enable(self, clone_id):
        if not self.is_live():
            raise KeeperError("Enable is only available for the installed user plugin directory")
        with clone_lock(self.keeper, clone_id):
            if self.transactions.history(clone_id)["pendingTransaction"]:
                raise KeeperError("Recover the unfinished transaction before enabling this clone")
            if self.keeper.state_path(clone_id).exists():
                record, _ = self.keeper.load_baseline(clone_id)
                if record.get("creationPending"):
                    raise KeeperError("Recover the unfinished creation before enabling this clone")
            self.keeper.clone(clone_id)
            # Rescanning is asynchronous. Poll discovery without repeatedly
            # mutating shell configuration, then send exactly one enable call.
            # Keep the writer lock through this bounded activation interval so
            # an apply/recovery cannot begin between the guard and activation.
            deadline = time.monotonic() + 8

            def shell(*args):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise KeeperError("Timed out waiting for the shell; plugin activation was not confirmed")
                return self._shell("shell", *args, timeout=remaining)

            shell("rescanPlugins")
            while True:
                try:
                    plugins = json.loads(shell("listPlugins"))
                except ValueError as exc:
                    raise KeeperError("Shell returned an invalid plugin list; plugin activation was not attempted") from exc
                if not isinstance(plugins, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in plugins):
                    raise KeeperError("Shell returned an invalid plugin list; plugin activation was not attempted")
                if any(item["id"] == clone_id for item in plugins):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise KeeperError("Timed out waiting for shell discovery; plugin activation was not attempted")
                time.sleep(min(0.1, remaining))
            response = shell("enablePlugin", clone_id, "{}")
            if response != "ok":
                raise KeeperError(response or "Shell did not confirm plugin activation")
        return {"cloneId": clone_id, "enabled": True, "message": "Clone enabled."}
