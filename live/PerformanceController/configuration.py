"""Private fixed-source Audio recovery trial configuration."""
# Empty fails closed, including Untitled Sets. Set the exact cheap fixture path at installation.
TARGET_SET = ''
# Optional exact-path tuple for a coordinated cheap/heavy installation.
# Empty falls back to TARGET_SET. Untitled never matches.
TARGET_SETS = ()
# Four white controls per group. Missing/invalid entries disable only that control.
# Example device target: {'kind':'device','device_path':[0], 'parameter_index':3}.
PARAMETER_BINDINGS = {}
INPUT_TYPE = 'Ext. In'
# Fixed trial source, copied from the prepared Temple of Loops input track.
INPUT_NAME = 'T-Bone'
INPUT_COLOR_INDEX = 64
# Empty keeps the accepted fixed-source path. Audio patch mode requires two
# explicitly prepared helpers inside every group's rightmost [Remote].
# Example: ({'name':'T-Bone','helper_name':'[Input 1]'},
#           {'name':'SM-58','helper_name':'[Input 2]'})
AUDIO_SOURCES = ()
# False selects a prepared-only trial: no new Audio tracks or phrase scenes,
# and no background input/output routing configuration. Choose before startup.
ALLOW_AUDIO_TRACK_CREATION = True
PREPARATION_TIMEOUT = 8.0
TRACE_PATH = None

# Optional private readiness telemetry; never read extra Native objects for this.
STATUS_PATH = None
