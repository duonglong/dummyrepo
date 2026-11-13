"""Writing and purging the audit trail.

The audit table is append-only for the ORM (``data.quality.audit`` refuses write and
unlink for everyone); this module is the one place that writes entries and the one
place that deletes them, by SQL, for the administrator's purge.
"""
from datetime import datetime, time

from odoo.exceptions import AccessError
from odoo.tools import SQL

from ..const import AUDIT_PURGE_BATCH_SIZE


def log(env, event, record=None, changes=None, description=False, field_names=False):
    """Write one audit entry for the current user and return it.

    ``record`` is the record the event is about; ``changes`` maps a field name to its
    ``[before, after]`` values. Values of sensitive fields must never be passed in.

    Requirements: FR-DGQ-0072
    """
    vals = {
        'event': event,
        'user_id': env.user.id,
        'company_id': env.company.id,
        'changes': changes or False,
        'field_names': field_names or (', '.join(changes) if changes else False),
        'description': description,
    }
    if record:
        vals.update(
            res_model=record._name,
            res_id=record.id,
            record_name=record.display_name,
            company_id=(record.company_id.id if 'company_id' in record._fields
                        else env.company.id),
        )
    # sudo: nobody holds create access, the app writes the trail itself
    return env['data.quality.audit'].sudo().create(vals)


def purge(env, date_before):
    """Delete the entries before the day ``date_before`` (a date) and write the purge entry.

    Only an administrator may purge. The deletion is a SQL DELETE in batches, bypassing the
    ORM guard on purpose; the purge leaves an entry that names the user and the limit.

    Requirements: FR-DGQ-0073
    """
    if not env.is_superuser() and not env.user.has_group('data_governance_quality_center_app.group_dgq_admin'):
        raise AccessError(env._("Only an administrator can purge the audit trail."))
    limit = datetime.combine(date_before, time.min)
    env['data.quality.audit'].flush_model()
    deleted = 0
    while True:
        env.cr.execute(SQL(
            "DELETE FROM data_quality_audit WHERE id IN ("
            "SELECT id FROM data_quality_audit WHERE date < %s LIMIT %s)",
            limit, AUDIT_PURGE_BATCH_SIZE,
        ))
        deleted += env.cr.rowcount
        if env.cr.rowcount < AUDIT_PURGE_BATCH_SIZE:
            break
    env['data.quality.audit'].invalidate_model()
    return log(
        env, 'purge',
        description=env._("Purged %(count)s entries older than %(date)s",
                          count=deleted, date=date_before.isoformat()),
        changes={'date_before': [False, date_before.isoformat()], 'deleted': [False, deleted]},
    )
