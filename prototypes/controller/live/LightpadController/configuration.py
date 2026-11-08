"""Explicit scratch-Set opt-in. No track creation, routing or installation.

These names must be unique top-level native Group Tracks. Do not rename tracks
in an original project to match. Prepare a separate scratch Set instead.
"""

GROUP_NAMES = tuple("Lightpad %d" % (index + 1) for index in range(8))

# The coordinator sets these only in a reviewed scratch installation. A missing
# manifest leaves the mute/mixer adapter available, with recording disabled.
AUDIO_CONTROLLER_MANIFEST = None
SYNC_UI_EVIDENCE = None
VALIDATED_AUDIO_FEATURES = ()
# Only a reviewed installation with matching native BeatTime evidence enables
# changing meter on the verified scratch grid. Other Sets remain unsupported.
NATIVE_BAR_CLOCK = False
# Per-group sequences of four explicit parameter target specifications.
# An absent group uses its four native return sends.
PARAMETER_TARGETS = {}

# Coordinator-reviewed installation may select this isolated proof mode.
# It has no MIDI mapping/output and still starts with Live mutations disabled.
AUDIO_PROOF_MODE = False
PROOF_DIRECTORY = None

# Separate no-MIDI trial over the actual normal-controller executor. Requires
# an explicit manifest and private output directory in the installed copy.
AUDIO_CONTROLLER_PROBE_MODE = False
CONTROLLER_PROBE_DIRECTORY = None
