"""Writing and purging the audit trail.

The audit table is append-only for the ORM (``data.quality.audit`` refuses write and
unlink for everyone); this module is the one place that writes entries and the one
place that deletes them, by SQL, for the administrator's purge.
"""
from odoo.exceptions import AccessError
from odoo.tools import SQL

from ..const import AUDIT_PURGE_BATCH_SIZE
from .export import day_start_utc


def entry_values(env, event, record=None, changes=None, description=False, field_names=False,
                 automation=None, user=None):
    """The values of one audit entry; ``log_many`` writes a list of them in one call.

    ``user`` is the person responsible when it is not the current user (a background job writes
    for the person who started it); ``automation`` is the automation whose run caused the event.
    Values of sensitive fields must never be passed in: name the field in ``field_names`` only.

    Requirements: FR-DGQ-0072, BR-DGQ-0010
    """
    vals = {
        'event': event,
        'user_id': (user or env.user).id,
        'company_id': env.company.id,
        'automation_id': automation.id if automation else False,
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
    return vals


def log_many(env, values_list):
    """Write the entries of ``entry_values`` in one call and return them.

    Requirements: FR-DGQ-0072
    """
    # sudo: nobody holds create access, the app writes the trail itself
    return env['data.quality.audit'].sudo().create(values_list)


def log(env, event, record=None, **details):
    """Write one audit entry for the current user and return it.

    ``record`` is the record the event is about; ``changes`` maps a field name to its
    ``[before, after]`` values (see ``entry_values`` for the other details).

    Requirements: FR-DGQ-0072
    """
    return log_many(env, [entry_values(env, event, record, **details)])


def purge(env, date_before):
    """Delete the entries before the day ``date_before`` (a date) and write the purge entry.

    Only an administrator may purge. The deletion is a SQL DELETE in batches, bypassing the
    ORM guard on purpose; the purge leaves an entry that names the user and the limit.

    Requirements: FR-DGQ-0073
    """
    if not env.is_superuser() and not env.user.has_group('data_governance_quality_center_app.group_dgq_admin'):
        raise AccessError(env._("Only an administrator can purge the audit trail."))
    audit = env['data.quality.audit']
    domain = [('date', '<', day_start_utc(env, date_before))]
    audit.flush_model()
    deleted = 0
    while True:
        # the same domain as the dialog's count: only the entries of the user's companies
        ids = audit._search(domain, limit=AUDIT_PURGE_BATCH_SIZE).get_result_ids()
        if not ids:
            break
        env.cr.execute(SQL("DELETE FROM data_quality_audit WHERE id IN %s", tuple(ids)))
        deleted += env.cr.rowcount
    env['data.quality.audit'].invalidate_model()
    return log(
        env, 'purge',
        description=env._("Purged %(count)s entries older than %(date)s",
                          count=deleted, date=date_before.isoformat()),
        changes={'date_before': [False, date_before.isoformat()], 'deleted': [False, deleted]},
    )
