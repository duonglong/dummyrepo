"""A8: long and idle-in-transaction sessions, and lock chains.

Requirements: FR-OPA-0014, FR-OPA-0015
"""
from datetime import timedelta

from .base import Check, register, table_models


@register
class SessionCheck(Check):
    """Sessions holding a transaction open for long, and sessions waiting for a lock."""

    code = 'A8'

    def run(self, ctx):
        """Return the long-session findings, then the lock-chain findings.

        Requirements: FR-OPA-0014, FR-OPA-0015
        """
        return self._long_sessions(ctx) + self._lock_chains(ctx)

    def _long_sessions(self, ctx):
        """One finding per other session whose transaction is older than the session age.

        Requirements: FR-OPA-0014
        """
        age = ctx.thresholds['session_age']
        sessions = ctx.probe.long_sessions(timedelta(minutes=age).total_seconds())
        return [self._long_finding(ctx, session, age) for session in sessions]

    @staticmethod
    def _long_finding(ctx, session, age):
        """The finding of one long session: its state, age, application and the start of its query."""
        minutes = timedelta(seconds=session['duration']) / timedelta(minutes=1)
        application = session['application_name'] or ctx.env._('unnamed')
        return {
            'kind': 'long',
            'identity_key': f"A8:long:{session['pid']}",
            'message': ctx.env._(
                'Session %(pid)s (%(app)s) has had a transaction open for %(minutes).1f minutes (%(state)s).',
                pid=session['pid'], app=application, minutes=minutes, state=session['state']),
            'query_text': session['query'],
            'threshold_label': 'session_age',
            'threshold_value': age,
            'found_value': minutes,
            'evidence': {
                'pid': session['pid'], 'state': session['state'],
                'duration_seconds': session['duration'],
                'application_name': session['application_name'],
                'query_start': session['query_start'],
            },
            'code_suggestion': ctx.env._(
                'Find the scheduled action or request behind application %s and make it commit or roll back.',
                application),
        }

    def _lock_chains(self, ctx):
        """One finding per session waiting for a lock, naming its blocking sessions and the table.

        Requirements: FR-OPA-0015
        """
        owners = table_models(ctx.env)
        findings = []
        for waiter in ctx.probe.lock_waits():
            blockers = waiter['blockers'] or []
            blocking_pids = [blocker['pid'] for blocker in blockers]
            table = waiter['table']
            findings.append({
                'kind': 'lock',
                'identity_key': f"A8:lock:{waiter['pid']}",
                'message': ctx.env._(
                    'Session %(pid)s waits for a lock on %(table)s held by session(s) %(blocking)s.',
                    pid=waiter['pid'], table=table or ctx.env._('an unknown table'),
                    blocking=', '.join(map(str, blocking_pids))),
                'table_name': table,
                'model_id': ctx.env['ir.model']._get_id(owners[table]) if table in owners else False,
                'query_text': waiter['query'],
                'evidence': {
                    'waiting_pid': waiter['pid'], 'application_name': waiter['application_name'],
                    'blocking': blockers, 'table': table,
                },
                'code_suggestion': ctx.env._(
                    'Look at the blocking session(s) first: finish or cancel their transaction.'),
            })
        return findings
