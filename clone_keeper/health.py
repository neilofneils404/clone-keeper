"""Check that an updated clone loads in the restarted Omarchy shell.

A clean merge and a syntax check do not show that a plugin works. After an
apply to an enabled clone, the shell restarts and its own log tells whether
the clone failed to load. Only load failures count against the update;
other messages that mention the clone are reported, since the clone may have
logged them before the update too.
"""
import re
import subprocess
import time

from .core import KeeperError

ANSI = re.compile(r"\x1b\[[0-9;]*m")
FAILURE = re.compile(r"\bfail(ed|ure)?\b|is not a type|is not installed|module .* not found|Syntax ?Error",
                     re.IGNORECASE)


def clone_messages(log: str, clone_id: str, clone_path: str) -> tuple[list[str], list[str]]:
    """Split log lines that mention this clone into load failures and other messages."""
    failures, others = [], []
    # The shell's own messages name the plugin ID; QML errors name its files.
    ident = re.escape(clone_id) + r"(?![A-Za-z0-9._-])"
    mentions = re.compile(r"(Plugin widget|panel plugin|bar option|load failed for) " + ident
                          + "|" + re.escape(clone_path.rstrip("/") + "/"))
    for raw in log.splitlines():
        line = ANSI.sub("", raw).strip()
        if not mentions.search(line):
            continue
        (failures if FAILURE.search(line) else others).append(line)
    return failures, others


def shell_instance(config_dir: str, timeout=10) -> int | None:
    """PID of the running shell instance for this config, from `qs list --all`."""
    try:
        process = subprocess.run(["qs", "list", "--all"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise KeeperError(f"Could not list Quickshell instances: {exc}") from exc
    wanted, found, pid = config_dir.rstrip("/") + "/shell.qml", [], None
    for line in process.stdout.splitlines():
        line = line.strip()
        if line.startswith("Process ID:"):
            pid = int(line.split(":", 1)[1])
        elif line.startswith("Config path:") and line.split(":", 1)[1].strip() == wanted and pid is not None:
            found.append(pid)
    return found[-1] if len(found) == 1 else None


def shell_log(pid: int, timeout=10) -> str:
    # By PID: `qs log -p` can return a previous instance's log after a restart.
    try:
        process = subprocess.run(["qs", "log", "--pid", str(pid), "--tail", "4000"],
                                 capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise KeeperError(f"Could not read the Omarchy shell log: {exc}") from exc
    if process.returncode:
        raise KeeperError("Could not read the Omarchy shell log: " + (process.stderr.strip() or "qs log failed"))
    return process.stdout


def restart_and_check(work, clone_id: str, settle: float = 6.0) -> dict:
    """Restart the shell, let plugins load, and look for this clone's load failures."""
    config = str(work.keeper.omarchy_path / "shell")
    before = shell_instance(config)
    work.restart_shell(wait=True)
    after = shell_instance(config)
    if after is None or after == before:
        raise KeeperError("Could not identify the restarted shell to check its log")
    # The shell answers ping before asynchronous plugin components finish.
    time.sleep(settle)
    failures, others = clone_messages(shell_log(after), clone_id, str(work.keeper.plugins_dir / clone_id))
    return {"healthy": not failures, "failures": failures[:5], "messages": others[:5]}
