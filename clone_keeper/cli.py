"""Command-line interface; JSON is a stable boundary for the Omarchy widget."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from . import __version__
from .core import Keeper, KeeperError


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Track and preview changes to customized Omarchy plugins.")
    root.add_argument("--version", action="version", version=f"Clone Keeper {__version__}")
    root.add_argument("--omarchy-path", type=Path,
                      default=Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")))
    root.add_argument("--plugins-dir", type=Path, default=Path.home() / ".config/omarchy/plugins")
    root.add_argument("--state-dir", type=Path,
                      default=Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "clone-keeper")
    commands = root.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="Discover clones and compare them with recorded baselines")
    status.add_argument("--json", action="store_true")
    track = commands.add_parser("track", help="Record a verified or explicitly supplied original baseline")
    track.add_argument("clone_id")
    track.add_argument("--baseline", type=Path, help="Known original upstream plugin directory (with manifest.json)")
    track.add_argument("--json", action="store_true")
    preview = commands.add_parser("preview", help="Preview a three-way merge without modifying the clone")
    preview.add_argument("clone_id")
    preview.add_argument("--json", action="store_true")
    export = commands.add_parser("export", help="Export a conflict-free candidate to a NEW review directory")
    export.add_argument("clone_id")
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--json", action="store_true")
    return root


def human_status(result: dict) -> None:
    print(f"Omarchy source version: {result['upstreamVersion']} (content hashes determine changes)")
    if not result["clones"]:
        print("No built-in plugin clones found.")
    for row in result["clones"]:
        print(f"{row['id']}: {row['status']} — {row['message']}")
        if row["tracked"]:
            print(f"  {row['localChangedFiles']} local / {row['upstreamChangedFiles']} upstream files changed")
    for error in result["errors"]:
        print(f"Skipped {error['path']}: {error['message']}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    keeper = Keeper(args.omarchy_path, args.plugins_dir, args.state_dir)
    try:
        if args.command == "status":
            result = keeper.status()
        elif args.command == "track":
            result = keeper.track(args.clone_id, args.baseline)
        elif args.command == "preview":
            result, _ = keeper.preview(args.clone_id)
        else:
            result = keeper.export(args.clone_id, args.output)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "status":
            human_status(result)
        elif args.command == "track":
            print(f"Tracking {result['cloneId']} from {result['provenance']}.")
            print(f"Baseline: {result['baselineHash'][:12]} ({result['statePath']})")
        elif args.command == "preview":
            print(f"{result['cloneId']}: {result['summary']['files']} changed files, "
                  f"{result['summary']['conflicts']} conflicts")
            for change in result["changes"]:
                print(f"\n{change['path']} [{change['status']}]")
                if change["reason"]:
                    print(change["reason"])
                print(change["diff"] or "No change to your current file.")
            for error in result["validation"]["errors"]:
                print(f"Validation: {error}")
            print("\nPreview only. Installed clone and baseline are unchanged.")
        else:
            print(f"Exported {result['files']} files to {result['output']}")
            print(result["message"])
        return 0
    except (KeeperError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"clone-keeper: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
