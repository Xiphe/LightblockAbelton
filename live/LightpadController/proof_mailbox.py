"""Fixed-directory JSON trigger for an explicitly installed scratch probe.

Creating a command file does nothing until the owning Remote Script polls it.
This module never enables a driver itself, opens MIDI, or calls Live. A fresh
session nonce on every construction invalidates commands from prior instances.
Dispatch owns driver state and must return JSON data. An `enabled` boolean in a
returned object updates the reported driver_disabled field; it changes no driver.
"""

import json
import math
import os
from pathlib import Path
import time
import uuid


MAX_COMMAND_BYTES = 8192
LEGACY_OPERATIONS = frozenset(("inspect", "enable", "start", "finish", "disable"))
OPERATIONS = LEGACY_OPERATIONS | frozenset(("stop_attempt", "discard_attempt"))
COMMAND_FILE = "proof-command.json"
STATE_FILE = "proof-state.json"


def atomic_json(path, value):
    """Replace one JSON file after a complete, flushed same-directory write."""
    path = Path(path)
    encoded = json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    temporary = path.with_name(".%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class Mailbox:
    def __init__(self, directory, dispatch, emit, operations=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.dispatch, self.emit = dispatch, emit
        self.operations = frozenset(OPERATIONS if operations is None else operations)
        if not self.operations or any(type(op) is not str or not op for op in self.operations):
            raise ValueError("explicit mailbox operations must be nonempty names")
        self.session = uuid.uuid4().hex
        self.last_seq = 0
        self._last_seen = None
        self._results = {}
        self.state = {"status": "ready", "session": self.session,
                      "last_seq": 0, "result": None, "driver_disabled": True,
                      "operations": sorted(self.operations)}
        # Never read or dispatch a previous command during construction.
        atomic_json(self.directory / STATE_FILE, self.state)

    def _log(self, event, error, message=None):
        try:
            details = {"event": event, "error": error}
            if message is not None:
                details["message"] = message[:500]
            self.emit("Lightpad proof mailbox " + json.dumps(details))
        except Exception:
            # Logging failure must not interrupt sequence consumption after a
            # driver call that may already have changed the scratch fixture.
            pass

    def _publish(self):
        try:
            atomic_json(self.directory / STATE_FILE, self.state)
        except (OSError, ValueError, TypeError) as error:
            self._log("state_write_error", type(error).__name__)

    def _reject(self, reason, sequence=None):
        rejection = {"error": reason}
        if type(sequence) is int:
            rejection["seq"] = sequence
        self.state["rejection"] = rejection
        self._publish()
        self._log("command_rejected", reason)

    def poll(self):
        """Dispatch at most one newly consumed envelope; otherwise return None."""
        try:
            with (self.directory / COMMAND_FILE).open("rb") as stream:
                stamp = os.fstat(stream.fileno()).st_mtime_ns
                raw = stream.read(MAX_COMMAND_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as error:
            self._log("command_read_error", type(error).__name__)
            return None
        signature = (stamp, raw)
        if signature == self._last_seen:
            return None
        self._last_seen = signature
        if len(raw) > MAX_COMMAND_BYTES:
            self._reject("command_too_large")
            return None
        try:
            envelope = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, RecursionError):
            self._reject("invalid_json")
            return None
        if not isinstance(envelope, dict):
            self._reject("invalid_envelope")
            return None
        sequence = envelope.get("seq")
        if envelope.get("session") != self.session:
            self._reject("wrong_session", sequence)
            return None
        if type(sequence) is not int or sequence <= 0:
            self._reject("invalid_sequence")
            return None
        if sequence != self.last_seq + 1:
            self._reject("stale_sequence" if sequence <= self.last_seq else "sequence_gap", sequence)
            return None
        expires = envelope.get("expires_at")
        now = time.time()
        try:
            valid_expiry = (type(expires) in (int, float) and math.isfinite(expires)
                            and now < expires <= now + 30)
        except OverflowError:
            valid_expiry = False
        if not valid_expiry:
            self._reject("invalid_expiry", sequence)
            return None

        # Consume before dispatch. A thrown exception may follow a mutation;
        # this sequence must never invoke that operation a second time.
        self.last_seq = sequence
        operation = envelope.get("op")
        if type(operation) is not str or operation not in self.operations:
            result = {"ok": False, "error": "unsupported_operation"}
        else:
            try:
                result = self.dispatch(envelope)
                json.dumps(result, allow_nan=False)
            except Exception as error:
                message = str(error)[:500]
                result = {"ok": False, "error": type(error).__name__, "message": message}
                self._log("dispatch_error", type(error).__name__, message)
        self._results[sequence] = result
        self.state.update(last_seq=sequence, result=result)
        self.state.pop("rejection", None)
        if isinstance(result, dict) and type(result.get("enabled")) is bool:
            self.state["driver_disabled"] = not result["enabled"]
        self._publish()
        return result
