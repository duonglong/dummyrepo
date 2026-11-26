"""Business limits of the profiling engine, each named once."""

MAX_LISTED_VALUES = 100  # distinct values listed per field (FR-DPA-0007)
COMMON_FORMAT_SHARE = 0.6  # share of a format that makes it "the" format (FR-DPA-0009)
USUAL_RANGE_MIN_VALUES = 10  # fewest values to work out a usual range (BR-DPA-0004)
USUAL_RANGE_FACTOR = 1.5  # Tukey fence factor (BR-DPA-0004)

RUN_LOCK_KEY = 0x44504101  # first key of the advisory lock that lets one run of a profile go at a time (FR-DPA-0029)
TECHNICAL_FIELDS = ('id', 'create_uid', 'create_date', 'write_uid', 'write_date')
PROFILABLE_TYPES = (
    'char', 'text', 'html', 'integer', 'float', 'monetary', 'boolean',
    'date', 'datetime', 'selection', 'many2one', 'many2many',
)
TEXT_TYPES = ('char', 'text')
WHITESPACE = ' \t\r\n'  # what a text made only of these characters is empty of (BR-DPA-0001)
RUN_IDS_TABLE = 'dpa_run_ids'
