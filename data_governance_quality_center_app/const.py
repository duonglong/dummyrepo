"""Business limits of the data quality center, each named once."""

# Records one batch of a background job reads and writes (FR-DGQ-0016).
JOB_BATCH_SIZE = 1000

# A running job whose heartbeat is older than this is considered stopped (ADR-DGQ-002).
STALE_JOB_MINUTES = 30

# Audit entries a purge deletes in one transaction (FR-DGQ-0073).
AUDIT_PURGE_BATCH_SIZE = 10000

# Similarity from which a duplicate rule shows a pair: allowed range (FR-DGQ-0038).
DUPLICATE_MIN_THRESHOLD = 50
DUPLICATE_MAX_THRESHOLD = 100

# Weight of a matching field of a duplicate rule (FR-DGQ-0038).
DUPLICATE_MIN_WEIGHT = 1
DUPLICATE_MAX_WEIGHT = 10

# Records sharing one blocking key that are compared; a larger block is skipped and counted (ADR-DGQ-009).
DUPLICATE_MAX_BLOCK_SIZE = 500

# Longest blocking key stored as is; a longer one is stored as its hash so that it fits the index (ADR-DGQ-009).
DUPLICATE_MAX_BLOCK_KEY_LENGTH = 200

# Records a merge takes: the smallest and the largest number (FR-DGQ-0042).
MERGE_MIN_RECORDS = 2
MERGE_MAX_RECORDS = 10

# Technical models whose rows never count as linked documents of a record: followers, messages,
# activities, user accounts and the like are not what a manager weighs in a merge (FR-DGQ-0040).
MERGE_TECHNICAL_MODEL_PREFIXES = ('ir.', 'mail.', 'bus.', 'base.', 'res.users', 'res.config', 'res.groups')

# Tables whose rows only link two records: a row that would duplicate one the kept record already
# has is left on the archived record instead of stopping a merge (FR-DGQ-0043).
MERGE_ASSOCIATION_TABLES = ('mail_followers',)

# Models whose records are never merged or corrected without an explicit authorization (BR-DGQ-0005).
PROTECTED_MODELS = (
    'account.move', 'account.move.line', 'account.payment', 'account.bank.statement.line',
    'account.tax', 'account.account', 'account.journal',
)
