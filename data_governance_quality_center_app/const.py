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

# The system parameter holding whether assignment notices are also e-mailed, and its default (FR-DGQ-0028, FR-DGQ-0076).
NOTIFY_EMAIL_PARAMETER = 'data_quality.notify_email'
NOTIFY_EMAIL_DEFAULT = True

# The priority an issue starts with, by the severity of its rule (FR-DGQ-0021).
ISSUE_PRIORITY_BY_SEVERITY = {'info': 'low', 'warning': 'normal', 'critical': 'high'}

# The statuses an issue may move to, by its current status; any other move is refused (BR-DGQ-0004).
ISSUE_STATE_MOVES = {
    'new': ('assigned', 'in_progress', 'resolved', 'ignored'),
    'assigned': ('in_progress', 'resolved', 'ignored', 'new'),
    'in_progress': ('resolved', 'ignored', 'assigned'),
    'resolved': ('new',),
    'ignored': ('new',),
}

# The statuses of an issue that a scan may resolve: the work is not finished or set aside (FR-DGQ-0023).
ISSUE_OPEN_STATES = ('new', 'assigned', 'in_progress')

# Skipped issues a bulk action names in its result; the others are only counted (FR-DGQ-0027).
BULK_SKIPPED_LISTED = 20

# The weight of a failed check by the severity of its rule, each a system parameter 'data_quality.weight_<severity>' (BR-DGQ-0001).
SEVERITY_WEIGHT_DEFAULTS = {'info': 1, 'warning': 3, 'critical': 10}
SEVERITY_WEIGHT_PARAMETER = 'data_quality.weight_%s'

# Models with the lowest score and issues the dashboard lists (FR-DGQ-0032).
DASHBOARD_LOWEST_MODELS = 5
DASHBOARD_RECENT_ISSUES = 10

# Times a failed automation run is tried again at its next due times before it is marked failed (FR-DGQ-0069).
AUTOMATION_RETRIES = 3

# Days after its deadline that an open issue waits before it is escalated, unless the automation says otherwise (FR-DGQ-0067).
ESCALATION_DEFAULT_DAYS = 1
