from odoo import api, fields, models

from ..const import JOB_BATCH_SIZE
from ..services import events


class DataQualityEventQueue(models.Model):
    """A record waiting for the check of an event automation: one row per automation and record.

    Requirements: FR-DGQ-0064
    """
    _name = 'data.quality.event.queue'
    _description = 'Data Quality Event Queue'
    _order = 'id'

    automation_id = fields.Many2one('data.quality.automation', required=True, ondelete='cascade', index=True)
    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)

    _record_unique = models.Constraint(
        'UNIQUE (automation_id, res_model, res_id)', 'A record waits once for each automation.')

    @api.model
    def _dgq_process(self):
        """Turn the waiting rows into event scans, at most one batch of each automation and model.

        The scheduled action works through the groups and commits after each, so the rows that wait
        for the next run are never more than the work of one run.

        Requirements: FR-DGQ-0064
        """
        queue = self.sudo()  # sudo: the runner is the app, the queue has no user access
        groups = queue._read_group([], ['automation_id', 'res_model'], ['__count'])
        remaining = sum(count for _automation, _model, count in groups)
        for automation, res_model, _count in groups:
            handled = queue._dgq_process_group(automation, res_model)
            remaining -= handled
            if not self.env['ir.cron']._commit_progress(handled, remaining=remaining):
                break

    def _dgq_process_group(self, automation, res_model):
        """Check the first batch of the rows of one automation and model; return how many rows were handled.

        Rows of a record that is gone or of an automation switched off or no longer on an event are dropped. A scope that is
        busy keeps its rows for the next run.

        Requirements: FR-DGQ-0064
        """
        rows = self.search(
            [('automation_id', '=', automation.id), ('res_model', '=', res_model)],
            order='id', limit=JOB_BATCH_SIZE)
        Model = self.env.get(res_model)
        if Model is None or not automation.active or automation.trigger != 'event':
            rows.unlink()
            return len(rows)
        # sudo: only the existence of the saved records is looked at
        existing = set(Model.sudo().browse(rows.mapped('res_id')).exists().ids)
        gone = rows.filtered(lambda row: row.res_id not in existing)
        gone.unlink()
        waiting = rows - gone
        if waiting and automation._dgq_launch_event(res_model, waiting.mapped('res_id')):
            waiting.unlink()
            return len(rows)
        return len(gone)


class Base(models.AbstractModel):
    """Queue the saves of the models an event automation watches; nothing else changes."""
    _inherit = 'base'

    @api.model_create_multi
    def create(self, vals_list):
        """Queue the new records for the event automations that watch their creation.

        Requirements: FR-DGQ-0064
        """
        records = super().create(vals_list)
        if events.is_watched(self.env, self._name):
            events.queue_saved(self.env, records, 'create')
        return records

    def write(self, vals):
        """Queue the changed records for the event automations that watch one of the written fields.

        Requirements: FR-DGQ-0064
        """
        result = super().write(vals)
        if vals and events.is_watched(self.env, self._name):
            events.queue_saved(self.env, self, 'write', vals)
        return result
