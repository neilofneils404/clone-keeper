"""Notice shell components a clone uses that a newer Omarchy no longer provides.

A clone keeps working copies of a built-in's files, but it runs against the
installed shell's shared Ui and Commons components. When Omarchy removes one of
those or one of its members, a clone can break with no change to its own source.
Fingerprints list the names each component declares; they are not a type check.
"""
import re

from .core import KeeperError, is_text, no_symlink_path, object_json, read_file
from .plans import atomic_json

MODULES = ("Ui", "Commons")
DECLARATION = re.compile(r"^\s*(?:(?:readonly|required|default)\s+)*(?:property\s+\S+|signal|function)\s+(\w+)",
                         re.MULTILINE)


def fingerprint(omarchy_path) -> dict:
    """Map each shared component, such as Ui.Panel, to the names it declares."""
    components = {}
    for module in MODULES:
        directory = omarchy_path / "shell" / module
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.qml")):
            text = path.read_text(encoding="utf-8", errors="replace")
            components[f"{module}.{path.stem}"] = sorted(set(DECLARATION.findall(text)))
    return components


def record(keeper, version: str) -> bool:
    """Save the installed shell's fingerprint for this release once."""
    path = keeper.state_dir / "releases" / ".host" / (re.sub(r"[^A-Za-z0-9._-]", "_", version) + ".json")
    no_symlink_path(path)
    if path.exists():
        return False
    components = fingerprint(keeper.omarchy_path)
    if not components:
        return False
    atomic_json(path, {"schemaVersion": 1, "version": version, "components": components})
    return True


def load(keeper, version: str) -> dict | None:
    path = keeper.state_dir / "releases" / ".host" / (re.sub(r"[^A-Za-z0-9._-]", "_", version) + ".json")
    no_symlink_path(path)
    if not path.exists():
        return None
    value = object_json(read_file(path).data, str(path))
    if value.get("schemaVersion") != 1 or not isinstance(value.get("components"), dict):
        raise KeeperError(f"Invalid shell fingerprint: {path}")
    return value["components"]


def baseline_release(meta: dict) -> str | None:
    """The release a baseline was taken from, when its record names one."""
    evidence = meta.get("evidence")
    if isinstance(evidence, dict) and evidence.get("versions"):
        return str(evidence["versions"][-1])
    label = str(meta.get("baselineVersion") or "")
    if meta.get("provenance") in ("created-from-source", "verified-current-source", "applied-plan"):
        return label.removeprefix("Omarchy ") or None
    return None


def warnings(tree, before: dict, after: dict) -> list[str]:
    """Shell components and members this clone mentions that are gone now."""
    text = "\n".join(file.data.decode(errors="replace") for name, file in tree.items()
                     if name.endswith((".qml", ".js")) and is_text(file.data))
    found = []
    for component, members in sorted(before.items()):
        module, name = component.split(".", 1)
        # Components are written as Ui.Panel, or bare inside a qs.Ui import.
        used = re.search(rf"\b(?:{module}\.)?{re.escape(name)}\s*(?:\{{|\.)", text) and f"qs.{module}" in text
        if not used:
            continue
        if component not in after:
            found.append(f"{component} is no longer part of the Omarchy shell.")
            continue
        removed = sorted(set(members) - set(after[component]))
        # Count uses such as panel.member, member: value and member(...).
        mentioned = [member for member in removed
                     if re.search(rf"\.{re.escape(member)}\b|\b{re.escape(member)}\s*[:(]", text)]
        if mentioned:
            found.append(f"{component} no longer declares {', '.join(mentioned)}.")
    return found
