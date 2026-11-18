"""Internal outcomes, deliberately separate from wire ACK values."""
APPLIED, OUTCOME_QUEUED, REJECTED, BUSY, NOT_READY, STALE = range(6)

# Matched packaging checks this literal before installation. Bank two adds
# mixer-only Constants without changing bank one's recording/source keys.
PERFORMANCE_CONTRACT = 2
CONSTANT_PARAMETER_BASE = 128
CONSTANT_PARAMETER_COUNT = 24
PATCH_DESIRED_FACT_BASE = 80
MONITOR_OWNER_FACT = 88
