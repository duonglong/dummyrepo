"""Shared names of the reconciliation status and history events.

Requirements: FR-IPR-0020
"""

STATES = [
    ('not_checked', 'Not checked'),
    ('matched', 'Matched'),
    ('waiting', 'Waiting for approval'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
]

EVENTS = [
    ('check', 'Check'),
    ('approve', 'Approve'),
    ('reject', 'Reject'),
    ('reset', 'Reset'),
    ('delete', 'Deleted'),
]
