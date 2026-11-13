"""Business limits of the data quality center, each named once."""

# Records one batch of a background job reads and writes (FR-DGQ-0016).
JOB_BATCH_SIZE = 1000

# A running job whose heartbeat is older than this is considered stopped (ADR-DGQ-002).
STALE_JOB_MINUTES = 30

# Audit entries a purge deletes in one transaction (FR-DGQ-0073).
AUDIT_PURGE_BATCH_SIZE = 10000
