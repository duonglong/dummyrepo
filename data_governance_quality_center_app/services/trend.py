"""The score over time and the reasons a score changed, both read from the score snapshots."""
from datetime import timedelta, timezone

from ..const import TREND_PERIODS
from . import scoring


def period_start(day, period):
    """The first day of the day, ISO week or month that holds ``day``.

    Requirements: FR-DGQ-0034
    """
    if period == 'week':
        return day - timedelta(days=day.weekday())
    if period == 'month':
        return day.replace(day=1)
    return day


def next_period(start, period):
    """The first day of the period after the one starting on ``start``."""
    if period == 'day':
        return start + timedelta(days=1)
    if period == 'week':
        return start + timedelta(days=7)
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def trend_points(env, snapshots, period):
    """The score of each period of the snapshots, using the last snapshot of each model and company in it.

    A period without a snapshot between the first and the last one is listed with no score, so
    a gap shows as a gap and no value is guessed. The ``snapshot_id`` of a point is that of the
    one snapshot behind it, empty when several models or companies make the figure.

    Requirements: FR-DGQ-0034
    """
    if period not in TREND_PERIODS:
        period = 'day'
    last = {}  # (period start, company id, model id) -> snapshot
    for snap in snapshots.sorted(lambda snap: (snap.date, snap.id)):
        day = _local_day(env, snap.date)
        last[(period_start(day, period), snap.company_id.id, snap.model_id.id)] = snap
    by_period = {}
    for (start, _company, _model), snap in last.items():
        by_period.setdefault(start, []).append(snap)
    if not by_period:
        return []
    points, start = [], min(by_period)
    while start <= max(by_period):
        group = by_period.get(start, [])
        applicable = sum(snap.applicable_weight for snap in group)
        failed = sum(snap.failed_weight for snap in group)
        points.append({
            'period': start.isoformat(),
            'score': scoring.score(applicable, failed),
            'snapshot_id': group[0].id if len(group) == 1 else False,
        })
        start = next_period(start, period)
    return points


def _local_day(env, moment):
    """The calendar day of a stored UTC datetime in the user's time zone."""
    return moment.replace(tzinfo=timezone.utc).astimezone(env.tz).date()


def explain_change(env, before, after):
    """The causes of the score change between two snapshots of one model and company, biggest first.

    The rule rows of both snapshots are compared and the changes applied one after the other to the
    score of ``before``: rules added, rules removed or switched off, rules that changed version, the
    applicable weight of the others (records added or removed) and then their failed weight. Each
    cause is the points of score its step moves, so the points add up to the whole change.

    Requirements: FR-DGQ-0035
    """
    old, new = _rule_rows(before), _rule_rows(after)
    applicable = sum(row['applicable_weight'] for row in old.values())
    failed = sum(row['failed_weight'] for row in old.values())
    rules = env['data.quality.rule'].with_context(active_test=False).browse(
        [rule_id for rule_id in {*old, *new} if rule_id]).exists()
    causes = []

    def step(kind, rule_id, new_applicable, new_failed):
        nonlocal applicable, failed
        points = _points(applicable, failed, new_applicable, new_failed)
        applicable, failed = new_applicable, new_failed
        if points or not kind.endswith('_weight_changed'):  # a rule change is listed even with no effect
            rule = rules.browse(rule_id)
            causes.append({'kind': kind, 'rule_id': rule.id, 'rule_name': rule.name or False, 'points': points})

    def current(rule_id, field):
        return (new.get(rule_id) or old[rule_id])[field]

    for rule_id in sorted(set(new) - set(old)):
        step('rule_added', rule_id, applicable + new[rule_id]['applicable_weight'], failed + new[rule_id]['failed_weight'])
    for rule_id in sorted(set(old) - set(new)):
        kind = 'rule_switched_off' if rule_id in rules.ids and not rules.browse(rule_id).active else 'rule_removed'
        step(kind, rule_id, applicable - old[rule_id]['applicable_weight'], failed - old[rule_id]['failed_weight'])
    common = sorted(set(old) & set(new))
    versioned = [rule_id for rule_id in common if old[rule_id]['rule_version'] != new[rule_id]['rule_version']]
    for rule_id in versioned:
        step('rule_version_changed', rule_id,
             applicable + new[rule_id]['applicable_weight'] - old[rule_id]['applicable_weight'],
             failed + new[rule_id]['failed_weight'] - old[rule_id]['failed_weight'])
    for rule_id in set(common) - set(versioned):
        step('applicable_weight_changed', rule_id,
             applicable + new[rule_id]['applicable_weight'] - old[rule_id]['applicable_weight'], failed)
    for rule_id in set(common) - set(versioned):
        step('failed_weight_changed', rule_id, applicable,
             failed + new[rule_id]['failed_weight'] - old[rule_id]['failed_weight'])
    causes.sort(key=lambda cause: (-abs(cause['points']), cause['kind'], cause['rule_id']))
    return {
        'score_before': before.score if before.has_score else None,
        'score_after': after.score if after.has_score else None,
        'causes': causes,
    }


def _points(applicable, failed, new_applicable, new_failed):
    """The score points a step moves; a score that begins to exist counts from 100, one that vanishes moves nothing."""
    start, end = scoring.score(applicable, failed), scoring.score(new_applicable, new_failed)
    if end is None:
        return 0.0
    return end - (100.0 if start is None else start)


def _rule_rows(snapshot):
    """The rule rows of the scan behind a model snapshot: {rule id: weights and version}.

    Rows of a rule deleted since are grouped under id 0.
    """
    Snapshot = snapshot.env['data.quality.score.snapshot']
    domain = [('company_id', '=', snapshot.company_id.id), ('model_id', '=', snapshot.model_id.id),
              ('severity', '!=', False)]
    domain.append(('scan_id', '=', snapshot.scan_id.id) if snapshot.scan_id else ('date', '=', snapshot.date))
    rows = {}
    for row in Snapshot.search(domain):
        figures = rows.setdefault(row.rule_id.id, {'applicable_weight': 0, 'failed_weight': 0, 'rule_version': row.rule_version})
        figures['applicable_weight'] += row.applicable_weight
        figures['failed_weight'] += row.failed_weight
    return rows
