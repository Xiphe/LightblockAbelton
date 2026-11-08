#!/usr/bin/env python3
"""Offline callback harness for actual blocksctl-compiled Littlefoot bytecode.

This is an independent Python transcription of the checked-in ROLI SDK Runner,
not the native Runner and not a model of the controller's state machine. It
executes program instructions with 32-bit integers/floats and checked memory.
Native LED, time and MIDI functions are deterministic fakes; no device APIs,
ports, discovery, uploads or installs are used. Unsupported natives fail closed.

The Apple compiler currently requires an unaccepted Xcode license, and the
installed blocksctl exposes compilation but no execution command. This bounded
alternative can check callback/parser/render behavior while native execution
remains unavailable. It cannot establish firmware timing, sensor behavior,
native color rounding or equivalence to the device VM. Report results as Python
bytecode execution, not native/hardware verification.

SDK reference: blocksctl/vendor/sdk/roli_blocks_basics/littlefoot/
roli_LittleFootRunner.h, Copyright 2020 ROLI Ltd, ISC license. The opcode order,
function ID, stack convention, program checksum and layout follow that source.

CLI reads JSONL commands from stdin: {"call":"initialise/v"},
{"time_ms":1000}, {"call":"handleMIDI/viii","args":[191,110,3]},
{"call":"repaint/v","capture":true}. Each command returns callback statistics,
new fake MIDI/log output and optionally the 15x15 RGB buffer. No automatic
initialise call is made. Python clients can import Harness directly.
"""

# Portions transcribed from the BLOCKS SDK LittleFoot Runner:
# Copyright (c) 2020 - ROLI Ltd
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND ROLI LTD DISCLAIMS ALL WARRANTIES WITH
# REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS. IN NO EVENT SHALL ROLI LTD BE LIABLE FOR ANY SPECIAL, DIRECT,
# INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
# LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE
# OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
# PERFORMANCE OF THIS SOFTWARE.

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys


# Positional instruction numbers are an ABI; do not sort this list.
OPCODES = """halt jump jumpIfTrue jumpIfFalse call retVoid retValue callNative
drop dropMultiple pushMultiple0 push0 push1 push8 push16 push32 dup
dupOffset_01 dupOffset_02 dupOffset_03 dupOffset_04 dupOffset_05 dupOffset_06 dupOffset_07
dupOffset dupOffset16 dropToStack dropToStack16 dupFromGlobal dropToGlobal
int32ToFloat floatToInt32 add_int32 add_float mul_int32 mul_float sub_int32 sub_float
div_int32 div_float mod_int32 bitwiseOr bitwiseAnd bitwiseXor bitwiseNot bitShiftLeft
bitShiftRight logicalOr logicalAnd logicalNot testZE_int32 testNZ_int32 testGT_int32
testGE_int32 testLT_int32 testLE_int32 testZE_float testNZ_float testGT_float testGE_float
testLT_float testLE_float getHeapByte getHeapInt getHeapBits setHeapByte setHeapInt""".split()
IMMEDIATE_8 = {"retVoid", "retValue", "dropMultiple", "pushMultiple0", "push8",
               "dupOffset", "dropToStack"}
IMMEDIATE_16 = {"jump", "jumpIfTrue", "jumpIfFalse", "call", "callNative", "push16",
                "dupOffset16", "dropToStack16", "dupFromGlobal", "dropToGlobal"}


class VMError(RuntimeError):
    pass


def i32(value):
    value = int(value) & 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


def float_bits(value):
    try:
        return struct.unpack("<i", struct.pack("<f", value))[0]
    except OverflowError:
        return struct.unpack("<i", struct.pack("<f", math.copysign(math.inf, value)))[0]


def bits_float(value):
    return struct.unpack("<f", struct.pack("<i", i32(value)))[0]


