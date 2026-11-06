#!/usr/bin/env python3
"""List or passively monitor existing Lightpad MIDI sources on macOS.

Uses only the Python standard library and Apple frameworks. There are no MIDI
send, output-port, device-configuration, or program-upload API bindings.

    python3 passive_lightpad_midi.py --list
    python3 passive_lightpad_midi.py --listen --endpoint 123 --seconds 60 > take.jsonl

Packet layout and API signatures follow the local Apple SDK MIDIServices.h:
  /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/System/Library/Frameworks/
  CoreMIDI.framework/Versions/A/Headers/MIDIServices.h
MIDIPacket/List use #pragma pack(push, 4). MIDIPacketNext aligns to four bytes
on ARM only. Packets contain variable-length data, not a fixed-stride array.
"""

import argparse
import ctypes as C
import json
import platform
import queue
import sys
import time


class Packet(C.Structure):
    _layout_ = "ms"  # ctypes supports explicit packing through this layout.
    _pack_ = 4
    _fields_ = [("timestamp", C.c_uint64), ("length", C.c_uint16),
                ("data", C.c_ubyte * 256)]


class PacketList(C.Structure):
    _layout_ = "ms"
    _pack_ = 4
    _fields_ = [("count", C.c_uint32), ("packet", Packet * 1)]


ARM = platform.machine().lower() in ("arm64", "aarch64", "arm")
READ_PROC = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_void_p)
assert Packet.data.offset == 10 and PacketList.packet.offset == 4


def packets(address, arm=ARM):
    """Copy packet bytes while the CoreMIDI callback's memory is valid."""
    count = C.c_uint32.from_address(address).value
    address += PacketList.packet.offset
    for _ in range(count):
        packet = Packet.from_address(address)
        size = packet.length
        yield packet.timestamp, C.string_at(address + Packet.data.offset, size)
        address += Packet.data.offset + size
        if arm:
            address = (address + 3) & ~3


def emit(record):
    print(json.dumps(record, separators=(",", ":")), flush=True)


def check(status, operation):
    if status:
        raise RuntimeError(f"{operation}: OSStatus {status}")


