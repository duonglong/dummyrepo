from odoo import api, fields, models

from ..const import MASK


class ConfigSnapshotItem(models.Model):
    """A system setting covered by snapshots.

    Requirements: FR-TCM-001, BR-TCM-003
    """

    _name = 'config.snapshot.item'
    _description = 'Covered system setting'
    _order = 'key'

    parameter_id = fields.Many2one(
        'ir.config_parameter', string='Setting', required=True,
        ondelete='cascade', index=True)
    key = fields.Char(
        related='parameter_id.key', store=True, readonly=True, index=True)
    value_display = fields.Char(
        string='Value', compute='_compute_value_display')
    sensitive = fields.Boolean(default=False)

    _parameter_unique = models.Constraint(
        'UNIQUE(parameter_id)', 'This setting is already covered.')

    @api.depends('sensitive', 'parameter_id.value')
    def _compute_value_display(self):
        """Mask the value of a sensitive setting; never stored.

        Requirements: FR-TCM-002, BR-TCM-001
        """
        for item in self:
            item.value_display = MASK if item.sensitive else item.parameter_id.sudo().value
