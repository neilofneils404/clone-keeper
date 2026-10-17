"""Optional Qt 6 syntax checking without executing candidate plugin code."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from .core import KeeperError, write_tree


def check_manifest(tree, omarchy_path: Path):
    """Run the matching Omarchy's own manifest validator, which only reads files."""
    validator = omarchy_path / "bin" / "omarchy-plugin-validate"
    if not validator.is_file():
        return []
    with tempfile.TemporaryDirectory(prefix="clone-keeper-validate-") as temporary:
        root = Path(temporary) / "plugin"
        root.mkdir()
        write_tree(root, tree)
        try:
            result = subprocess.run([str(validator), str(root)], text=True, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise KeeperError(f"Omarchy's plugin validator could not complete: {exc}") from exc
    if result.returncode == 0:
        return []
    message = (result.stderr.strip() or result.stdout.strip() or "invalid plugin").replace(str(root), "candidate")
    return ["Omarchy's validator: " + message.removeprefix("omarchy-plugin-validate: ")]


def check_qml(tree):
    files = sorted(name for name in tree if name.endswith((".qml", ".js")))
    if not files:
        return {"available": True, "checked": 0, "errors": [], "message": "No QML or JavaScript files."}
    tool = None
    for candidate in ("/usr/lib/qt6/bin/qmllint", shutil.which("qmllint6"), shutil.which("qmllint")):
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            version = subprocess.run([candidate, "--version"], text=True, capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if "qmllint 6." in version.stdout + version.stderr:
            tool = candidate
            break
    if tool is None:
        return {"available": False, "checked": 0, "errors": [],
                "message": "Qt 6 qmllint is unavailable; only manifest and file structure were checked."}
    with tempfile.TemporaryDirectory(prefix="clone-keeper-lint-") as temporary:
        root = Path(temporary)
        write_tree(root, tree)
        try:
            result = subprocess.run([tool, "--ignore-settings", "--bare", "--json", "-",
                                     *[str(root / name) for name in files]], cwd=root,
                                    text=True, capture_output=True, timeout=30)
            output = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise KeeperError(f"Candidate syntax check could not complete: {exc}") from exc
        reports = output.get("files", [])
        if len(reports) != len(files):
            raise KeeperError("Qt 6 qmllint did not report on every candidate file")
        errors = []
        for report in reports:
            for warning in report.get("warnings", []):
                # Qt also labels some type-dependent diagnostics "syntax" while
                # reporting a successful parse (e.g. Quickshell anchor objects).
                # Only failed parses are blockers; native runtime testing remains
                # necessary for dynamic host types.
                if warning.get("id") == "syntax" and report.get("success") is False:
                    name = str(Path(report["filename"]).relative_to(root))
                    errors.append(f"{name}:{warning.get('line', '?')}: {warning.get('message', 'Syntax error')}")
        return {"available": True, "checked": len(files), "errors": errors,
                "message": "Qt 6 syntax checked. Runtime behavior still needs desktop testing."}
