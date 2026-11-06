#!/usr/bin/env python3
"""Measure pad CC20 -> Live feedback -> pad CC21 without physical taps.

Requires the temporary trigger-probe program and exactly one Live feedback writer.
Only channel-16 CC22=0/127 is sent, to the explicitly selected Lightpad destination.
CC20 is never echoed by this process. The trigger leg is excluded from timing.
Do not touch the pad or change the target in Live during a run. CC21 and CC22
must remain unmapped. A successful run verifies message pairing, not Live's
actual target selection, audio timing, touch acquisition or LED repaint timing.
"""

import argparse
import ctypes as C
import math
import queue
import statistics
import threading
import time

from direct_lightpad_echo import DirectMIDI, MIDIStream, emit, validate_pair
from passive_lightpad_midi import READ_PROC, check, packets


class Measurement:
    """Strict one-trial pairing; injected sender/clock allow offline checks."""

    def __init__(self, sender, timeout=2.0, clock=time.monotonic_ns):
        self.sender, self.clock = sender, clock
        self.timeout_ns = int(timeout * 1e9)
        self.lock = threading.RLock()
        self.events = queue.SimpleQueue()
        self.pending = None
        self.triggers = self.requests = self.acks = 0
        self.missing_requests = self.missing_acks = 0
        self.unexpected_requests = self.unexpected_acks = self.duplicates = 0
        self.pairs = []
        self.fatal = None

    def issue(self, value):
        with self.lock:
            if self.fatal:
                return
            if self.pending is not None:
                self.fail("A trial is already outstanding")
                return
            self.triggers += 1
            pending = {"sample": self.triggers, "value": value,
                       "issued_ns": self.clock()}
            self.pending = pending
        try:
            start, end = self.sender(value)
        except Exception as error:
            self.fail(str(error))
            return
        with self.lock:
            pending.update(trigger_send_start_ns=start, trigger_send_end_ns=end)
            self.events.put({"type": "trigger", **pending})
            self._complete()

    def receive(self, message, arrival_ns):
        if len(message) != 3 or message[0] != 0xBF or message[1] not in (20, 21):
            return
        _, controller, value = message
        with self.lock:
            pending = self.pending
            event = {"value": value, "arrival_monotonic_ns": arrival_ns}
            if controller == 20:
                self.requests += 1
                if pending is None or pending["value"] != value or "request_ns" in pending:
                    self.unexpected_requests += 1
                    if pending is not None and "request_ns" in pending:
                        self.duplicates += 1
                    self.events.put({"type": "unexpected_request", **event})
                    self.fail("Unsolicited, mismatched or duplicate CC20 request")
                    return
                pending["request_ns"] = arrival_ns
                self.events.put({"type": "request", "sample": pending["sample"], **event})
                return
            self.acks += 1
            if (pending is None or pending["value"] != value
                    or "request_ns" not in pending or "ack_ns" in pending):
                self.unexpected_acks += 1
                if pending is None and self.pairs and self.pairs[-1]["value"] == value:
                    self.duplicates += 1
                elif pending is not None and "ack_ns" in pending:
                    self.duplicates += 1
                self.events.put({"type": "unexpected_ack", **event})
                self.fail("Unsolicited, mismatched, early or duplicate CC21 acknowledgment")
                return
            if arrival_ns < pending["request_ns"]:
                self.unexpected_acks += 1
                self.fail("Acknowledgment timestamp precedes request")
                return
            pending["ack_ns"] = arrival_ns
            self._complete()

    def _complete(self):
        pending = self.pending
        if (self.fatal or pending is None or "ack_ns" not in pending
                or "trigger_send_end_ns" not in pending):
            return
        pair = {"type": "pair", **pending,
                "round_trip_ms": (pending["ack_ns"] - pending["request_ns"]) / 1e6,
                "trigger_leg_ms": (pending["request_ns"] - pending["trigger_send_start_ns"]) / 1e6}
        self.pairs.append(pair)
        self.events.put(pair)
        self.pending = None

    def expire(self, reason="timeout", force=False):
        with self.lock:
            pending = self.pending
            if pending is None or (not force and self.clock() - pending["issued_ns"] < self.timeout_ns):
                return
            stage = "request" if "request_ns" not in pending else "acknowledgment"
            if stage == "request":
                self.missing_requests += 1
            elif "ack_ns" not in pending:
                self.missing_acks += 1
            self.events.put({"type": "miss", "stage": stage, "reason": reason, **pending})
            self.pending = None
            if not force:
                self.fail(f"Timed out waiting for pad {stage}")

    def fail(self, error):
        with self.lock:
            if self.fatal is None:
                self.fatal = str(error)
                self.events.put({"type": "error", "error": self.fatal})

    def snapshot(self):
        with self.lock:
            return self.triggers, len(self.pairs), self.pending is not None, self.fatal

    def summary(self, reason, expected):
        with self.lock:
            values = sorted(pair["round_trip_ms"] for pair in self.pairs)
            complete = (reason == "samples_complete" and self.triggers == expected
                        and len(values) == expected and self.requests == expected
                        and self.acks == expected and self.pending is None
                        and not self.fatal and not self.missing_requests and not self.missing_acks
                        and not self.unexpected_requests and not self.unexpected_acks)
            return {"type": "summary", "status": "pass" if complete else "fail",
                    "reason": reason, "requested_samples": expected,
                    "triggers": self.triggers, "requests": self.requests,
                    "acknowledgments": self.acks, "matched": len(values),
                    "missing_requests": self.missing_requests, "missing_acks": self.missing_acks,
                    "unexpected_requests": self.unexpected_requests,
                    "unexpected_acks": self.unexpected_acks, "duplicates": self.duplicates,
                    "contaminated": bool(self.unexpected_requests or self.unexpected_acks),
                    "round_trip_ms": {"min": min(values), "median": statistics.median(values),
                                      "p95": values[math.ceil(len(values) * 0.95) - 1],
                                      "max": max(values)} if values else None,
                    "error": self.fatal, "exit_status": 0 if complete else 1}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List current Lightpad endpoints; send nothing")
    parser.add_argument("--source", type=int)
    parser.add_argument("--destination", type=int)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.5, help="Quiet seconds after each acknowledgment")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--seconds", type=float, default=30.0, help="Hard run deadline")
    parser.add_argument("--first-value", type=int, choices=(0, 127), default=0,
                        help="First desired activator value; choose opposite the known Live state")
    args = parser.parse_args(argv)
    if not args.list and (not args.source or not args.destination or args.source < 0 or args.destination < 0):
        parser.error("Explicit positive --source and --destination endpoint IDs are required")
    if not 1 <= args.samples <= 1000:
        parser.error("--samples must be between 1 and 1000")
    for key, lower, upper in [("interval", 0.5, 60), ("timeout", 0.1, 10), ("seconds", 0.1, 120)]:
        value = getattr(args, key)
        if not math.isfinite(value) or not lower <= value <= upper:
            parser.error(f"--{key} must be finite and between {lower} and {upper}")
    return args


