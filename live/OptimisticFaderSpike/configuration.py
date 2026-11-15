"""Disposable spike settings. Configure only the staged installation copy."""
# Exact saved Set path. Empty disables target binding and every mutation.
TARGET_SET = ''
# Nonblocking acknowledgement delay only. Native writes remain immediate.
ACK_DELAY_SECONDS = 0.0
# Optional JSONL trace path; disabled in the portable source.
TRACE_PATH = None