def function_id(signature):
    slash = signature.index("/")
    result = 0
    for index, char in enumerate(signature):
        if index != slash + 1:
            result = (result * 31 + ord(char)) & 0xFFFFFFFF
    result = (result + len(signature)) & 0xFFFF
    return result - 0x10000 if result & 0x8000 else result


class Harness:
    """One deterministic VM instance, retaining program globals/heap between calls."""

    def __init__(self, binary, program_heap_bytes=7200, stack_globals_bytes=800):
        code = Path(binary).read_bytes() if isinstance(binary, (str, Path)) else bytes(binary)
        if len(code) < 10:
            raise VMError("Incomplete program header")
        expected, size, functions, globals_, heap = struct.unpack_from("<5H", code)
        actual = size
        for byte in code[2:]:
            actual = (actual * 3 + byte) & 0xFFFF
        if size != len(code) or actual != expected or 10 + 4 * functions > size:
            raise VMError("Invalid program size, checksum or function table")
        if size + heap > program_heap_bytes:
            raise VMError("Program plus heap exceeds configured device budget")
        self.code_size, self.heap_size, self.num_globals = size, heap, globals_
        self.sha256 = hashlib.sha256(code).hexdigest()
        self.memory = bytearray((program_heap_bytes + stack_globals_bytes + 3) & ~3)
        self.memory[:size] = code
        self.heap_start = size
        self.heap_end = size + heap
        self.stack_start = self.heap_end
        self.stack_end = len(self.memory) - globals_ * 4
        if self.stack_start + 128 > self.stack_end:
            raise VMError("Insufficient stack space after program, heap and globals")
        self.functions = {}
        for index in range(functions):
            identifier, address = struct.unpack_from("<hH", code, 10 + index * 4)
            if address >= size:
                raise VMError("Invalid function address")
            self.functions[identifier] = address
        self.time_ms = 1000
        self.leds = [[0 for _ in range(15)] for _ in range(15)]
        self.midi = []
        self.logs = []
        self.overlays = []
        self.max_stack_bytes = 0
        self.max_instructions = 0
        self.executed_instructions = 0
        self.calls = 0
        self.natives = {}
        self._register_natives()

    def _register(self, signature, callback):
        self.natives[function_id(signature)] = (signature, callback)

    def _register_natives(self):
        self._register("getMillisecondCounter/i", lambda: self.time_ms)
        self._register("getTimeInCurrentFunctionCall/i", lambda: 0)
        for kind in ("i", "f"):
            self._register(f"abs/{kind}{kind}", abs)
            self._register(f"min/{kind}{kind}{kind}", min)
            self._register(f"max/{kind}{kind}{kind}", max)
            self._register(f"clamp/{kind}{kind}{kind}{kind}", lambda low, high, v: min(high, max(low, v)))
        self._register("makeARGB/iiiii", lambda a, r, g, b: i32((a & 255) << 24 | (r & 255) << 16 | (g & 255) << 8 | b & 255))
        self._register("blendARGB/iii", self.blend)
        self._register("fillPixel/viii", self.fill_pixel)
        self._register("blendPixel/viii", lambda c, x, y: self.fill_pixel(c, x, y, True))
        self._register("fillRect/viiiii", self.fill_rect)
        self._register("blendRect/viiiii", self.blend_rect)
        self._register("clearDisplay/v", lambda: self.fill_rect(0, 0, 0, 15, 15))
        self._register("clearDisplay/vi", lambda c: self.fill_rect(c, 0, 0, 15, 15))
        self._register("sendCC/viii", self.send_cc)
        self._register("sendMIDI/viii", lambda a, b, c: self.midi.append([a, b, c]))
        self._register("log/vi", lambda value: self.logs.append(value))
        self._register("logHex/vi", lambda value: self.logs.append(value))

    @staticmethod
    def blend(base, over):
        # A deterministic conventional alpha blend, not a firmware-rounding claim.
        alpha = (over >> 24) & 255
        channels = [(((base >> shift) & 255) * (255 - alpha)
                     + ((over >> shift) & 255) * alpha + 127) // 255
                    for shift in (16, 8, 0)]
        return i32(0xFF000000 | channels[0] << 16 | channels[1] << 8 | channels[2])

    def fill_pixel(self, colour, x, y, blend=False):
        if 0 <= x < 15 and 0 <= y < 15:
            self.leds[y][x] = (self.blend(self.leds[y][x], colour) if blend else colour) & 0xFFFFFF

    def fill_rect(self, colour, x, y, width, height, blend=False):
        for row in range(max(0, y), min(15, y + height)):
            for column in range(max(0, x), min(15, x + width)):
                self.fill_pixel(colour, column, row, blend)

    def blend_rect(self, colour, x, y, width, height):
        self.overlays.append({"time_ms": self.time_ms, "colour": colour & 0xFFFFFFFF,
                              "alpha": (colour >> 24) & 255,
                              "rect": [x, y, width, height]})
        self.fill_rect(colour, x, y, width, height, True)

    def send_cc(self, channel, controller, value):
        if not 0 <= channel <= 15 or not 0 <= controller <= 127 or not 0 <= value <= 127:
            raise VMError(f"Invalid fake MIDI CC arguments: {channel}, {controller}, {value}")
        self.midi.append([0xB0 | channel, controller, value])

    def heap_bytes(self, offset=0, size=None):
        size = self.heap_size - offset if size is None else size
        if offset < 0 or size < 0 or offset + size > self.heap_size:
            raise VMError("Heap inspection outside declared heap")
        return bytes(self.memory[self.heap_start + offset:self.heap_start + offset + size])

    def _word(self, offset):
        if offset < 0 or offset + 4 > len(self.memory):
            raise VMError("Word read outside VM memory")
        return struct.unpack_from("<i", self.memory, offset)[0]

    def _write(self, offset, value):
        if offset < 0 or offset + 4 > len(self.memory):
            raise VMError("Word write outside VM memory")
        struct.pack_into("<i", self.memory, offset, i32(value))

    def call(self, signature, *arguments, max_ops=1_000_000):
        if function_id(signature) not in self.functions:
            raise VMError(f"Unknown callback {signature}")
        types = signature.split("/", 1)[1]
        if len(arguments) != len(types) - 1:
            raise VMError(f"Wrong argument count for {signature}")
        sp = self.stack_end - 4
        self._write(sp, 0)
        tos = 0
        pc = self.functions[function_id(signature)]
        minimum_sp = sp
        ops = 0

        def check_stack():
            nonlocal minimum_sp
            minimum_sp = min(minimum_sp, sp)
            if sp < self.stack_start:
                raise VMError(f"Stack overflow in {signature}")
            if sp > self.stack_end:
                raise VMError(f"Stack underflow in {signature}")

        def flush():
            nonlocal sp
            sp -= 4
            check_stack()
            self._write(sp, tos)

        def push(value):
            nonlocal tos
            flush()
            tos = i32(value)

        def pop():
            nonlocal sp
            if sp >= self.stack_end:
                raise VMError(f"Stack underflow in {signature}")
            value = self._word(sp)
            sp += 4
            check_stack()
            return value

        def jump(address):
            nonlocal pc
            address &= 0xFFFF
            if address >= self.code_size:
                raise VMError("Jump outside program")
            pc = address

        if arguments:
            for kind, value in reversed(list(zip(types[1:], arguments))):
                push(float_bits(value) if kind == "f" else value)
            push(0)

        try:
            while ops < max_ops:
                if not 0 <= pc < self.code_size:
                    raise VMError("Instruction outside program")
                opnumber = self.memory[pc]
                pc += 1
                ops += 1
                if opnumber >= len(OPCODES):
                    raise VMError(f"Unknown instruction {opnumber}")
                op = OPCODES[opnumber]
                argument = None
                count = 1 if op in IMMEDIATE_8 else 2 if op in IMMEDIATE_16 else 4 if op == "push32" else 0
                if pc + count > self.code_size:
                    raise VMError("Truncated instruction operand")
                if count:
                    argument = int.from_bytes(self.memory[pc:pc + count], "little", signed=True)
                    pc += count
                if op == "halt":
                    break
                if op == "jump": jump(argument)
                elif op in ("jumpIfTrue", "jumpIfFalse"):
                    truth = bool(tos)
                    tos = pop()
                    if truth == (op == "jumpIfTrue"): jump(argument)
                elif op == "call":
                    flush()
                    tos = pc
                    jump(argument)
                elif op == "retVoid":
                    if tos == 0: break
                    address = tos
                    sp += (argument & 255) * 4
                    check_stack()
                    tos = pop()
                    jump(address)
                elif op == "retValue":
                    address = pop()
                    if address == 0: break
                    sp += (argument & 255) * 4
                    check_stack()
                    jump(address)
                elif op == "callNative":
                    if argument not in self.natives:
                        raise VMError(f"Unsupported native function ID {argument}")
                    native_signature, callback = self.natives[argument]
                    native_types = native_signature.split("/", 1)[1]
                    flush()
                    raw = [self._word(sp + 4 * i) for i in range(len(native_types) - 1)]
                    values = [bits_float(v) if kind == "f" else v for v, kind in zip(raw, native_types[1:])]
                    value = callback(*values)
                    tos = float_bits(value) if native_types[0] == "f" else i32(value or 0)
                    sp += 4 * len(raw)
                    check_stack()
                    if native_types[0] == "v": tos = pop()
                elif op == "drop": tos = pop()
                elif op == "dropMultiple":
                    if argument < 0:
                        sp -= argument * 4
                        check_stack()
                    else:
                        sp += (argument - 1) * 4
                        tos = pop()
                elif op == "pushMultiple0":
                    count = argument & 255
                    if sp - count * 4 <= self.stack_start: raise VMError("Stack overflow")
                    flush()
                    for _ in range(count - 1):
                        sp -= 4
                        self._write(sp, 0)
                    tos = 0
                    check_stack()
                elif op.startswith("push"): push(0 if op == "push0" else 1 if op == "push1" else argument)
                elif op == "dup": flush()
                elif op.startswith("dupOffset"):
                    offset = int(op[-2:]) if "_" in op else argument & (255 if op == "dupOffset" else 65535)
                    flush()
                    address = sp + offset * 4
                    if address < self.stack_start or address >= self.stack_end: raise VMError("Illegal stack access")
                    tos = self._word(address)
                elif op.startswith("dropToStack"):
                    offset = argument & (255 if op == "dropToStack" else 65535)
                    address = sp + offset * 4
                    if address < self.stack_start or address >= self.stack_end: raise VMError("Illegal stack access")
                    self._write(address, tos)
                    tos = pop()
                elif op in ("dupFromGlobal", "dropToGlobal"):
                    index = argument & 65535
                    if index >= self.num_globals: raise VMError("Illegal global access")
                    address = self.stack_end + index * 4
                    if op == "dupFromGlobal":
                        flush()
                        tos = self._word(address)
                    else:
                        self._write(address, tos)
                        tos = pop()
                elif op == "int32ToFloat": tos = float_bits(float(tos))
                elif op == "floatToInt32": tos = i32(int(bits_float(tos)))
                elif op in ("bitwiseNot", "logicalNot"):
                    tos = i32(~tos) if op == "bitwiseNot" else int(not tos)
                elif op.startswith("test"):
                    relation, kind = op[4:].split("_")
                    value = bits_float(tos) if kind == "float" else tos
                    tos = int({"ZE": value == 0, "NZ": value != 0, "GT": value > 0,
                               "GE": value >= 0, "LT": value < 0, "LE": value <= 0}[relation])
                elif op.startswith("getHeap"):
                    offset = tos & 0xFFFFFFFF
                    if op == "getHeapBits":
                        width = pop() & 0xFFFFFFFF
                        if offset + width > 8 * self.heap_size: raise VMError("Illegal heap bits access")
                        data = int.from_bytes(self.heap_bytes(), "little")
                        tos = i32((data >> offset) & ((1 << width) - 1))
                    elif op == "getHeapInt":
                        tos = self._word(self.heap_start + offset) if offset + 4 <= self.heap_size else 0
                    else:
                        tos = self.memory[self.heap_start + offset] if offset < self.heap_size else 0
                elif op.startswith("setHeap"):
                    offset = tos & 0xFFFFFFFF
                    value = pop()
                    size = 1 if op == "setHeapByte" else 4
                    if offset + size > self.heap_size:
                        raise VMError("Out-of-range heap write rejected by harness")
                    if size == 1: self.memory[self.heap_start + offset] = value & 255
                    else: self._write(self.heap_start + offset, value)
                    tos = pop()
                else:
                    b = tos
                    a = pop()
                    if op.endswith("_float"):
                        a, b = bits_float(a), bits_float(b)
                    operation = op.split("_", 1)[0]
                    if operation in ("div", "mod") and b == 0: raise VMError("Division by zero")
                    if operation == "add": value = a + b
                    elif operation == "sub": value = a - b
                    elif operation == "mul": value = a * b
                    elif operation == "div": value = a / b if op.endswith("_float") else math.trunc(a / b)
                    elif operation == "mod": value = a - math.trunc(a / b) * b
                    elif operation == "bitwiseOr": value = a | b
                    elif operation == "bitwiseAnd": value = a & b
                    elif operation == "bitwiseXor": value = a ^ b
                    elif operation in ("bitShiftLeft", "bitShiftRight"):
                        if not 0 <= b < 32: raise VMError("Undefined integer shift")
                        value = a << b if operation == "bitShiftLeft" else a >> b
                    elif operation == "logicalOr": value = int(bool(a) or bool(b))
                    elif operation == "logicalAnd": value = int(bool(a) and bool(b))
                    else: raise VMError(f"Unimplemented instruction {op}")
                    tos = float_bits(value) if op.endswith("_float") else i32(value)
            else:
                raise VMError(f"Instruction limit exceeded in {signature}")
        except Exception as error:
            raise VMError(f"{error}; callback={signature}, pc={pc}, instruction={op if ops else '?'}") from error
        finally:
            self.max_stack_bytes = max(self.max_stack_bytes, self.stack_end - minimum_sp)
        self.calls += 1
        self.max_instructions = max(self.max_instructions, ops)
        self.executed_instructions += ops
        return {"return": bits_float(tos) if types[0] == "f" else tos,
                "instructions": ops, "stack_bytes": self.stack_end - minimum_sp,
                "stack_capacity_bytes": self.stack_end - self.stack_start}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("binary", type=Path)
    args = parser.parse_args()
    vm = Harness(args.binary)
    print(json.dumps({"type": "program", "sha256": vm.sha256, "code_bytes": vm.code_size,
                      "heap_bytes": vm.heap_size, "globals": vm.num_globals,
                      "kind": "independent_python_bytecode_interpreter"}), flush=True)
    for line in sys.stdin:
        command = json.loads(line)
        if "time_ms" in command:
            vm.time_ms = int(command["time_ms"])
        midi_start, log_start, overlay_start = len(vm.midi), len(vm.logs), len(vm.overlays)
        result = vm.call(command["call"], *command.get("args", [])) if "call" in command else {}
        result.update(midi=vm.midi[midi_start:], logs=vm.logs[log_start:],
                      overlays=vm.overlays[overlay_start:])
        if command.get("capture"): result["leds"] = vm.leds
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
