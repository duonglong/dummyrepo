from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services import sourcing


class ProcurementSourcingCriterion(models.Model):
    """A weighted criterion on a sourcing template or on one event.

    Requirements: FR-SPM-0031
    """
    _name = 'procurement.sourcing.criterion'
    _description = 'Sourcing Criterion'
    _order = 'sequence, id'

    template_id = fields.Many2one('procurement.sourcing.template', ondelete='cascade', index=True)
    event_id = fields.Many2one('procurement.sourcing.event', ondelete='cascade', index=True)
    name = fields.Char(required=True, translate=True)
    kind = fields.Selection([
        ('price', 'Price'), ('total_cost', 'Total cost'), ('lead_time', 'Lead time'),
        ('terms', 'Payment terms'), ('manual', 'Manual score'), ('yes_no', 'Yes/no'),
    ], required=True, default='manual')
    direction = fields.Selection([
        ('higher', 'Higher is better'), ('lower', 'Lower is better'),
    ], required=True, default='lower')
    weight = fields.Float(default=1.0)
    sequence = fields.Integer(default=10)

    _weight_positive = models.Constraint('CHECK(weight >= 0)', 'The weight cannot be negative.')

    @api.constrains('template_id', 'event_id')
    def _check_one_parent(self):
        """Requirements: FR-SPM-0031"""
        for criterion in self:
            if bool(criterion.template_id) == bool(criterion.event_id):
                raise ValidationError(self.env._("A criterion belongs to a template or to an event, not both."))

    def write(self, vals):
        """Requirements: FR-SPM-0041: the evidence of an awarded event is not changed by a user."""
        sourcing.check_event_not_awarded(self.env, self.event_id)
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0041: the evidence of an awarded event is not deleted by a user."""
        sourcing.check_event_not_awarded(self.env, self.event_id)
        return super().unlink()
