# Data Profiling & Analysis

Profiles the data of any model a user picks and reports on its quality. Only users of the
Data Profiling group see the app or its records.

## Notes

- Every flag of a report is a text label (High empty rate, Inconsistent format, Unusual number), never a colour alone.
- A profile of 100,000 records and 30 fields is checked to finish within 60 seconds (`tests/test_benchmark.py`).
- Only members of the Data Profiling group have a right on any profiling model; all members share every profile and snapshot.
