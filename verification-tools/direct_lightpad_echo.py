#!/usr/bin/env python3
"""Direct CC20/CC21 transport probe. Close Live feedback before running.

Echo forwards physical channel-16 CC20 requests unchanged. Ping sends alternating
values without touches. Both require the existing probe program, which replies
on CC21. No SDK, SysEx, upload or persistent-device operations are used.
The passive monitor remains receive-only. CoreMIDI signatures come from the
local Apple SDK MIDIServices.h; output timestamps are zero for immediate delivery.
"""

import argparse
import ctypes as C
import json
import math
import queue
import statistics
import threading
import time

from passive_lightpad_midi import CoreMIDI, READ_PROC, check, packets


class MIDIStream:
    """Decode channel messages across packets, including MIDI running status."""

    def __init__(self):
        self.status = None
        self.data = []
        self.sysex = False
        self.common_left = 0

    def feed(self, raw):
        messages = []
        for byte in raw:
            if byte >= 0xF8:  # Realtime may interrupt any message, including SysEx.
                continue
            if self.sysex:
                if byte < 0x80:
                    continue
                self.sysex = False  # F7 or another status terminates SysEx.
            if byte >= 0x80:
                self.data = []
                self.common_left = 0
                if byte >= 0xF0:
                    self.status = None  # System common cancels running status.
                    self.sysex = byte == 0xF0
                    self.common_left = {0xF1: 1, 0xF2: 2, 0xF3: 1}.get(byte, 0)
                else:
                    self.status = byte
                continue
            if self.common_left:
                self.common_left -= 1
                continue
            if self.status is None:
                continue
            self.data.append(byte)
            needed = 1 if self.status & 0xF0 in (0xC0, 0xD0) else 2
            if len(self.data) == needed:
                messages.append((self.status, *self.data))
                self.data = []
        return messages


def validate_pair(source, destination):
    """Reject another device, virtual destinations, and mixed USB/BLE routes."""
    name = source.get("device_name", "").strip().casefold()
    other = destination.get("device_name", "").strip().casefold()
    if not name or "lightpad" not in name or name != other:
        raise ValueError("Source and destination must belong to the same named Lightpad device")
    if not source.get("device_ref") or source["device_ref"] != destination.get("device_ref"):
        raise ValueError("Source and destination must have the same CoreMIDI owning device")


class DirectMIDI(CoreMIDI):
    def __init__(self):
        super().__init__()
        signatures = {
            "MIDIGetNumberOfDestinations": ([], C.c_ulong),
            "MIDIGetDestination": ([C.c_ulong], C.c_uint32),
            "MIDIOutputPortCreate": ([C.c_uint32, C.c_void_p, C.POINTER(C.c_uint32)], C.c_int32),
            "MIDIPacketListInit": ([C.c_void_p], C.c_void_p),
            "MIDIPacketListAdd": ([C.c_void_p, C.c_ulong, C.c_void_p, C.c_uint64,
                                   C.c_ulong, C.POINTER(C.c_ubyte)], C.c_void_p),
            "MIDISend": ([C.c_uint32, C.c_uint32, C.c_void_p], C.c_int32),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.midi, name)
            fn.argtypes, fn.restype = args, result

    def endpoints(self, destinations=False):
        count = self.midi.MIDIGetNumberOfDestinations if destinations else self.midi.MIDIGetNumberOfSources
        get = self.midi.MIDIGetDestination if destinations else self.midi.MIDIGetSource
        found = []
        for index in range(count()):
            endpoint = get(index)
            entity, device = C.c_uint32(), C.c_uint32()
            if not self.midi.MIDIEndpointGetEntity(endpoint, C.byref(entity)) and entity.value:
                self.midi.MIDIEntityGetDevice(entity.value, C.byref(device))
            info = {"endpoint": endpoint, "name": self.property(endpoint, "kMIDIPropertyName"),
                    "display_name": self.property(endpoint, "kMIDIPropertyDisplayName"),
                    "device_name": self.property(device.value, "kMIDIPropertyName") if device.value else "",
                    "device_ref": device.value}
            if "lightpad" in " ".join(str(v) for v in info.values()).casefold():
                found.append(info)
        return found

    def send(self, port, destination, value):
        return self.send_cc(port, destination, 20, value)

    def send_cc(self, port, destination, controller, value):
        if controller not in (20, 22) or value not in (0, 127):
            raise ValueError("Only channel-16 probe CC20/CC22 values 0 or 127 are allowed")
        buffer = C.create_string_buffer(1024)
        packet = self.midi.MIDIPacketListInit(buffer)
        data = (C.c_ubyte * 3)(0xBF, controller, value)
        if not self.midi.MIDIPacketListAdd(buffer, len(buffer), packet, 0, len(data), data):
            raise RuntimeError("Cannot build MIDI packet")
        start = time.monotonic_ns()
        status = self.midi.MIDISend(port, destination, buffer)
        end = time.monotonic_ns()
        check(status, "MIDISend")
        return start, end


