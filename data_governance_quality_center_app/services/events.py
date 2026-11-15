"""Queueing of the records saved on a model watched by an event automation.

The save itself only inserts the waiting rows with one statement: the check runs later, in the
background, so a failing rule never delays or blocks the user's work.
"""
from odoo.tools import SQL

QUEUE_CRON = 'data_governance_quality_center_app.ir_cron_dgq_event_queue'


def watch(env, model_names):
    """Remember, for this registry, the models whose saves an event automation may want."""
    env.registry.__dict__.setdefault('dgq_event_models', set()).update(model_names)


def is_watched(env, model_name):
    """Whether the model may have an event automation: a set lookup, made on every save."""
    return model_name in env.registry.__dict__.get('dgq_event_models', ())


def queue_saved(env, records, event, field_names=()):
    """Queue the saved records for each active event automation that watches this save.

    ``event`` is ``create`` or ``write``; a write counts when the automation names no field of the
    model or one of the written fields. Returns how many waiting rows were new.

    Requirements: FR-DGQ-0064
    """
    ids = [record_id for record_id in records.ids if record_id]
    if not ids:
        return 0
    # sudo: the saving user has no access to automations or to the queue
    automations = env['data.quality.automation'].sudo().search([
        ('trigger', '=', 'event'), ('event', 'in', (event, 'both')),
        ('model_ids.model', '=', records._name)])
    written = set(field_names)
    rows = []
    for automation in automations:
        watched = automation.watched_field_ids.filtered(lambda field: field.model == records._name)
        if event == 'write' and watched and not written & set(watched.mapped('name')):
            continue
        rows.extend((automation.id, records._name, record_id) for record_id in ids)
    if not rows:
        return 0
    env.cr.execute(SQL(
        "INSERT INTO data_quality_event_queue (automation_id, res_model, res_id, create_uid, create_date,"
        " write_uid, write_date) VALUES %s ON CONFLICT DO NOTHING",
        SQL(", ").join(
            SQL("(%s, %s, %s, %s, now() AT TIME ZONE 'UTC', %s, now() AT TIME ZONE 'UTC')",
                automation_id, res_model, res_id, env.uid, env.uid)
            for automation_id, res_model, res_id in rows)))
    if env.cr.rowcount:
        env.ref(QUEUE_CRON).sudo()._trigger()  # a user may not read scheduled actions
    return env.cr.rowcount
