"""Shared source-bank and receiver metadata constants for the matched controller."""
MAX_SOURCES = 25
SOURCE_MASK = (1 << MAX_SOURCES) - 1
LOW_BITS, LOW_MASK, HIGH_MASK = 13, (1 << 13) - 1, (1 << 12) - 1
SOURCE_NAME_BASE = 64
RECEIVER_KEY = 'net.xiphe.lightpad.midi.receiver.v1'
MIDI_OWNER_KEY = 'net.xiphe.lightpad.midi.owner.v1'
