from collections import defaultdict

from odoo import api, fields, models

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
        """Turn the waiting rows into one event scan for each automation and model.

        Rows of a record that is gone or of an automation switched off are dropped. A scope that is
        busy keeps its rows for the next run.

        Requirements: FR-DGQ-0064
        """
        groups = defaultdict(lambda: self.browse())
        for row in self.sudo().search([]):  # sudo: the runner is the app, the queue has no user access
            groups[(row.automation_id, row.res_model)] |= row
        for (automation, res_model), rows in groups.items():
            Model = self.env.get(res_model)
            if Model is None or not automation.active:
                rows.unlink()
                continue
            # sudo: only the existence of the saved records is looked at
            existing = Model.sudo().browse(rows.mapped('res_id')).exists()
            rows.filtered(lambda row: row.res_id not in existing.ids).unlink()
            rows = rows.filtered(lambda row: row.res_id in existing.ids)
            if rows and automation._dgq_launch_event(res_model, rows.mapped('res_id')):
                rows.unlink()


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
