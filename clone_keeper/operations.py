"""Mutating requests that outlive the panel process that started them.

Omarchy reloads every plugin widget after a rescan or a change under the
installed plugins directory, and Quickshell kills the helper started by a
destroyed Process. The panel therefore asks for these commands to be detached:
the work runs in its own session and records its outcome under the state
directory, where status reports it to a reopened panel.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import uuid

from .core import KeeperError, no_symlink_path, object_json, plugin_id, read_file
from .plans import atomic_json


DETACHABLE = ("clone", "enable", "apply", "undo", "recover")
FINISHED = {"succeeded", "failed"}
HANDLED = (KeeperError, OSError, ValueError, KeyError, TypeError)
DEFAULT_MESSAGES = {
    "clone": "Clone created and baseline saved. Enable it when you are ready.",
    "enable": "Clone enabled.",
    "apply": "Candidate applied; the baseline now records upstream.",
    "undo": "Clone and recorded baseline restored.",
    "recover": "Interrupted operation recovered.",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def request_identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise KeeperError(f"Invalid operation request ID: {value!r}")
    return value


def process_identity(pid: int) -> str | None:
    """Return a live process's start time, distinguishing reused PIDs."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    # Fields after the command name start at field 3 (state); 22 is starttime.
    if fields[0] in ("Z", "X"):
        return None
    return fields[19]


class Operations:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.directory = state_dir / "operations"

    def path(self, request_id: str) -> Path:
        return self.directory / (request_identifier(request_id) + ".json")

    def _write(self, record: dict) -> None:
        no_symlink_path(self.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_json(self.path(record["requestId"]), record)

    def read(self, request_id: str) -> dict | None:
        path = self.path(request_id)
        if not path.exists():
            return None
        record = object_json(read_file(path).data, str(path))
        if record.get("schemaVersion") != 1 or record.get("requestId") != request_id:
            raise KeeperError(f"Invalid operation record: {path}")
        return record

    def run(self, clone_id: str, operation: str, work) -> dict:
        """Run work() in a new session and wait for its recorded outcome.

        If this process is killed while waiting, the worker continues and its
        record remains for status. The worker never writes to inherited pipes.
        """
        plugin_id(clone_id)
        request_id = uuid.uuid4().hex
        read_end, write_end = os.pipe()
        child = os.fork()
        if child == 0:
            code = 1
            try:
                os.close(read_end)
                os.setsid()
                null = os.open(os.devnull, os.O_RDWR)
                for descriptor in (0, 1, 2):
                    os.dup2(null, descriptor)
                code = self._work(request_id, clone_id, operation, work, write_end)
            finally:
                os._exit(code)
        os.close(write_end)
        # The worker signals once its running record is durable, so a reopened
        # panel can never miss an operation that has already begun.
        with os.fdopen(read_end, "rb") as started:
            started.read()
        os.waitpid(child, 0)
        record = self.read(request_id)
        if record is None or record.get("status") not in FINISHED:
            raise KeeperError("The background operation ended without recording a result; check status")
        return record

    def _work(self, request_id, clone_id, operation, work, started) -> int:
        pid = os.getpid()
        record = {"schemaVersion": 1, "requestId": request_id, "cloneId": clone_id,
                  "operation": operation, "status": "running", "pid": pid,
                  "processStart": process_identity(pid), "startedAt": _now()}
        try:
            self._write(record)
        finally:
            os.close(started)
        try:
            result = work()
            record.update(status="succeeded", result=result)
        except HANDLED as exc:
            record.update(status="failed", error=str(exc))
        except BaseException as exc:  # Record even unexpected failures.
            record.update(status="failed", error=f"Unexpected {type(exc).__name__}: {exc}")
        record["finishedAt"] = _now()
        self._write(record)
        return 0 if record["status"] == "succeeded" else 1

    def summaries(self) -> tuple[list[dict], list[dict]]:
        """Report running and undismissed operations without modifying them."""
        results, errors = [], []
        no_symlink_path(self.directory)
        if not self.directory.exists():
            return results, errors
        for path in sorted(self.directory.iterdir()):
            if path.name.startswith(".") or path.suffix != ".json":
                continue
            try:
                record = self.read(path.stem)
                status = record["status"]
                if status == "running" and process_identity(record["pid"]) != record.get("processStart"):
                    status = "interrupted"
                results.append(self.summary(record, status))
            except HANDLED as exc:
                errors.append({"path": str(path), "message": str(exc)})
        return sorted(results, key=lambda item: item["startedAt"]), errors

    @staticmethod
    def summary(record: dict, status: str | None = None) -> dict:
        status = status or record["status"]
        operation, clone_id = record["operation"], record["cloneId"]
        result = record.get("result") if isinstance(record.get("result"), dict) else {}
        if status == "succeeded":
            message = str(result.get("message") or DEFAULT_MESSAGES.get(operation, "Done."))
        elif status == "failed":
            message = str(record.get("error") or "The operation failed.")
        elif status == "interrupted":
            message = (f"The {operation} request for {clone_id} stopped before recording a result. "
                       "Check the clone's status and recover it if prompted.")
        else:
            message = f"Finishing {operation} for {clone_id}…"
        return {"requestId": record["requestId"], "cloneId": clone_id, "operation": operation,
                "status": status, "message": message, "warning": result.get("warning"),
                "reloadRequired": bool(result.get("reloadRequired")),
                "restarted": bool(result.get("restarted")), "undone": bool(result.get("undone")),
                "startedAt": record["startedAt"], "finishedAt": record.get("finishedAt")}

    def dismiss(self, request_ids: list[str]) -> dict:
        dismissed = []
        for request_id in request_ids:
            record = self.read(request_id)
            if record is None:
                continue
            if record["status"] == "running" and process_identity(record["pid"]) == record.get("processStart"):
                raise KeeperError(f"Operation {request_id} is still running")
            self.path(request_id).unlink()
            dismissed.append(request_id)
        return {"dismissed": dismissed}
