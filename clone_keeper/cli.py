"""CLI and JSON boundary shared by the native Omarchy panel."""
import argparse
import json
import os
from pathlib import Path
import sys

from . import __version__
from .core import Keeper, KeeperError
from .service import Workflow


def parser():
    root = argparse.ArgumentParser(description="Keep your Omarchy customizations while reviewing upstream improvements.")
    root.add_argument("--version", action="version", version=f"Clone Keeper {__version__}")
    root.add_argument("--omarchy-path", type=Path, default=Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")))
    root.add_argument("--plugins-dir", type=Path, default=Path.home() / ".config/omarchy/plugins")
    root.add_argument("--state-dir", type=Path, default=Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "clone-keeper")
    commands = root.add_subparsers(dest="command", required=True)
    descriptions = {
        "status": "Discover clones and compare their recorded baselines", "sources": "List built-in sources for new clones",
        "clone": "Create a disabled clone and capture its original baseline", "enable": "Enable an installed clone",
        "track": "Record a verified or explicitly supplied original baseline", "preview": "Inspect a merge without saving a plan",
        "export": "Export a clean candidate to a new directory", "prepare": "Save a merge plan for review and resolution",
        "plans": "List saved merge plans", "review": "Review a saved plan and detect stale inputs",
        "resolve": "Choose a conflict resolution or accept an edited candidate", "apply": "Apply the exact reviewed candidate with recovery and undo",
        "history": "Inspect saved transactions", "undo": "Restore the most recent applied transaction",
        "recover": "Finish an interrupted operation from its journal"}
    for name, description in descriptions.items():
        command = commands.add_parser(name, help=description)
        command.add_argument("--json", action="store_true")
        if name in ("track", "preview", "export", "prepare", "history", "undo", "recover", "enable"):
            command.add_argument("clone_id")
        if name in ("review", "resolve", "apply"):
            command.add_argument("plan_id")
        if name == "track":
            command.add_argument("--baseline", type=Path, help="Known original source directory with manifest.json")
        elif name == "clone":
            command.add_argument("source_id")
            command.add_argument("--id", dest="clone_id")
            command.add_argument("--enable", action="store_true", help="Also enable the new clone in the live shell")
        elif name == "plans":
            command.add_argument("--clone", dest="clone_id")
        elif name == "export":
            command.add_argument("--output", required=True, type=Path)
        elif name == "resolve":
            command.add_argument("--path", required=True)
            choice = command.add_mutually_exclusive_group(required=True)
            choice.add_argument("--take", choices=["local", "upstream", "base", "delete", "edited"])
            choice.add_argument("--file", type=Path)
        elif name == "apply":
            command.add_argument("--expect", required=True, help="Exact candidateHash from the reviewed plan")
            command.add_argument("--expect-plan", help="Optional planHash from the reviewed plan")
    return root


def human(result, command):
    if command == "status":
        print(f"Omarchy source version: {result['upstreamVersion']} (content hashes determine changes)")
        if not result["clones"]:
            print("No built-in plugin clones found.")
        for row in result["clones"]:
            print(f"{row['id']}: {row['status']} — {row['message']}")
            if row["tracked"]:
                print(f"  {row['localChangedFiles']} local / {row['upstreamChangedFiles']} upstream files changed")
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
    elif command == "track":
        print(f"Tracking {result['cloneId']} from {result['provenance']}.")
        print(f"Baseline: {result['baselineHash']} ({result['statePath']})")
    else:
        print(result.get("message", "Done."))
        if "output" in result:
            print(result["output"])
    if result.get("warning"):
        print(result["warning"], file=sys.stderr)


def main(argv=None):
    args = parser().parse_args(argv)
    keeper = Keeper(args.omarchy_path, args.plugins_dir, args.state_dir)
    work = Workflow(keeper)
    try:
        command = args.command
        if command == "status":
            result = work.status()
        elif command == "sources":
            result = work.sources()
        elif command == "clone":
            if args.enable and not work.is_live():
                raise KeeperError("Enable is only available for the installed user plugin directory")
            result = work.refresh(work.create(args.source_id, args.clone_id))
            if args.enable:
                try:
                    result.update(work.enable(result["cloneId"]))
                except KeeperError as exc:
                    result["enabled"] = None
                    result["warning"] = "Clone saved, but activation was not confirmed. Check its status in Omarchy: " + str(exc)
        elif command == "enable":
            result = work.enable(args.clone_id)
        elif command == "track":
            result = work.track(args.clone_id, args.baseline)
        elif command == "preview":
            result, _ = keeper.preview(args.clone_id)
        elif command == "export":
            result = keeper.export(args.clone_id, args.output)
        elif command == "prepare":
            result = work.plans.prepare(args.clone_id)
        elif command == "plans":
            result = work.saved_plans(args.clone_id)
        elif command == "review":
            result = work.plans.review(args.plan_id)
        elif command == "resolve":
            result = work.plans.resolve(args.plan_id, args.path, args.take, args.file)
        elif command == "apply":
            result = work.refresh(work.transactions.apply(args.plan_id, args.expect, args.expect_plan))
        elif command == "history":
            result = work.transactions.history(args.clone_id)
        elif command == "undo":
            result = work.refresh(work.transactions.undo(args.clone_id))
        else:
            result = work.refresh(work.recover(args.clone_id))
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            human(result, command)
        return 0
    except (KeeperError, OSError, ValueError, KeyError, TypeError) as exc:
        message = str(exc)
        print(f"clone-keeper: {message}", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": message, "message": message}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