class Probe:
    """One outstanding request. Sender and clock are injectable for offline checks."""

    def __init__(self, mode, sender, timeout, clock=time.monotonic_ns):
        self.mode, self.sender, self.clock = mode, sender, clock
        self.timeout_ns = int(timeout * 1e9)
        self.events = queue.SimpleQueue()
        self.lock = threading.RLock()
        self.pending = None
        self.sent = self.missed = self.extra_acks = self.busy_requests = 0
        self.physical_requests_during_ping = 0
        self.pairs = []
        self.fatal = None

    def issue(self, value, request_ns=None):
        with self.lock:
            if self.pending is not None:
                self.busy_requests += 1
                self.events.put({"type": "busy_request", "value": value, "request_ns": request_ns})
                return
            self.sent += 1
            pending = {"sample": self.sent, "value": value, "request_ns": request_ns,
                       "issued_ns": self.clock()}
            self.pending = pending
        # No output I/O or sleep precedes this send. ACK may arrive while MIDISend
        # releases the GIL; retain it until the send timestamps become available.
        try:
            start, end = self.sender(value)
        except Exception as error:
            self.fail(error)
            return
        with self.lock:
            pending.update(send_start_ns=start, send_end_ns=end)
            self.events.put({"type": "send", **pending})
            self._complete()

    def receive(self, message, arrival_ns):
        if len(message) != 3 or message[0] != 0xBF or message[1] not in (20, 21):
            return
        _, controller, value = message
        if controller == 20:
            if self.mode == "echo" and value in (0, 127):
                self.issue(value, arrival_ns)
            else:
                with self.lock:
                    if self.mode == "ping":
                        self.physical_requests_during_ping += 1
                    self.events.put({"type": "unsolicited_request", "value": value,
                                     "arrival_monotonic_ns": arrival_ns})
            return
        with self.lock:
            p = self.pending
            if p is None or p["value"] != value or "ack_ns" in p:
                self.extra_acks += 1
                self.events.put({"type": "extra_ack", "value": value,
                                 "arrival_monotonic_ns": arrival_ns})
                return
            p["ack_ns"] = arrival_ns
            self._complete()

    def _complete(self):
        p = self.pending
        if p is None or "ack_ns" not in p or "send_end_ns" not in p:
            return
        base = p["request_ns"] if self.mode == "echo" else p["send_start_ns"]
        pair = {"type": "pair", **p, "round_trip_ms": (p["ack_ns"] - base) / 1e6,
                "send_call_ms": (p["send_end_ns"] - p["send_start_ns"]) / 1e6,
                "host_dispatch_ms": ((p["send_start_ns"] - p["request_ns"]) / 1e6
                                     if self.mode == "echo" else None)}
        self.pairs.append(pair)
        self.events.put(pair)
        self.pending = None

    def expire(self, reason="timeout", force=False):
        with self.lock:
            p = self.pending
            if p is not None and (force or self.clock() - p["issued_ns"] >= self.timeout_ns):
                self.missed += 1
                self.events.put({"type": "miss", "reason": reason, **p})
                self.pending = None

    def fail(self, error):
        with self.lock:
            self.fatal = str(error)
            self.events.put({"type": "error", "error": self.fatal})

    def summary(self, reason):
        def stats(values):
            return {"min": min(values), "median": statistics.median(values), "max": max(values)} if values else None
        with self.lock:
            return {"type": "summary", "mode": self.mode, "reason": reason,
                    "requests": self.sent, "matched": len(self.pairs), "misses": self.missed,
                    "extra_acks": self.extra_acks, "busy_requests": self.busy_requests,
                    "physical_requests_during_ping": self.physical_requests_during_ping,
                    "contaminated": bool(self.physical_requests_during_ping or self.busy_requests or self.extra_acks),
                    "round_trip_ms": stats([p["round_trip_ms"] for p in self.pairs]),
                    "host_dispatch_ms": stats([p["host_dispatch_ms"] for p in self.pairs if p["host_dispatch_ms"] is not None]),
                    "send_call_ms": stats([p["send_call_ms"] for p in self.pairs]), "error": self.fatal}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="List endpoints without sending")
    mode.add_argument("--mode", choices=("echo", "ping"))
    parser.add_argument("--source", type=int)
    parser.add_argument("--destination", type=int)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    if not args.list and (not args.source or not args.destination or args.source < 0 or args.destination < 0):
        parser.error("--mode requires explicit positive --source and --destination endpoint IDs")
    if not 1 <= args.samples <= 10000:
        parser.error("--samples must be between 1 and 10000")
    for key, lower, upper in [("interval", 0.5, 60), ("timeout", 0.01, 60), ("seconds", 0.01, 3600)]:
        value = getattr(args, key)
        if not math.isfinite(value) or not lower <= value <= upper:
            parser.error(f"--{key} must be finite and between {lower} and {upper}")
    return args