def main(argv=None):
    args = arguments(argv)
    midi = DirectMIDI()
    sources, destinations = midi.endpoints(), midi.endpoints(True)
    if args.list:
        emit({"type": "endpoints", "sources": sources, "destinations": destinations})
        return 0
    source = next((item for item in sources if item["endpoint"] == args.source), None)
    destination = next((item for item in destinations if item["endpoint"] == args.destination), None)
    if source is None or destination is None:
        raise ValueError("Choose current Lightpad endpoint IDs from --list")
    validate_pair(source, destination)
    incoming, outgoing = C.c_uint32(), C.c_uint32()
    probe = Measurement(lambda value: midi.send_cc(outgoing.value, args.destination, 22, value), args.timeout)
    decoder = MIDIStream()

    @READ_PROC
    def receive(address, _context, _connection):
        arrival = time.monotonic_ns()
        try:
            for _ticks, raw in packets(address):
                for message in decoder.feed(raw):
                    probe.receive(message, arrival)
        except Exception as error:
            probe.fail(error)

    connected = False
    reason = "deadline"
    try:
        for name, fn, output in [("Live feedback measurement input", midi.midi.MIDIInputPortCreate, incoming),
                                 ("Trigger CC22 only", midi.midi.MIDIOutputPortCreate, outgoing)]:
            text = midi.string(name)
            try:
                call_args = (midi.client, text, receive, None, C.byref(output)) if output is incoming else (midi.client, text, C.byref(output))
                check(fn(*call_args), name)
            finally:
                midi.cf.CFRelease(text)
        check(midi.midi.MIDIPortConnectSource(incoming.value, args.source, None), "MIDIPortConnectSource")
        connected = True
        emit({"type": "started", "mode": "live_feedback", "source": source,
              "destination": destination, "samples": args.samples, "interval": args.interval,
              "timeout": args.timeout, "seconds": args.seconds, "first_value": args.first_value,
              "timing_definition": "host arrival of pad CC20 to matching pad CC21; CC22 trigger leg excluded",
              "limits": "One Live feedback writer; no physical touches; CC21/22 unmapped; no audio or LED timing"})
        deadline = time.monotonic() + args.seconds
        next_trigger = time.monotonic() + args.interval
        completed = 0
        while time.monotonic() < deadline:
            probe.expire()
            sent, matched, pending, fatal = probe.snapshot()
            if fatal:
                reason = "error"
                break
            if matched != completed:
                completed = matched
                next_trigger = time.monotonic() + args.interval
            if not pending and time.monotonic() >= next_trigger:
                if sent >= args.samples:
                    reason = "samples_complete"
                    break
                value = args.first_value if sent % 2 == 0 else 127 - args.first_value
                probe.issue(value)
            while not probe.events.empty():
                emit(probe.events.get())
            time.sleep(0.005)
    except KeyboardInterrupt:
        reason = "interrupted"
    finally:
        if connected:
            midi.midi.MIDIPortDisconnectSource(incoming.value, args.source)
        for port in (incoming, outgoing):
            if port.value:
                midi.midi.MIDIPortDispose(port.value)
        probe.expire(reason, force=True)
        while not probe.events.empty():
            emit(probe.events.get())
        summary = probe.summary(reason, args.samples)
        emit(summary)
    return summary["exit_status"]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error))
