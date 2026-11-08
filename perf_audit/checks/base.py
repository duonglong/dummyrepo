"""The contract of an audit check and the registry the run engine reads.

Requirements: BR-OPA-0002, BR-OPA-0005, FR-OPA-0003
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# Severity of a finding per check (BR-OPA-0005). A key is a check code, or a
# (check code, finding kind) pair for a check whose parts differ. B1 is absent:
# its severity depends on the evidence, so the check supplies it per finding.
SEVERITY = {
    'A1': 'medium',
    'A2': 'high',
    'A3': 'low',
    'A4': 'medium',
    'A5': 'low',
    'A6': 'medium',
    'A7': 'low',
    ('A8', 'long'): 'high',
    ('A8', 'lock'): 'high',
    'B2': 'medium',
    'B3': 'medium',
    'B4': 'medium',
    'B5': 'medium',
    'B6': 'low',
    ('B7', 'failure'): 'high',
    ('B7', 'runtime'): 'high',
    ('B7', 'late'): 'medium',
    ('B7', 'shared'): 'low',
}

BYTES_PER_MEGABYTE = 1024 * 1024

_REGISTRY = {}


def table_models(env):
    """Name of the model that owns each table, by table name (the first by name when several share one)."""
    owners = {}
    for model_name in sorted(env.registry):
        model = env[model_name]
        if not model._abstract and model._auto:
            owners.setdefault(model._table, model_name)
    return owners


def severity_for(code, kind=None):
    """Severity BR-OPA-0005 gives to a finding of this check and kind, or None when the check decides.

    Requirements: BR-OPA-0005
    """
    return SEVERITY.get((code, kind)) or SEVERITY.get(code)


@dataclass
class AuditContext:
    """What a check reads: the environment, the probe, and its thresholds by key."""

    env: object
    probe: object
    thresholds: dict = field(default_factory=dict)
    run: object = None
    # values a check adds to its result line: part_skip_reason, measures
    outcome: dict = field(default_factory=dict)


class Check(ABC):
    """One audit check; a subclass sets `code` and implements `run`.

    A finding is a dict of perf.audit.finding values without run_id and check_id:
    at least identity_key and message, plus severity when SEVERITY has none for it.
    """

    code = None

    def needs(self, ctx):
        """Return the reason this check cannot run (naming the missing input), or None."""
        return None

    @abstractmethod
    def run(self, ctx):
        """Return the list of findings for the database in `ctx`."""


def register(check_class):
    """Class decorator: make the check available to the run engine under its code."""
    _REGISTRY[check_class.code] = check_class()
    return check_class


def registry():
    """The registered checks by code."""
    return _REGISTRY
