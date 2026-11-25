"""Business limits of the profiling engine, each named once."""

MAX_LISTED_VALUES = 100  # distinct values listed per field (FR-DPA-0007)
COMMON_FORMAT_SHARE = 0.6  # share of a format that makes it "the" format (FR-DPA-0009)
USUAL_RANGE_MIN_VALUES = 10  # fewest values to work out a usual range (BR-DPA-0004)
USUAL_RANGE_FACTOR = 1.5  # Tukey fence factor (BR-DPA-0004)
