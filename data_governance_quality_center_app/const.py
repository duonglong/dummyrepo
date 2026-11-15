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

# Days scan result lines and import files are kept, and days a Resolved or Ignored issue is kept (BR-DGQ-0013, FR-DGQ-0077).
SCAN_LINE_RETENTION_DAYS = 90
ISSUE_RETENTION_DAYS = 365
SCAN_LINE_RETENTION_PARAMETER = 'data_quality.scan_line_retention_days'
ISSUE_RETENTION_PARAMETER = 'data_quality.issue_retention_days'

# Days a failed run stays on the health page (FR-DGQ-0079).
HEALTH_FAILED_DAYS = 7

# Scans the health page lists with their duration (FR-DGQ-0079).
HEALTH_LAST_SCANS = 5

# Settings keys the scheduled scans and the escalation read (FR-DGQ-0076).
SCAN_FREQUENCY_PARAMETER = 'data_quality.scan_frequency'
ESCALATION_DAYS_PARAMETER = 'data_quality.escalation_days'

# Most frequent values, shapes and linked records a profile keeps for a field (FR-DGQ-0053, FR-DGQ-0055).
PROFILE_TOP_COUNT = 20

# Days without an update after which a record is stale, unless the profile says otherwise (FR-DGQ-0055).
PROFILE_STALE_DAYS = 365

# A number is unusual when it lies this many interquartile ranges outside the quartiles (FR-DGQ-0054).
PROFILE_OUTLIER_FACTOR = 1.5

# Largest import file and most data rows a check takes (FR-DGQ-0058).
IMPORT_MAX_BYTES = 20 * 1024 * 1024
IMPORT_MAX_ROWS = 100000

# Rule types an import check evaluates on the rows of a file: they read the values of the row only.
# A link rule and a custom validator need saved records: the check names them as not checked (FR-DGQ-0059).
IMPORT_RULE_TYPES = ('required', 'pattern', 'range', 'date', 'condition', 'cross_field')

# Longest cell value kept with a finding.
IMPORT_VALUE_MAX_LENGTH = 255

# Space between the default sequences, so a person can slot a widget in between (FR-DGQ-0037).
DASHBOARD_WIDGET_STEP = 10

# The periods of the score trend (FR-DGQ-0034).
TREND_PERIODS = ('day', 'week', 'month')
# Records listed behind a figure of records (FR-DGQ-0036).
DRILL_DOWN_RECORD_LIMIT = 1000
