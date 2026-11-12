"""File commands are bounded, session-scoped and never retried after dispatch."""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.proof_mailbox import (
    Mailbox, atomic_json, COMMAND_FILE, STATE_FILE, OPERATIONS)
import proof_command


class MailboxTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.calls, self.logs = [], []
        def dispatch(envelope):
            self.calls.append(envelope)
            return {"ok": True, "enabled": envelope["op"] == "enable"}
        self.mailbox = Mailbox(self.directory, dispatch, self.logs.append)

    def envelope(self, **changes):
        result = dict(session=self.mailbox.session, seq=self.mailbox.last_seq + 1,
                      expires_at=time.time() + 10, op="inspect")
        result.update(changes)
        return result

    def submit(self, **changes):
        atomic_json(self.directory / COMMAND_FILE, self.envelope(**changes))
        return self.mailbox.poll()

    def cli(self, *args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return proof_command.main(["--directory", str(self.directory)] + list(args))

    def test_construction_is_disabled_and_does_not_dispatch_old_command(self):
        old = self.envelope(op="start", layer="A")
        atomic_json(self.directory / COMMAND_FILE, old)
        replacement = Mailbox(self.directory, self.calls.append, self.logs.append)
        self.assertNotEqual(replacement.session, self.mailbox.session)
        self.assertTrue(replacement.state["driver_disabled"])
        self.assertEqual(self.calls, [])
        self.assertIsNone(replacement.poll())
        self.assertEqual(self.calls, [])
        self.assertEqual(replacement.last_seq, 0)

    def test_dispatch_is_explicit_and_same_file_or_touched_duplicate_never_reexecutes(self):
        self.submit(op="enable")
        self.assertFalse(self.mailbox.state["driver_disabled"])
        self.assertIsNone(self.mailbox.poll())
        os.utime(self.directory / COMMAND_FILE, None)
        self.assertIsNone(self.mailbox.poll())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.mailbox.last_seq, 1)
        self.assertEqual(self.mailbox.state["result"], {"ok": True, "enabled": True})

    def test_changed_bytes_with_same_mtime_are_checked(self):
        path = self.directory / COMMAND_FILE
        self.submit(session="old")
        stamp = path.stat().st_mtime_ns
        atomic_json(path, self.envelope())
        os.utime(path, ns=(stamp, stamp))
        self.mailbox.poll()
        self.assertEqual(len(self.calls), 1)

    def test_wrong_nonce_expired_future_and_sequence_gaps_never_dispatch(self):
        invalid = [dict(session="old"), dict(seq=0), dict(seq=True), dict(seq=2),
                   dict(expires_at=time.time() - 1), dict(expires_at=time.time() + 60),
                   dict(expires_at=float("nan")), dict(expires_at=True)]
        for changes in invalid:
            with self.subTest(changes=changes):
                # Write manually to permit JSON NaN for the malformed envelope.
                (self.directory / COMMAND_FILE).write_text(json.dumps(self.envelope(**changes)))
                self.assertIsNone(self.mailbox.poll())
                self.assertEqual(self.mailbox.last_seq, 0)
        self.assertEqual(self.calls, [])

    def test_dispatch_exception_consumes_and_caches_sequence_with_bounded_diagnostic(self):
        def throwing(envelope):
            self.calls.append(envelope)
            raise RuntimeError("fixture guard mismatch " + "x" * 1000)
        self.mailbox.dispatch = throwing
        result = self.submit(op="start", layer="A")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "RuntimeError")
        self.assertTrue(result["message"].startswith("fixture guard mismatch"))
        self.assertEqual(len(result["message"]), 500)
        self.assertEqual(self.mailbox._results[1], result)
        self.assertEqual(self.mailbox.last_seq, 1)
        self.assertIsNone(self.mailbox.poll())
        self.assertIsNone(self.submit(seq=1, op="start", layer="B"))
        self.assertEqual(len(self.calls), 1)
        record = json.loads(self.logs[0].split("Lightpad proof mailbox ", 1)[1])
        self.assertEqual(record["message"], result["message"])

    def test_unknown_operation_consumes_error_without_dispatching(self):
        self.assertEqual(self.submit(op="delete"), {"ok": False, "error": "unsupported_operation"})
        self.assertEqual(self.mailbox.last_seq, 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.mailbox._results[1]["error"], "unsupported_operation")
        self.assertEqual(self.submit(op=[]), {"ok": False, "error": "unsupported_operation"})
        self.assertEqual(self.mailbox.last_seq, 2)

    def test_logging_failure_cannot_prevent_consuming_a_driver_exception(self):
        def failing(envelope):
            raise RuntimeError("dispatch failed")
        def bad_log(message):
            raise OSError("log failed")
        self.mailbox.dispatch, self.mailbox.emit = failing, bad_log
        self.assertEqual(self.submit(), {"ok": False, "error": "RuntimeError", "message": "dispatch failed"})
        self.assertEqual(self.mailbox._results[1]["error"], "RuntimeError")
        self.assertEqual(self.mailbox.last_seq, 1)

    def test_malformed_duplicate_keys_and_oversized_files_do_not_dispatch(self):
        path = self.directory / COMMAND_FILE
        for content in [b"{", b"[]", b'{"seq":1,"seq":2}', b" " * 8193, b"\xff"]:
            path.write_bytes(content)
            self.assertIsNone(self.mailbox.poll())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.mailbox.last_seq, 0)

    def test_non_json_dispatch_result_is_consumed_as_error(self):
        self.mailbox.dispatch = lambda envelope: object()
        self.assertEqual(self.submit()["error"], "TypeError")
        self.assertEqual(self.mailbox.last_seq, 1)
        persisted = json.loads((self.directory / STATE_FILE).read_text())
        self.assertEqual(persisted["result"]["error"], "TypeError")

    def test_status_cli_never_writes_a_command(self):
        self.assertEqual(self.cli("--status"), 0)
        self.assertFalse((self.directory / COMMAND_FILE).exists())

    def test_status_can_report_a_driver_inspection_larger_than_command_limit(self):
        self.mailbox.dispatch = lambda envelope: {"inspection": "x" * 9000}
        self.submit()
        self.assertEqual(self.cli("--status"), 0)

    def test_expiry_window_accepts_thirty_seconds_but_not_past_or_later(self):
        with patch("LightpadController.proof_mailbox.time.time", return_value=1000):
            self.assertIsNone(self.submit(expires_at=1000))
            self.assertIsNone(self.submit(expires_at=1030.001))
            self.assertIsNotNone(self.submit(expires_at=1030))
        self.assertEqual(len(self.calls), 1)

    def test_cli_writes_one_expiring_command_and_refuses_an_outstanding_one(self):
        before = time.time()
        self.assertEqual(self.cli("inspect"), 0)
        command = json.loads((self.directory / COMMAND_FILE).read_text())
        self.assertEqual((command["op"], command["session"], command["seq"]),
                         ("inspect", self.mailbox.session, 1))
        self.assertTrue(before + 9 < command["expires_at"] < time.time() + 11)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.cli("disable"), 2)
        self.mailbox.poll()
        self.assertEqual(self.cli("disable"), 0)
        self.mailbox.poll()
        self.assertEqual([call["op"] for call in self.calls], ["inspect", "disable"])

    def test_cli_enable_and_start_require_explicit_confirmations_and_layer(self):
        for arguments in [("enable",), ("enable", "--confirm-set-path", "/scratch.als"), ("start",)]:
            with self.subTest(arguments=arguments):
                with self.assertRaises(SystemExit) as error:
                    self.cli(*arguments)
                self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.cli("enable", "--confirm-set-path", "/scratch.als",
                                  "--confirm-fingerprint", "abc"), 0)
        self.mailbox.poll()
        self.assertEqual(self.calls[-1]["confirm_set_path"], "/scratch.als")
        self.assertEqual(self.calls[-1]["confirm_fingerprint"], "abc")
        self.assertEqual(self.cli("start", "--layer", "A"), 0)
        self.mailbox.poll()
        self.assertEqual(self.calls[-1]["layer"], "A")

    def test_cli_sync_ui_confirmation_is_explicit_and_enable_only(self):
        confirmations = ["--confirm-set-path", "/scratch.als", "--confirm-fingerprint", "abc"]
        for arguments in [["enable"] + confirmations + ["--confirm-sync-ui-off"],
                          ["inspect", "--confirm-sync-ui-off", "link"]]:
            with self.subTest(arguments=arguments):
                with self.assertRaises(SystemExit) as error:
                    self.cli(*arguments)
                self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.cli("enable", *confirmations, "--confirm-sync-ui-off",
                                  "link", "tempo_follower", "external_sync"), 0)
        command = json.loads((self.directory / COMMAND_FILE).read_text())
        self.assertEqual(command["confirm_sync_ui_off"], ["link", "tempo_follower", "external_sync"])
        self.assertEqual(self.calls, [])

    def test_legacy_installed_state_cannot_accept_new_stop_operations(self):
        legacy = dict(self.mailbox.state)
        del legacy["operations"]
        atomic_json(self.directory / STATE_FILE, legacy)
        for operation in ("stop_attempt", "discard_attempt"):
            self.assertEqual(self.cli(operation), 2)
            self.assertFalse((self.directory / COMMAND_FILE).exists())
        self.assertEqual(self.cli("inspect"), 0)
        self.assertEqual(self.calls, [])

    def test_new_mailbox_advertises_and_cli_writes_explicit_stop_and_discard(self):
        state = json.loads((self.directory / STATE_FILE).read_text())
        self.assertEqual(state["operations"], sorted(OPERATIONS))
        for operation in ("stop_attempt", "discard_attempt"):
            self.assertEqual(self.cli(operation), 0)
            command = json.loads((self.directory / COMMAND_FILE).read_text())
            self.assertEqual(command["op"], operation)
            self.assertNotIn("layer", command)
            self.mailbox.poll()
        self.assertEqual([command["op"] for command in self.calls],
                         ["stop_attempt", "discard_attempt"])

    def test_cli_checks_advertisement_for_legacy_operations_too(self):
        state = dict(self.mailbox.state, operations=["inspect"])
        atomic_json(self.directory / STATE_FILE, state)
        self.assertEqual(self.cli("disable"), 2)
        self.assertFalse((self.directory / COMMAND_FILE).exists())

    def test_stop_operations_reject_start_layer_argument(self):
        for operation in ("stop_attempt", "discard_attempt"):
            with self.assertRaises(SystemExit) as error:
                self.cli(operation, "--layer", "A")
            self.assertEqual(error.exception.code, 2)

    def test_custom_operation_set_is_advertised_exactly_without_changing_other_instances(self):
        operations = ("inspect", "begin", "cancel", "advance")
        self.mailbox = Mailbox(self.directory, self.calls.append, self.logs.append,
                               operations=operations)
        persisted = json.loads((self.directory / STATE_FILE).read_text())
        self.assertEqual(persisted["operations"], sorted(operations))
        self.assertEqual(self.mailbox.operations, frozenset(operations))
        self.assertTrue(persisted["driver_disabled"])
        ordinary = Mailbox(self.directory / "separate", self.calls.append, self.logs.append)
        self.assertEqual(ordinary.state["operations"], sorted(OPERATIONS))
        self.assertEqual(self.calls, [])

    def test_controller_mailbox_rejects_raw_primitive_operations_without_dispatch(self):
        self.mailbox = Mailbox(self.directory, self.calls.append, self.logs.append,
                               operations=("inspect", "begin", "cancel"))
        for operation in ("start", "finish", "stop_attempt", "discard_attempt"):
            with self.subTest(operation=operation):
                self.assertEqual(self.submit(op=operation, layer="A"),
                                 {"ok": False, "error": "unsupported_operation"})
                self.assertEqual(self.calls, [])
        self.submit(op="begin", group=0)
        self.assertEqual([(command["op"], command["group"]) for command in self.calls],
                         [("begin", 0)])

    def test_cli_controller_commands_write_explicit_group_only_when_advertised(self):
        commands = ("begin", "accept", "accept_next", "retry", "cancel", "advance")
        operations = ("inspect",) + commands
        self.mailbox = Mailbox(self.directory, self.calls.append, self.logs.append,
                               operations=operations)
        for operation in commands:
            with self.subTest(operation=operation):
                self.assertEqual(self.cli(operation, "--group", "0"), 0)
                command = json.loads((self.directory / COMMAND_FILE).read_text())
                self.assertEqual((command["op"], command["group"], command["session"]),
                                 (operation, 0, self.mailbox.session))
                self.assertNotIn("layer", command)
                self.mailbox.poll()
        self.assertEqual(len(self.calls), len(commands))

    def test_controller_commands_require_group_and_reject_out_of_range_groups(self):
        self.mailbox = Mailbox(self.directory, self.calls.append, self.logs.append,
                               operations=proof_command.CONTROLLER_COMMANDS)
        for operation in proof_command.CONTROLLER_COMMANDS:
            with self.subTest(operation=operation), self.assertRaises(SystemExit) as error:
                self.cli(operation)
            self.assertEqual(error.exception.code, 2)
        for group in ("-1", "8", "wrong"):
            with self.subTest(group=group), self.assertRaises(SystemExit) as error:
                self.cli("begin", "--group", group)
            self.assertEqual(error.exception.code, 2)
        self.assertFalse((self.directory / COMMAND_FILE).exists())
        self.assertEqual(self.calls, [])

    def test_group_argument_cannot_be_attached_to_primitive_or_status_operations(self):
        cases = [("inspect", "--group", "0"), ("disable", "--group", "0"),
                 ("start", "--layer", "A", "--group", "0"), ("--status", "--group", "0")]
        for arguments in cases:
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as error:
                self.cli(*arguments)
            self.assertEqual(error.exception.code, 2)
        self.assertFalse((self.directory / COMMAND_FILE).exists())
        self.assertEqual(self.calls, [])

    def test_old_primitive_or_legacy_state_cannot_authorize_begin(self):
        for advertisement in (sorted(OPERATIONS), None, ["inspect", "cancel"]):
            with self.subTest(advertisement=advertisement):
                state = dict(self.mailbox.state)
                if advertisement is None:
                    del state["operations"]
                else:
                    state["operations"] = advertisement
                atomic_json(self.directory / STATE_FILE, state)
                self.assertEqual(self.cli("begin", "--group", "0"), 2)
                self.assertFalse((self.directory / COMMAND_FILE).exists())
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
