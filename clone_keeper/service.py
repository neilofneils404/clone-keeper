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
from .operations import Operations
from .origins import ReleaseStore
from .plans import Plans, atomic_json
from .transactions import Transactions, clone_lock


class Workflow:
    def __init__(self, keeper):
        self.keeper = keeper
        self.plans = Plans(keeper)
        self.transactions = Transactions(keeper)
        self.operations = Operations(keeper.state_dir)

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
                        plans.append({"planId": path.name, "cloneId": record["cloneId"], "createdAt": record["createdAt"],
                                      "baselineHash": record["baselineHash"]})
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
        operations, errors = self.operations.summaries()
        result["operations"] = operations
        result["errors"].extend(errors)
        result["live"] = self.is_live()
        self._host_warnings(result)
        enabled = self._enabled_plugins()
        for row in result["clones"]:
            running = next((item["operation"] for item in operations
                            if item["cloneId"] == row["id"] and item["status"] == "running"), None)
            row.update(latestTransaction=None, pendingTransaction=None, canUndo=False, latestPlan=None,
                       runningOperation=running, enabled=enabled.get(row["id"]) if enabled is not None else None)
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
                    # A plan prepared against another baseline can never be applied.
                    row["latestPlan"] = next((plan for plan in plans if plan["cloneId"] == row["id"]
                                              and plan["baselineHash"] == meta.get("baselineHash")), None)
                if row["pendingTransaction"] and not running:
                    row.update(status="error", message="An interrupted operation needs recovery before further changes.", canUndo=False)
            except (KeeperError, OSError, ValueError, KeyError, TypeError) as exc:
                row.update(status="error", message=str(exc), canUndo=False)
        return result

    def _host_warnings(self, result):
        """Shell components each tracked clone uses that its baseline's release had and this one lacks."""
        from . import host
        current, after = result["upstreamVersion"], None
        for row in result["clones"]:
            row["hostWarnings"] = []
            if not row["tracked"] or row["status"] == "error":
                continue
            try:
                meta, _ = self.keeper.load_baseline(row["id"])
                release = host.baseline_release(meta)
                before = host.load(self.keeper, release) if release and release != current else None
                if before is None:
                    continue
                if after is None:
                    after = host.fingerprint(self.keeper.omarchy_path)
                row["hostWarnings"] = host.warnings(self.keeper.clone(row["id"])[1], before, after)
            except (KeeperError, OSError, ValueError, KeyError) as exc:
                result["errors"].append({"path": row["path"], "message": str(exc)})

    def _enabled_plugins(self):
        """Read activation state from the live shell; unknown when unavailable."""
        if not self.is_live():
            return None
        try:
            plugins = json.loads(self._shell("shell", "listPlugins", timeout=2))
            return {item["id"]: item.get("enabled") is True for item in plugins
                    if isinstance(item, dict) and isinstance(item.get("id"), str)}
        except (KeeperError, ValueError, TypeError):
            return None

    def track(self, ident, baseline=None, origin=None):
        with clone_lock(self.keeper, ident):
            return self.keeper.track(ident, baseline, origin)

    def sync(self, origins, notify=False):
        """Record the running release's sources and track clones identical to one.

        Runs whenever the panel loads, which Omarchy does at shell start and
        after any plugin change, including clones made from its own menu.
        It writes only Clone Keeper state, never installed plugins.
        """
        result = ReleaseStore(self.keeper).record()
        result["tracked"] = []
        if result["skipped"]:
            return result
        for row in self.keeper.status()["clones"]:
            if row["tracked"] or row["status"] != "untracked":
                continue
            try:
                with clone_lock(self.keeper, row["id"]):
                    if self.keeper.state_path(row["id"]).exists():
                        continue
                    match, errors = origins.exact(row["id"])
                    result["errors"].extend(errors)
                    if match is not None:
                        self.keeper.track(row["id"], origin=match)
                        result["tracked"].append(row["id"])
            except (KeeperError, OSError, ValueError) as exc:
                result["errors"].append({"path": row["path"], "message": str(exc)})
        result["notified"] = self._notify_updates() if notify else []
        return result

    def _notify_updates(self):
        """Tell the owner once about each upstream change to a tracked clone."""
        path = self.keeper.state_dir / "notified.json"
        no_symlink_path(path)
        try:
            seen = object_json(read_file(path).data, str(path)) if path.exists() else {}
        except KeeperError:
            seen = {}
        sent = []
        for row in self.keeper.status()["clones"]:
            if not row["tracked"] or not row["upstreamChangedFiles"] or row["status"] == "error":
                continue
            key = row["baselineHash"] + ":" + row["upstreamHash"]
            if seen.get(row["id"]) == key:
                continue
            count, conflicts = row["upstreamChangedFiles"], row.get("conflicts", 0)
            detail = (f"Your choices are needed in {conflicts} file(s)." if conflicts
                      else "It merges cleanly with your customizations." if row["localChangedFiles"]
                      else "You have no customizations in the way.")
            try:
                process = subprocess.run(
                    ["omarchy-notification-send", "-g", "\U000f018f", f"Omarchy update for {row['name']}",
                     f"Upstream changed {count} file(s). {detail} Click to review.",
                     "--exec", "omarchy-shell", "neil.clone-keeper", "open"],
                    capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                break
            if process.returncode:
                break
            seen[row["id"]] = key
            sent.append(row["id"])
        if sent:
            atomic_json(path, seen)
        return sent

    def clone_id_for(self, source_id, clone_id=None):
        plugin_id(source_id)
        if clone_id is None:
            selected = next((item for item in self.sources()["sources"] if item["id"] == source_id), None)
            if selected is None:
                raise KeeperError(f"Unknown built-in source: {source_id}")
            clone_id = selected["suggestedCloneId"]
        return plugin_id(clone_id)

    def create(self, source_id, clone_id=None):
        clone_id = self.clone_id_for(source_id, clone_id)
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

    def restart_shell(self, wait=False):
        """Start Omarchy's own shell restart, which loads updated plugin code.

        A live rescan reloads plugins, but changed QML is not reliably rebuilt
        until the shell restarts. The restart replaces the shell running the
        panel, so it runs in its own session and outlives this helper.
        """
        if not self.is_live():
            raise KeeperError("Restarting the shell is only available for the installed user plugin directory")
        # omarchy-restart-shell restarts the session's shell, whatever this
        # process was given. Refuse when that is not the Omarchy in use here,
        # such as a nested test session or a caller outside the desktop.
        session = self._session_omarchy_path()
        if session is None or Path(session).resolve() != self.keeper.omarchy_path.resolve():
            raise KeeperError("The desktop session uses a different Omarchy installation; restart its shell yourself")
        if wait:
            # A detached worker outlives the shell it restarts and waits for the new one.
            try:
                process = subprocess.run(["omarchy-restart-shell"], stdin=subprocess.DEVNULL,
                                         capture_output=True, text=True, timeout=90)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise KeeperError(f"Could not restart the Omarchy shell: {exc}") from exc
            if process.returncode:
                raise KeeperError(process.stderr.strip() or "omarchy-restart-shell failed")
            return {"message": "Restarted the Omarchy shell."}
        try:
            subprocess.Popen(["omarchy-restart-shell"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            raise KeeperError(f"Could not start omarchy-restart-shell: {exc}") from exc
        return {"message": "Restarting the Omarchy shell."}

    def open_editor(self, plan_id, path):
        """Open one candidate file in the owner's editor; the plan is unchanged until accepted."""
        from .core import safe_relative
        if not self.is_live():
            raise KeeperError("Opening an editor is only available for the installed user plugin directory")
        self.plans.load(plan_id)
        target = self.plans.path(plan_id) / "candidate" / safe_relative(path)
        no_symlink_path(target)
        if not target.is_file():
            raise KeeperError("That file is not in the candidate; choose a file present in the result")
        try:
            subprocess.Popen(["omarchy-launch-editor", str(target)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            raise KeeperError(f"Could not start omarchy-launch-editor: {exc}") from exc
        return {"path": str(target), "message": "Editor opened."}

    def ask_agent(self, plan_id, path):
        """Open the owner's default coding agent on one conflicting candidate file.

        The agent works where the owner can watch it, in the plan's candidate
        directory. Its edit counts only once the owner accepts the edited
        candidate, which is then reviewed and confirmed like any other.
        """
        from .core import safe_relative
        if not self.is_live():
            raise KeeperError("Asking an agent is only available for the installed user plugin directory")
        try:
            agent = subprocess.run(["omarchy-default-agent"], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            agent = ""
        if not agent:
            raise KeeperError("Choose a default coding agent first: omarchy default agent")
        record, _ = self.plans.load(plan_id)
        path = safe_relative(path)
        root = self.plans.path(plan_id)
        target = root / "candidate" / path
        no_symlink_path(target)
        if not target.is_file():
            raise KeeperError("That file is not in the candidate; choose a file present in the result")
        versions = {name: root / name / path for name in ("base", "local", "upstream")}
        listed = "\n".join(f"- {label}: {versions[name]}" + ("" if versions[name].is_file() else " (absent)")
                           for name, label in (("base", "Original (the recorded baseline)"),
                                               ("local", "The owner's customized clone"),
                                               ("upstream", "The new Omarchy version")))
        prompt = (
            f"Resolve the merge conflicts in {target}, part of my customized copy {record['cloneId']} "
            f"of the Omarchy shell plugin {record['sourceId']}. The file contains git diff3 conflict blocks: "
            "<<<<<<< Your clone, ||||||| Recorded baseline, =======, >>>>>>> Current upstream.\n\n"
            f"For context, these are read-only:\n{listed}\n\n"
            "Keep my customizations and also keep Omarchy's improvements wherever both can coexist. "
            "Where they truly conflict, prefer my intent and explain the trade-off. Remove every conflict "
            f"marker. Edit only {target}; do not change other files, run the plugin, or restart the shell. "
            "When done, summarize each choice briefly. I will review the result in Clone Keeper before applying it.")
        try:
            subprocess.Popen(["omarchy-agent-prompt", prompt], cwd=root / "candidate", stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            raise KeeperError(f"Could not start omarchy-agent-prompt: {exc}") from exc
        return {"path": str(target), "agent": agent, "prompt": prompt,
                "message": f"Opened {agent} on {path}. When it is done, choose Use edited candidate to review its result."}

    def verify_update(self, clone_id):
        """After applying to an enabled clone, load it and undo the update if it fails to load."""
        from .health import restart_and_check
        try:
            health = restart_and_check(self, clone_id)
        except KeeperError as exc:
            return {"restarted": False, "warning": f"Applied, but the update could not be checked in a restarted shell: {exc}"}
        if health["healthy"]:
            return {"restarted": True, "health": health,
                    "message": "Updated, and the restarted shell loaded it. The previous version is available in Undo."}
        first = health["failures"][0]
        try:
            self.transactions.undo(clone_id)
        except KeeperError as exc:
            return {"restarted": True, "health": health, "undone": False,
                    "warning": f"The update kept {clone_id} from loading ({first}), and undoing it failed: {exc}. "
                               "Use Undo or recover it."}
        result = {"restarted": True, "health": health, "undone": True,
                  "message": f"The update kept {clone_id} from loading, so it was undone. Shell log: {first}"}
        try:
            self.restart_shell(wait=True)
        except KeeperError as exc:
            result["warning"] = f"Restart the Omarchy shell to load the restored version: {exc}"
        return result

    @staticmethod
    def _session_omarchy_path():
        try:
            process = subprocess.run(["systemctl", "--user", "show-environment"],
                                     capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            return None
        values = [line.split("=", 1)[1] for line in process.stdout.splitlines() if line.startswith("OMARCHY_PATH=")]
        return values[-1] if process.returncode == 0 and values else None

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