class CoreMIDI:
    def __init__(self):
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.midi = C.CDLL("/System/Library/Frameworks/CoreMIDI.framework/CoreMIDI")
        signatures = {
            "MIDIClientCreate": ([C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_uint32)], C.c_int32),
            "MIDIGetNumberOfSources": ([], C.c_ulong),
            "MIDIGetSource": ([C.c_ulong], C.c_uint32),
            "MIDIObjectGetStringProperty": ([C.c_uint32, C.c_void_p, C.POINTER(C.c_void_p)], C.c_int32),
            "MIDIEndpointGetEntity": ([C.c_uint32, C.POINTER(C.c_uint32)], C.c_int32),
            "MIDIEntityGetDevice": ([C.c_uint32, C.POINTER(C.c_uint32)], C.c_int32),
            "MIDIInputPortCreate": ([C.c_uint32, C.c_void_p, READ_PROC, C.c_void_p, C.POINTER(C.c_uint32)], C.c_int32),
            "MIDIPortConnectSource": ([C.c_uint32, C.c_uint32, C.c_void_p], C.c_int32),
            "MIDIPortDisconnectSource": ([C.c_uint32, C.c_uint32], C.c_int32),
            "MIDIPortDispose": ([C.c_uint32], C.c_int32),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.midi, name)
            fn.argtypes, fn.restype = args, result
        self.cf.CFStringCreateWithCString.argtypes = [C.c_void_p, C.c_char_p, C.c_uint32]
        self.cf.CFStringCreateWithCString.restype = C.c_void_p
        self.cf.CFStringGetLength.argtypes = [C.c_void_p]
        self.cf.CFStringGetLength.restype = C.c_long
        self.cf.CFStringGetMaximumSizeForEncoding.argtypes = [C.c_long, C.c_uint32]
        self.cf.CFStringGetMaximumSizeForEncoding.restype = C.c_long
        self.cf.CFStringGetCString.argtypes = [C.c_void_p, C.c_char_p, C.c_long, C.c_uint32]
        self.cf.CFStringGetCString.restype = C.c_ubyte
        self.cf.CFRelease.argtypes = [C.c_void_p]
        self.cf.CFRelease.restype = None
        self.client = C.c_uint32()
        name = self.string("Passive Lightpad verification")
        try:
            check(self.midi.MIDIClientCreate(name, None, None, C.byref(self.client)), "MIDIClientCreate")
        finally:
            self.cf.CFRelease(name)

    def string(self, value):
        return self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)

    def property(self, obj, name):
        key = C.c_void_p.in_dll(self.midi, name).value
        result = C.c_void_p()
        if self.midi.MIDIObjectGetStringProperty(obj, key, C.byref(result)) or not result.value:
            return ""
        try:
            length = self.cf.CFStringGetMaximumSizeForEncoding(self.cf.CFStringGetLength(result), 0x08000100) + 1
            buffer = C.create_string_buffer(length)
            if self.cf.CFStringGetCString(result, buffer, length, 0x08000100):
                return buffer.value.decode("utf-8")
            return ""
        finally:
            self.cf.CFRelease(result)

    def sources(self):
        found = []
        for index in range(self.midi.MIDIGetNumberOfSources()):
            endpoint = self.midi.MIDIGetSource(index)
            entity, device = C.c_uint32(), C.c_uint32()
            if not self.midi.MIDIEndpointGetEntity(endpoint, C.byref(entity)) and entity.value:
                self.midi.MIDIEntityGetDevice(entity, C.byref(device))
            info = {"endpoint": endpoint,
                    "name": self.property(endpoint, "kMIDIPropertyName"),
                    "display_name": self.property(endpoint, "kMIDIPropertyDisplayName"),
                    "device_name": self.property(device.value, "kMIDIPropertyName") if device.value else ""}
            if "lightpad" in " ".join(str(v) for v in info.values()).lower():
                found.append(info)
        return found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="List Lightpad sources; do not subscribe. Default.")
    mode.add_argument("--listen", action="store_true", help="Subscribe to one existing Lightpad MIDI source.")
    parser.add_argument("--endpoint", type=int, help="Exact endpoint ID returned by --list.")
    parser.add_argument("--seconds", type=float, default=60, help="Monitoring duration, default 60 seconds.")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    midi = CoreMIDI()
    sources = midi.sources()
    if not args.listen:
        emit({"type": "sources", "sources": sources})
        return
    candidates = [s for s in sources if args.endpoint is None or s["endpoint"] == args.endpoint]
    if len(candidates) != 1:
        parser.error("Choose exactly one listed Lightpad source with --endpoint; no subscription created.")
    source = candidates[0]
    events = queue.Queue(maxsize=8192)
    dropped = 0

    @READ_PROC
    def receive(address, _context, _connection):
        nonlocal dropped
        arrival = time.monotonic_ns()
        try:
            for timestamp, raw in packets(address):
                try:
                    events.put_nowait({"type": "packet", "endpoint": source["endpoint"],
                                       "arrival_monotonic_ns": arrival,
                                       "coremidi_timestamp_ticks": timestamp,
                                       "rawbytes": list(raw), "hex": raw.hex(" ")})
                except queue.Full:
                    dropped += 1
        except Exception as error:
            try:
                events.put_nowait({"type": "callback_error", "error": str(error)})
            except queue.Full:
                dropped += 1

    port = C.c_uint32()
    name = midi.string("Receive only")
    try:
        check(midi.midi.MIDIInputPortCreate(midi.client, name, receive, None, C.byref(port)), "MIDIInputPortCreate")
    finally:
        midi.cf.CFRelease(name)
    connected = False
    try:
        check(midi.midi.MIDIPortConnectSource(port.value, source["endpoint"], None), "MIDIPortConnectSource")
        connected = True
        emit({"type": "subscribed", "source": source, "seconds": args.seconds,
              "timestamp_unit": "CoreMIDI host clock ticks; raw, not milliseconds"})
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            try:
                emit(events.get(timeout=min(0.1, max(0.001, deadline - time.monotonic()))))
            except queue.Empty:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        if connected:
            midi.midi.MIDIPortDisconnectSource(port.value, source["endpoint"])
        midi.midi.MIDIPortDispose(port.value)
        while not events.empty():
            emit(events.get_nowait())
        emit({"type": "stopped", "dropped_packets": dropped})
        # Process termination owns client cleanup, per Apple's recommendation
        # against explicitly disposing an application's last CoreMIDI client.


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
