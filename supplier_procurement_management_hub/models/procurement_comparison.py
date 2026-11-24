from odoo import fields, models

from ..services import sourcing


class ProcurementComparison(models.Model):
    """The stored comparison of the current quotations of one sourcing event.

    Requirements: FR-SPM-0035, FR-SPM-0037, BR-SPM-0007
    A snapshot rebuilt on demand by the sourcing service; the assumptions it was made with
    are kept as text on it.
    """
    _name = 'procurement.comparison'
    _description = 'Quotation Comparison'

    event_id = fields.Many2one(
        'procurement.sourcing.event', required=True, ondelete='cascade', index=True)
    assumptions = fields.Text(readonly=True)
    is_normalized = fields.Boolean(readonly=True)
    computed_at = fields.Datetime(readonly=True)
    line_ids = fields.One2many('procurement.comparison.line', 'comparison_id', string="Cells")
    company_id = fields.Many2one(related='event_id.company_id', store=True, index=True)

    _event_unique = models.Constraint('UNIQUE(event_id)', 'An event has one comparison.')

    def write(self, vals):
        """Requirements: FR-SPM-0041: the evidence of an awarded event is not changed by a user."""
        sourcing.check_event_not_awarded(self.env, self.event_id)
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0041: the evidence of an awarded event is not deleted by a user."""
        sourcing.check_event_not_awarded(self.env, self.event_id)
        return super().unlink()
