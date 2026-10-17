"""CLI and JSON boundary shared by the native Omarchy panel."""
import argparse
import json
import os
from pathlib import Path
import sys

from . import __version__
from .core import Keeper, KeeperError, object_json, plugin_id, read_file
from .operations import DETACHABLE, HANDLED
from .origins import Origins
from .plans import plan_identifier
from .service import Workflow


def user_path(value):
    """Expand ~ so a path works the same from a terminal and from the panel."""
    return Path(value).expanduser()


def parser():
    root = argparse.ArgumentParser(description="Keep your Omarchy customizations while reviewing upstream improvements.")
    root.add_argument("--version", action="version", version=f"Clone Keeper {__version__}")
    root.add_argument("--omarchy-path", type=Path, default=Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")))
    root.add_argument("--plugins-dir", type=Path, default=Path.home() / ".config/omarchy/plugins")
    root.add_argument("--state-dir", type=Path, default=Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "clone-keeper")
    root.add_argument("--package-cache", type=Path, default=Path("/var/cache/pacman/pkg"), help=argparse.SUPPRESS)
    root.add_argument("--pacman-log", type=Path, default=Path("/var/log/pacman.log"), help=argparse.SUPPRESS)
    commands = root.add_subparsers(dest="command", required=True)
    descriptions = {
        "status": "Discover clones and compare their recorded baselines", "sources": "List built-in sources for new clones",
        "clone": "Create a disabled clone and capture its original baseline", "enable": "Enable an installed clone",
        "track": "Record a verified or explicitly supplied original baseline",
        "origins": "Find a clone's likely original among Omarchy releases on this machine",
        "sync": "Record the running release's sources and track clones identical to one", "preview": "Inspect a merge without saving a plan",
        "export": "Export a clean candidate to a new directory", "prepare": "Save a merge plan for review and resolution",
        "plans": "List saved merge plans", "review": "Review a saved plan and detect stale inputs",
        "resolve": "Choose a conflict resolution or accept an edited candidate", "apply": "Apply the exact reviewed candidate with recovery and undo",
        "history": "Inspect saved transactions", "undo": "Restore the most recent applied transaction",
        "recover": "Finish an interrupted operation from its journal",
        "dismiss": "Forget recorded outcomes of detached operations",
        "restart-shell": "Restart the Omarchy shell so updated clones load (installed plugins only)",
        "edit": "Open a saved plan's candidate file in your Omarchy editor (installed plugins only)",
        "prune": "List, then with --yes remove, history that undo, recovery and ancestry no longer need",
        "fetch": "Check a git-installed plugin's origin for a newer version (network)",
        "ask-agent": "Open your default coding agent to resolve one candidate file's conflicts (installed plugins only)"}
    for name, description in descriptions.items():
        command = commands.add_parser(name, help=description)
        command.add_argument("--json", action="store_true")
        if name in ("fetch", "track", "origins", "preview", "export", "prepare", "history", "undo", "recover", "enable"):
            command.add_argument("clone_id")
        if name in ("review", "resolve", "apply", "edit", "ask-agent"):
            command.add_argument("plan_id")
        if name in DETACHABLE:
            command.add_argument("--detach", action="store_true",
                                 help="Finish even if this caller is killed; status reports the outcome until dismissed")
        if name in ("track", "origins"):
            command.add_argument("--search", type=user_path, action="append", default=[], metavar="PATH",
                                 help="Also consider an Omarchy tree or package archive")
        if name == "track":
            origin = command.add_mutually_exclusive_group()
            origin.add_argument("--baseline", type=user_path, help="Known original source directory with manifest.json")
            origin.add_argument("--origin", metavar="VERSION", help="Adopt the original from this Omarchy release (see origins)")
            command.add_argument("--expect", metavar="BASELINE_HASH", help="baselineHash of the origin you reviewed")
        elif name == "clone":
            command.add_argument("source_id")
            command.add_argument("--id", dest="clone_id")
            command.add_argument("--enable", action="store_true", help="Also enable the new clone in the live shell")
        elif name == "plans":
            command.add_argument("--clone", dest="clone_id")
        elif name == "dismiss":
            command.add_argument("request_ids", nargs="+", metavar="REQUEST_ID")
        elif name == "export":
            command.add_argument("--output", required=True, type=user_path)
        elif name == "prune":
            command.add_argument("--keep-transactions", type=int, default=3, metavar="N",
                                 help="Undoable transactions to keep per clone (default 3)")
            command.add_argument("--keep-releases", type=int, default=3, metavar="N",
                                 help="Newest recorded Omarchy releases to keep (default 3)")
            command.add_argument("--yes", action="store_true", help="Remove the listed items")
        elif name in ("edit", "ask-agent"):
            command.add_argument("--path", required=True)
        elif name == "resolve":
            command.add_argument("--path", required=True)
            command.add_argument("--conflict", type=int, metavar="N",
                                 help="Resolve only conflict N in this file (see review); --take local, upstream, base or both")
            choice = command.add_mutually_exclusive_group(required=True)
            choice.add_argument("--take", choices=["local", "upstream", "base", "both", "delete", "edited"])
            choice.add_argument("--file", type=user_path)
        elif name == "apply":
            command.add_argument("--expect", required=True, help="Exact candidateHash from the reviewed plan")
            command.add_argument("--expect-plan", help="Optional planHash from the reviewed plan")
            command.add_argument("--restart-shell", action="store_true",
                                 help="Restart the shell to load the update, and undo it if the clone fails to load")
        elif name == "sync":
            command.add_argument("--notify", action="store_true",
                                 help="Send a desktop notification once for each new upstream change")
    return root


def human(result, command):
    if command == "status":
        print(f"Omarchy source version: {result['upstreamVersion']} (content hashes determine changes)")
        if not result["clones"]:
            print("No built-in plugin clones found.")
        for row in result["clones"]:
            print(f"{row['id']}: {row['status']} — {row['message']}")
            if row["status"] == "untracked":
                print(f"  Find its original: clone-keeper origins {row['id']}")
            if row["tracked"]:
                print(f"  {row['localChangedFiles']} local / {row['upstreamChangedFiles']} upstream files changed")
        for item in result.get("operations", []):
            print(f"Operation {item['requestId']} ({item['operation']} {item['cloneId']}): "
                  f"{item['status']} — {item['message']}")
        for error in result["errors"]:
            print(f"Skipped {error['path']}: {error['message']}", file=sys.stderr)
    elif command in ("prepare", "preview", "review", "resolve"):
        if "planId" in result:
            print(f"Plan: {result['planId']}\nCandidate: {result['candidateHash']}")
        print(f"{result['cloneId']}: {result['summary']['files']} changed files, {result['summary']['conflicts']} conflicts")
        for change in result["changes"]:
            print(f"\n{change['path']} [{change['status']}]")
            if change["reason"]:
                print(change["reason"])
            print(change["diff"] or "No change to your current file.")
        for error in result["validation"]["errors"] + result.get("staleReasons", []):
            print(f"Review required: {error}")
        if result["validation"].get("syntax"):
            print(result["validation"]["syntax"]["message"])
        if "editablePath" in result:
            print(f"Editable candidate: {result['editablePath']}")
        print("\nInstalled clone and baseline are unchanged.")
    elif command == "sources":
        for item in result["sources"]:
            print(f"{item['id']}: {item['name']} → {item['suggestedCloneId']}")
    elif command == "plans":
        for item in result["plans"]:
            print(f"{item['planId']}  {item['cloneId']}  {item['createdAt']}")
    elif command == "history":
        for item in result["transactions"]:
            print(f"{item['id']}  {item['status']}  {item['createdAt']}")
        if not result["transactions"]:
            print("No transactions recorded.")
    elif command == "dismiss":
        print(f"Dismissed {len(result['dismissed'])} recorded operation(s).")
    elif command == "origins":
        when = result["cloneCreatedAt"] or "an unrecorded time"
        print(f"{result['cloneId']} was created {when}"
              + (f", while Omarchy {result['installedAtCreation']} was installed." if result["installedAtCreation"] else "."))
        if not result["candidates"]:
            print("No release on this machine contains its source.")
        for item in result["candidates"]:
            mark = "→ " if item.get("suggested") else "  "
            print(f"{mark}Omarchy {', '.join(item['versions'])}: {item['unchangedFiles']}/{item['files']} files unchanged, "
                  f"{item['changedLines']} lines differ{' (installed at creation)' if item['timelineMatch'] else ''}")
            if item["changedFiles"]:
                print(f"    differs in: {', '.join(item['changedFiles'][:8])}")
            print(f"    track {result['cloneId']} --origin {item['versions'][-1]} --expect {item['baselineHash']}")
        for error in result["errors"]:
            print(f"Note: {error['message']}", file=sys.stderr)
    elif command == "prune":
        verb = "Would remove" if result["dryRun"] else "Removed"
        for item in result["items"]:
            label = item.get("cloneId") or item.get("sourceId")
            print(f"{verb} {item['kind']} {item['id'][:12]} ({label}, {item['bytes'] // 1024} KiB): {item['reason']}")
        total = f"{result['bytes'] // 1024} KiB in total." if result["items"] else "Nothing to remove."
        print(total + (" Run again with --yes to remove." if result["dryRun"] and result["items"] else ""))
        for error in result["errors"]:
            print(f"Skipped {error['path']}: {error['message']}", file=sys.stderr)
    elif command == "sync":
        if result["skipped"]:
            print("Another sync is running.")
        else:
            print(f"Recorded Omarchy {result['version']} sources"
                  + (f" ({len(result['recorded'])} new)." if result["recorded"] else "; already up to date."))
        for clone_id in result["tracked"]:
            print(f"Now tracking {clone_id}: it is identical to a recorded release.")
        for error in result["errors"]:
            print(f"Skipped {error['path']}: {error['message']}", file=sys.stderr)
    elif command == "track":
        print(f"Tracking {result['cloneId']} from {result['provenance']}.")
        print(f"Baseline: {result['baselineHash']} ({result['statePath']})")
    else:
        print(result.get("message", "Done."))
        if "output" in result:
            print(result["output"])
    if result.get("warning"):
        print(result["warning"], file=sys.stderr)


def execute(args, keeper, work):
    command = args.command
    if command == "status":
        return work.status()
    if command == "sources":
        return work.sources()
    if command == "clone":
        if args.enable and not work.is_live():
            raise KeeperError("Enable is only available for the installed user plugin directory")
        result = work.refresh(work.create(args.source_id, args.clone_id))
        if args.enable:
            try:
                result.update(work.enable(result["cloneId"]))
            except KeeperError as exc:
                result["enabled"] = None
                result["warning"] = "Clone saved, but activation was not confirmed. Check its status in Omarchy: " + str(exc)
        return result
    if command == "enable":
        return work.enable(args.clone_id)
    if command == "sync":
        return work.sync(Origins(keeper, args.package_cache, args.pacman_log), args.notify)
    if command == "origins":
        return Origins(keeper, args.package_cache, args.pacman_log).find(args.clone_id, args.search)
    if command == "track":
        if args.origin:
            if not args.expect:
                raise KeeperError("--origin requires --expect with the baselineHash shown by origins")
            tree, evidence = Origins(keeper, args.package_cache, args.pacman_log).baseline(
                args.clone_id, args.origin, args.expect, args.search)
            return work.track(args.clone_id, origin=(tree, f"Omarchy {args.origin}", evidence))
        return work.track(args.clone_id, args.baseline)
    if command == "preview":
        return keeper.preview(args.clone_id)[0]
    if command == "export":
        return keeper.export(args.clone_id, args.output)
    if command == "prepare":
        return work.prepare(args.clone_id)
    if command == "fetch":
        return work.git.fetch(args.clone_id)
    if command == "plans":
        return work.saved_plans(args.clone_id)
    if command == "review":
        return work.plans.review(args.plan_id)
    if command == "resolve":
        if args.take == "both" and args.conflict is None:
            raise KeeperError("--take both keeps both sides of one conflict; add --conflict N")
        return work.plans.resolve(args.plan_id, args.path, args.take, args.file, args.conflict)
    if command == "apply":
        if args.restart_shell and not work.is_live():
            raise KeeperError("--restart-shell is only available for the installed user plugin directory")
        result = work.refresh(work.apply(args.plan_id, args.expect, args.expect_plan))
        if args.restart_shell:
            result.update(work.verify_update(result["cloneId"]))
        return result
    if command == "history":
        return work.history(args.clone_id)
    if command == "undo":
        return work.refresh(work.undo(args.clone_id))
    if command == "dismiss":
        return work.operations.dismiss(args.request_ids)
    if command == "prune":
        from .prune import Pruner
        return Pruner(work).run(args.keep_transactions, args.keep_releases, args.yes)
    if command == "ask-agent":
        return work.ask_agent(args.plan_id, args.path)
    if command == "edit":
        return work.open_editor(args.plan_id, args.path)
    if command == "restart-shell":
        return work.restart_shell()
    return work.refresh(work.recover(args.clone_id))


def target_clone(args, keeper, work):
    """Name the clone a detachable command changes, without changing anything."""
    if args.command == "clone":
        return work.clone_id_for(args.source_id, args.clone_id)
    if args.command == "apply":
        path = keeper.state_dir / "plans" / plan_identifier(args.plan_id) / "record.json"
        return plugin_id(object_json(read_file(path).data, "plan record").get("cloneId", ""))
    return plugin_id(args.clone_id)


def main(argv=None):
    args = parser().parse_args(argv)
    keeper = Keeper(args.omarchy_path, args.plugins_dir, args.state_dir)
    work = Workflow(keeper)
    request_id = None
    try:
        if getattr(args, "detach", False):
            # The worker persists its running record before executing work.
            # Reject unsafe roots before even that first state-directory write.
            work.transactions.validate_roots()
            record = work.operations.run(target_clone(args, keeper, work), args.command,
                                         lambda: execute(args, keeper, work))
            request_id = record["requestId"]
            if record["status"] != "succeeded":
                raise KeeperError(record.get("error") or "The operation failed")
            result = {**record["result"], "requestId": request_id}
        else:
            result = execute(args, keeper, work)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            human(result, args.command)
        return 0
    except HANDLED as exc:
        message = str(exc)
        print(f"clone-keeper: {message}", file=sys.stderr)
        if args.json:
            error = {"error": message, "message": message}
            if request_id:
                error["requestId"] = request_id
            print(json.dumps(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
