#!/usr/bin/env python3
"""Self-contained current-device regressions; no hardware, apps or MIDI ports.

Default: compile both canonical sources with installed blocksctl into a temporary
folder, then execute actual bytecode under the fixed7200-byte harness. Supplied
--main-binary/--metronome-binary avoid compilation and remain hash-checked.
Only maintained source, static fixtures and the pure host timing encoder are
read. See ../README.md for interpreter/native-rounding limits.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from littlefoot_harness import Harness, i32

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures/current_device.json"
CONTRACT = json.loads(FIXTURES.read_text())
PROGRAMS = {}
VMS = []
_spec = importlib.util.spec_from_file_location(
    "current_device_timing", ROOT / "live/LightpadController/timing.py")
timing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(timing)


def compile_programs(destination):
    compiler = shutil.which("blocksctl")
    if compiler is None:
        raise RuntimeError("Offline blocksctl compiler required, or supply both binary paths")
    result = {}
    for name, expected in CONTRACT["programs"].items():
        source = ROOT / expected["source"]
        target = destination / (name + ".bin")
        subprocess.run([compiler, "compile", str(source), "-o", str(target)],
                       check=True, capture_output=True, text=True)
        result[name] = target.read_bytes()
    return result


def word_event(kind, bank, part, revision, values):
    """Minimal direct A9 fixture encoder, independent of the bridge serializer."""
    if not 0 <= len(values) <= 8 or any(not 0 <= value < 128 for value in values):
        raise ValueError("eight seven-bit values maximum")
    data = bytes(values) + bytes(8 - len(values))
    header = 0xA9 | (kind << 8) | (bank << 11) | (part << 15) | (revision << 17)
    return int.from_bytes(data[:4], "little"), int.from_bytes(data[4:], "little"), header


def record(vm, kind, bank, body, revision=0):
    bank = 12 if kind in (1, 2) and bank == 8 else bank
    words = [word_event(kind, bank, offset // 8, revision, body[offset:offset + 8])
             for offset in range(0, len(body), 8)]
    for event in words:
        vm.call("handleMessage/viii", *event)
    return words


def snapshot(vm, group=0, body=None, revision=0):
    body = list(body or [2, 0, 0, 100, 30, 20, 90, 0, 0, 0, 64, 31, 0, 7, 0])
    record(vm, 0, group, body, revision)
    return body


def clock_payload(group=8, *, span=None, position=1.25, first=0, bar=4,
                  numerator=4, denominator=4, running=True, click=False):
    actual = timing.Clock(timing.ONE_BAR if group == 8 else timing.REFERENCE,
                          0 if group == 8 else 7, span if span is not None else 4 if group == 8 else 8,
                          position, first, bar, 120, numerator, denominator, running)
    messages = (timing.encode_global_timing(0, actual, click) if group == 8
                else timing.encode_view_timing(group, 0, actual))
    return [message[2] for message in messages[1:-1]]


def clock(vm, group=8, body=None, revision=0, **options):
    record(vm, 1, group, clock_payload(group, **options) if body is None else body, revision)


def presentation(vm, mode=0, selected=0, flags=0, revision=0, body=None):
    body = list(body) if body is not None else [mode, selected, selected if mode == 2 else 127, 7 if mode == 2 else 0, flags, 0, 0]
    if len(body) == 7: body.append(127)
    record(vm, 2, 8, body, revision)


def new(program, hello=True):
    vm = Harness(PROGRAMS[program])
    VMS.append(vm)
    vm.call("initialise/v")
    if hello:
        vm.call("handleMessage/viii", 274, 0, 0xA9 | (3 << 8) | (12 << 11))
    return vm


def ready(program):
    vm = new(program)
    for group in range(12):
        snapshot(vm, group, [0] * 15 if group >= 8 else None)
    clock(vm)
    clock(vm, 0)
    presentation(vm)
    return vm


def touch(vm, kind, finger=0, x=1.5, y=1.5, milliseconds=10):
    vm.time_ms += milliseconds
    callback = {1: "touchStart", 2: "touchMove", 3: "touchEnd"}[kind]
    return vm.call(callback + "/viffff", finger, x / 7.5, y / 7.5,
                   0 if kind == 3 else .5, 0.)


def frames(vm):
    complete, partial = [], {}
    for status, controller, value in vm.midi:
        if not 20 <= controller <= 26:
            continue
        finger = status - 176
        if controller == 20:
            partial[finger] = []
        if finger not in partial:
            raise AssertionError("touch message outside a framed contact")
        expected = 20 + len(partial[finger])
        if controller != expected:
            raise AssertionError("noncontiguous touch frame")
        partial[finger].append(value)
        if controller == 26:
            raw = partial.pop(finger)
            if raw[0] != raw[6]:
                raise AssertionError("touch frame revision mismatch")
            complete.append(dict(finger=finger, kind=raw[1] & 3, role=(raw[1] >> 2) & 7,
                opened=bool(raw[1] & 64), moved=bool(raw[1] & 32),
                extra=bool(raw[3] & 64), view=(raw[3] >> 4) & 3, group=raw[5] >> 4,
                x=raw[2] + ((raw[3] & 15) << 7), y=raw[4] + ((raw[5] & 15) << 7)))
    if partial:
        raise AssertionError("incomplete emitted touch frame")
    return complete


def markers(vm):
    return [message for message in vm.midi if message[:2] == [191, 108]]


def render_case(case):
    vm = new(case["program"])
    for group in range(12):
        snapshot(vm, group, [0] * 15 if group >= 8 else None)
    for group, body in case.get("snapshots", {}).items():
        snapshot(vm, int(group), body, revision=1)
    clock(vm, **case.get("global_clock", {}))
    clock(vm, 0, **case.get("group_clock", {}))
    presentation(vm, body=case["presentation"])
    vm.time_ms = case.get("time_ms", 1200)
    vm.call("repaint/v")
    return vm


class CurrentDeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PROGRAMS:
            folder = tempfile.TemporaryDirectory(prefix="lightpad-current-device-")
            cls.addClassCleanup(folder.cleanup)
            PROGRAMS.update(compile_programs(Path(folder.name)))

    def test_pinned_sources_binaries_and_real_resource_limit(self):
        for name, expected in CONTRACT["programs"].items():
            with self.subTest(program=name):
                source = ROOT / expected["source"]
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), expected["source_sha256"])
                data = PROGRAMS[name]
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected["binary_sha256"])
                _, code, _, _, heap = struct.unpack_from("<5H", data)
                self.assertEqual((code, heap, code + heap),
                                 (expected["code"], expected["heap"], expected["total"]))
                self.assertLessEqual(code + heap, 7200)
                self.assertEqual(expected["free"], 7200-code-heap)
                vm = new(name, hello=False)
                self.assertEqual(vm.midi, [[191, 110, 19]])

    def test_snapshot_commit_is_atomic_and_native_interruptions_fail_closed(self):
        body = [2, 0, 64, 100, 30, 20, 90, 0, 0, 0, 64, 31, 0, 7, 0]
        start, tail = word_event(0, 0, 0, 0, body[:8]), word_event(0, 0, 1, 0, body[8:])
        bad = [(0, 0, 0), (274, 0, 0xA9 | (3 << 8) | (12 << 11) | (1 << 23)),
               word_event(0, 0, 3, 0, []), word_event(0, 0, 1, 4, body[8:]),
               (start[0] | 128, start[1], start[2]),
               (tail[0], tail[1] | (1 << 24), tail[2]),
               word_event(0, 1, 0, 0, body[:8])]
        for event in bad:
            vm = new("main")
            vm.call("handleMessage/viii", *start)
            self.assertEqual(vm.heap_bytes(0, 15), bytes(15))
            vm.call("handleMessage/viii", *map(i32, event))
            vm.call("handleMessage/viii", *tail)
            self.assertEqual(vm.heap_bytes(0, 15), bytes(15), event)
        vm = new("main")
        record(vm, 0, 0, body)
        self.assertEqual(vm.heap_bytes(0, 15), bytes(body))

    def test_snapshot_enums_and_clock_geometry_reject_without_vm_errors(self):
        for index, value in ((0, 3), (1, 1), (11, 32), (12, 5)):
            vm = new("main")
            body = snapshot(vm)
            body[index] = value
            record(vm, 0, 1, body)
            self.assertEqual(vm.heap_bytes(15, 15), bytes(15))
        for index, value in ((1, 6), (16, 0), (16, 100), (17, 0), (17, 3), (17, 32)):
            vm = ready("main")
            body = clock_payload(0)
            body[index] = value
            clock(vm, 0, body, revision=1)
            self.assertEqual(vm.heap_bytes(204, 1), bytes(1), (index, value))
        for kind in ("span_zero", "bar_zero", "position_at_end", "first_after_bar"):
            vm = ready("main")
            body = clock_payload(0)
            if kind == "span_zero": body[2:5] = [0, 0, 0]
            if kind == "bar_zero": body[11:14] = [0, 0, 0]
            if kind == "position_at_end": body[5:8] = body[2:5]
            if kind == "first_after_bar": body[8:11] = [1, 3, 0]
            clock(vm, 0, body, revision=1)
            self.assertEqual(vm.heap_bytes(204, 1), bytes(1), kind)

    def test_committed_clock_and_ui_replays_cannot_change_the_model(self):
        vm = ready("main")
        old_clock = vm.heap_bytes(332, 16)
        clock(vm, revision=0, position=2.5)
        self.assertEqual(vm.heap_bytes(332, 16), old_clock)
        presentation(vm, mode=2, flags=2, revision=0)
        touch(vm, 1)
        self.assertEqual((frames(vm)[-1]["role"], frames(vm)[-1]["view"]), (6, 0))
        self.assertEqual(vm.heap_bytes(453, 1), bytes([1]))

    def test_main_bottom_and_gutters_are_blocked_with_no_tap_or_editor(self):
        vm = ready("main")
        for x, y in ((7.5, 14.5), (2, 13.5), (7.5, 7.5)):
            touch(vm, 1, x=x, y=y)
            touch(vm, 2, x=x, y=1.5, milliseconds=50)
            touch(vm, 3, x=x, y=1.5)
            self.assertEqual([event["role"] for event in frames(vm)[-3:]], [5, 5, 5])
        self.assertTrue(all(not event["opened"] for event in frames(vm)))

    def test_main_group_and_mixer_record_context_match_the_display(self):
        vm = ready("main")
        touch(vm, 1, finger=15)
        touch(vm, 3, finger=15)
        self.assertEqual(frames(vm)[-1]["role"], 6)
        presentation(vm, mode=1, selected=7, revision=1)
        touch(vm, 1, finger=1, x=1.5, y=12.5)
        self.assertEqual((frames(vm)[-1]["role"], frames(vm)[-1]["view"], frames(vm)[-1]["group"]), (0, 1, 7))
        touch(vm, 3, finger=1, x=1.5, y=12.5)
        presentation(vm, mode=2, selected=7, flags=2, revision=2)
        touch(vm, 1, finger=1, x=9.5, y=2.5)
        event = frames(vm)[-1]
        self.assertEqual((event["role"], event["view"], event["group"], event["extra"]), (0, 2, 7, True))

    def test_modal_buttons_digits_and_central_gap_have_captured_roles(self):
        for x, y, role in ((1.5, 1.5, 2), (7.5, .5, 5), (2, 2, 2), (9, 9.5, 3),
                            (2, 12.5, 4), (12, 12.5, 1), (7.5, 12.5, 5), (2, 10.5, 4),
                            (7.5, 5.5, 5), (14.5, 10, 1), (6.99, 9.99, 2)):
            vm = ready("metronome")
            touch(vm, 1, x=x, y=y)
            event = frames(vm)[-1]
            self.assertEqual((event["role"], event["opened"], event["extra"]), (role, True, False), (x, y))

    def test_modal_buttons_do_not_close_or_cancel_other_fields(self):
        vm = ready("metronome")
        for finger, x, y in ((1, 2, 6), (2, 9, 6), (3, 2, 12.5), (4, 12, 12.5)):
            touch(vm, 1, finger, x, y)
        for finger, x, y, role in ((3, 2, 12.5, 4), (4, 12, 12.5, 1),
                                    (1, 2, 6, 2), (2, 9, 6, 3)):
            touch(vm, 3, finger, x, y)
            event = frames(vm)[-1]
            self.assertEqual((event["role"], event["kind"], event["opened"]), (role, 3, True))

    def test_modal_captures_button_role_across_crossing_and_never_emits_close(self):
        for start_x, end_x, role in ((2, 2, 4), (2, 12, 4), (12, 2, 1), (7.5, 2, 5)):
            vm = ready("metronome")
            touch(vm, 1, x=start_x, y=12.5)
            touch(vm, 2, x=8, y=5, milliseconds=50)
            touch(vm, 3, x=end_x, y=12.5)
            event = frames(vm)[-1]
            self.assertEqual((event["role"], event["opened"]), (role, True))

    def test_modal_nonoverview_and_expired_ui_never_remain_editable(self):
        for mode in (1, 2):
            vm = ready("metronome")
            touch(vm, 1, x=12, y=13.5)
            presentation(vm, mode=mode, revision=1)
            before = len(frames(vm))
            touch(vm, 3, x=12, y=13.5)
            self.assertEqual(len(frames(vm)), before)
            vm.call("repaint/v")
            self.assertTrue(all(colour == 0 for row in vm.leds for colour in row))
        vm = ready("metronome")
        touch(vm, 1, x=12, y=13.5)
        vm.time_ms += 3000
        vm.call("repaint/v")
        before = len(frames(vm))
        touch(vm, 3, x=12, y=13.5)
        self.assertEqual(len(frames(vm)), before)

    def test_ready_markers_wait_for_confirmed_models_and_are_one_shot(self):
        for name, tag in (("main", 34), ("metronome", 35)):
            vm = new(name)
            vm.call("repaint/v")
            self.assertEqual(markers(vm), [])
            presentation(vm)
            vm.call("repaint/v")
            self.assertEqual(markers(vm), [])
            clock(vm)
            if name == "main":
                for group in range(12):
                    if group != 7: snapshot(vm, group)
                vm.call("repaint/v")
                self.assertEqual(markers(vm), [])
                snapshot(vm, 7)
            vm.call("repaint/v")
            self.assertEqual(markers(vm), [[191, 108, tag]])
            for _ in range(3): vm.call("repaint/v")
            presentation(vm, revision=1)
            vm.call("repaint/v")
            self.assertEqual(markers(vm), [[191, 108, tag]])

    def test_led_regression_fixtures_cover_overview_mixer_record_and_modal(self):
        self.assertGreaterEqual(len(CONTRACT["led_cases"]), 6)
        for case in CONTRACT["led_cases"]:
            with self.subTest(scene=case["name"]):
                vm = render_case(case)
                actual = [" ".join(f"{pixel:06x}" for pixel in row) for row in vm.leds]
                self.assertEqual(actual, case["pixels"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-binary", type=Path)
    parser.add_argument("--metronome-binary", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    if bool(args.main_binary) != bool(args.metronome_binary):
        parser.error("supply both binary paths, or neither")
    if args.main_binary:
        PROGRAMS.update(main=args.main_binary.read_bytes(), metronome=args.metronome_binary.read_bytes())
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CurrentDeviceTests))
    data = dict(scope="Actual canonical bytecode in independent Python VM/fake natives; no hardware or optical proof",
                tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                programs={name: hashlib.sha256(value).hexdigest() for name, value in PROGRAMS.items()},
                instances=len(VMS), callbacks=sum(vm.calls for vm in VMS),
                instructions=sum(vm.executed_instructions for vm in VMS),
                max_callback=max((vm.max_instructions for vm in VMS), default=0),
                max_stack=max((vm.max_stack_bytes for vm in VMS), default=0))
    if args.result: args.result.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2))
    return int(not result.wasSuccessful())


if __name__ == "__main__":
    raise SystemExit(main())
