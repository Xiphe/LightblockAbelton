"""Matched controller defaults; runtime paths and bindings are configured privately."""
# Empty fails closed, including Untitled Sets. Configure exact saved Set paths at installation.
TARGET_SET = ''
# Optional exact-path allowlist for multiple saved Sets.
# Empty falls back to TARGET_SET. Untitled never matches.
TARGET_SETS = ()
# Four white controls per group. Missing/invalid entries disable only that control.
# Example device target: {'kind':'device','device_path':[0], 'parameter_index':3}.
PARAMETER_BINDINGS = {}
INPUT_TYPE = 'Ext. In'
# Fixed Audio fallback when the catalog is disabled.
INPUT_NAME = 'T-Bone'
INPUT_COLOR_INDEX = 64
# Set to 'catalog' to enable up to 25 Audio sources and preconfigured MIDI instruments.
# Slots follow descendants of the unique [Inputs] group in Live order.
# Source identity and explicit MIDI receiver markers replace helper/name mappings.
AUDIO_SOURCES = ()
# False selects prepared-only operation: no new Audio/MIDI tracks or phrase scenes,
# while existing free lanes may still be prepared for the selected source.
ALLOW_AUDIO_TRACK_CREATION = True
PREPARATION_TIMEOUT = 8.0
TRACE_PATH = None

# Optional private readiness telemetry; never read extra Native objects for this.
STATUS_PATH = None
