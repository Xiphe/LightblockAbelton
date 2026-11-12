#!/usr/bin/env python3
"""Write one proof command. Nothing executes until the Remote Script polls.

This CLI never opens Live or MIDI, enables automatically, or retries a command.
Use --status to inspect the current state before writing an explicit operation.
An expired unconsumed command is not replaced automatically. Inspect its session
and expiry against --status, then explicitly remove that scratch command file
before writing another command. Never remove an unexpired pending command.
"""

import argparse
import json
from pathlib import Path
import sys
import time

from LightpadController.proof_mailbox import (
    atomic_json, COMMAND_FILE, STATE_FILE, MAX_COMMAND_BYTES,
    LEGACY_OPERATIONS, OPERATIONS)

CONTROLLER_COMMANDS = frozenset(("begin", "accept", "accept_next", "retry", "cancel", "advance"))


def read_object(path, maximum=MAX_COMMAND_BYTES):
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("proof JSON exceeds %d bytes" % maximum)
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("proof JSON must be an object")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", nargs="?", choices=sorted(OPERATIONS | CONTROLLER_COMMANDS))
    parser.add_argument("--directory", type=Path, required=True, help="fixed scratch proof directory")
    parser.add_argument("--status", action="store_true", help="print state without writing a command")
    parser.add_argument("--confirm-set-path")
    parser.add_argument("--confirm-fingerprint")
    parser.add_argument("--confirm-sync-ui-off", nargs="+",
                        choices=("link", "tempo_follower", "external_sync"))
    parser.add_argument("--layer", choices=("A", "B", "C"))
    parser.add_argument("--group", type=int, choices=range(8), help="normal-executor probe group index 0..7")
    args = parser.parse_args(argv)
    if args.status and args.operation:
        parser.error("--status cannot be combined with an operation")
    if not args.status and args.operation is None:
        parser.error("choose --status or one explicit operation")
    if args.operation == "enable" and not (args.confirm_set_path and args.confirm_fingerprint):
        parser.error("enable requires --confirm-set-path and --confirm-fingerprint")
    if args.operation == "start" and args.layer is None:
        parser.error("start requires --layer A, B or C")
    if args.operation != "enable" and (args.confirm_set_path or args.confirm_fingerprint):
        parser.error("confirmation arguments belong to enable only")
    if args.operation != "enable" and args.confirm_sync_ui_off is not None:
        parser.error("--confirm-sync-ui-off belongs to enable only")
    if args.operation != "start" and args.layer:
        parser.error("--layer belongs to start only")
    if args.operation in CONTROLLER_COMMANDS and args.group is None:
        parser.error("controller probe commands require --group 0..7")
    if args.operation not in CONTROLLER_COMMANDS and args.group is not None:
        parser.error("--group belongs to controller probe commands only")
    directory = args.directory.resolve()
    try:
        state = read_object(directory / STATE_FILE, maximum=1024 * 1024)
        if args.status:
            print(json.dumps(state, indent=2, sort_keys=True))
            return 0
        if (state.get("status") != "ready" or not isinstance(state.get("session"), str)
                or not state["session"] or type(state.get("last_seq")) is not int
                or state["last_seq"] < 0):
            raise ValueError("proof mailbox is not ready")
        # The source CLI may be newer than the separately installed script.
        # An old state file authorizes only its original five operations.
        advertised = state.get("operations", sorted(LEGACY_OPERATIONS))
        if not isinstance(advertised, list) or any(type(op) is not str for op in advertised):
            raise ValueError("mailbox operations advertisement is invalid")
        if args.operation not in advertised:
            raise ValueError("installed mailbox does not advertise operation %s" % args.operation)
        try:
            command = read_object(directory / COMMAND_FILE)
        except FileNotFoundError:
            command = None
        if command is not None and command.get("session") == state["session"]:
            if type(command.get("seq")) is not int or command["seq"] > state["last_seq"]:
                raise ValueError("an outstanding command has not been consumed; inspect --status. "
                                 "Only an expired scratch command may be explicitly removed")
        envelope = {"session": state["session"], "seq": state["last_seq"] + 1,
                    "expires_at": time.time() + 10, "op": args.operation}
        if args.operation == "enable":
            envelope.update(confirm_set_path=args.confirm_set_path,
                            confirm_fingerprint=args.confirm_fingerprint)
            if args.confirm_sync_ui_off is not None:
                envelope["confirm_sync_ui_off"] = args.confirm_sync_ui_off
        if args.operation == "start":
            envelope["layer"] = args.layer
        if args.operation in CONTROLLER_COMMANDS:
            envelope["group"] = args.group
        encoded = json.dumps(envelope, allow_nan=False).encode("utf-8")
        if len(encoded) + 1 > MAX_COMMAND_BYTES:
            raise ValueError("command exceeds 8192 bytes")
        atomic_json(directory / COMMAND_FILE, envelope)
        print(json.dumps({"written": args.operation, "seq": envelope["seq"],
                          "execution": "pending script poll"}, sort_keys=True))
        return 0
    except (OSError, ValueError, UnicodeError) as error:
        print("Proof command refused: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