def emit(event):
    print(json.dumps(event, separators=(",", ":")), flush=True)


def result_code(summary, expected):
    complete = (summary["reason"] == "samples_complete"
                and summary["requests"] == expected and summary["matched"] == expected
                and summary["misses"] == 0 and not summary["contaminated"]
                and summary["error"] is None)
    return 0 if complete else 1


def main(argv=None):
    args = arguments(argv)  # Reject bad commands before creating any MIDI client.
    midi = DirectMIDI()
    sources, destinations = midi.endpoints(), midi.endpoints(True)
    if args.list:
        emit({"type": "endpoints", "sources": sources, "destinations": destinations})
        return 0
    source = next((s for s in sources if s["endpoint"] == args.source), None)
    destination = next((d for d in destinations if d["endpoint"] == args.destination), None)
    if source is None or destination is None:
        raise ValueError("Choose current Lightpad endpoint IDs from --list")
    validate_pair(source, destination)
    incoming, outgoing = C.c_uint32(), C.c_uint32()
    probe = Probe(args.mode, lambda value: midi.send(outgoing.value, args.destination, value), args.timeout)
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
        for name, fn, output in [("Direct probe input", midi.midi.MIDIInputPortCreate, incoming),
                                 ("Direct probe output", midi.midi.MIDIOutputPortCreate, outgoing)]:
            text = midi.string(name)
            try:
                call_args = (midi.client, text, receive, None, C.byref(output)) if output is incoming else (midi.client, text, C.byref(output))
                check(fn(*call_args), name)
            finally:
                midi.cf.CFRelease(text)
        emit({"type": "started", "mode": args.mode, "source": source, "destination": destination,
              "samples": args.samples, "interval": args.interval, "timeout": args.timeout,
              "timing_definition": "request arrival to device ACK arrival" if args.mode == "echo" else "host send start to device ACK arrival",
              "limits": "No touch acquisition or LED repaint timing; close Live feedback; CC21 must remain unmapped"})
        check(midi.midi.MIDIPortConnectSource(incoming.value, args.source, None), "MIDIPortConnectSource")
        connected = True
        deadline = time.monotonic() + args.seconds
        next_ping = 0.0
        while time.monotonic() < deadline:
            probe.expire()
            if probe.fatal:
                reason = "error"
                break
            if probe.sent >= args.samples and probe.pending is None:
                reason = "samples_complete"
                break
            if args.mode == "ping" and probe.pending is None and probe.sent < args.samples and time.monotonic() >= next_ping:
                probe.issue(127 if probe.sent % 2 == 0 else 0)
                next_ping = time.monotonic() + args.interval
            while not probe.events.empty():
                emit(probe.events.get())
            time.sleep(0.005)  # Main-thread logging/ping scheduling only, never the receive callback.
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
        summary = probe.summary(reason)
        summary["requested_samples"] = args.samples
        summary["exit_status"] = result_code(summary, args.samples)
        emit(summary)
        # Keep receive and probe alive through port disposal. Client cleanup is
        # left to process termination, matching the passive monitor.
    return summary["exit_status"]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error))
