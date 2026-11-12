"""Installed controller settings. The normal mode follows marked groups in Live.

Legacy names and manifests remain solely for the archived proof harness. The
production mode discovers [R1] through [R8] in the loaded Set and needs no file
path, saved fingerprint or local experiment directory.
"""

LIVE_STATE_MODE = True
GROUP_NAMES = tuple("Lightpad %d" % (index + 1) for index in range(8))

# Legacy proof settings. LIVE_STATE_MODE with no manifest uses Live directly,
# including recording; a manifest explicitly selects the archived proof path.
AUDIO_CONTROLLER_MANIFEST = None
SYNC_UI_EVIDENCE = None
VALIDATED_AUDIO_FEATURES = ()
# Legacy proof-grid gate. The Live-state engine reads the loaded Set's Native
# bar grid and does not use this proof-only switch.
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
