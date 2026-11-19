from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ProcurementComparisonLine(models.Model):
    """One cell of a comparison: a line, a criterion or the total of one response.

    Requirements: FR-SPM-0035, FR-SPM-0037
    """
    _name = 'procurement.comparison.line'
    _description = 'Quotation Comparison Cell'
    _order = 'row_kind, event_line_id, criterion_id, response_id'

    comparison_id = fields.Many2one('procurement.comparison', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='comparison_id.company_id', store=True, index=True)
    row_kind = fields.Selection([
        ('line', 'Unit price'), ('criterion', 'Criterion'), ('total', 'Total'),
    ], required=True)
    event_line_id = fields.Many2one('procurement.sourcing.event.line', ondelete='cascade')
    criterion_id = fields.Many2one('procurement.sourcing.criterion', ondelete='cascade')
    response_id = fields.Many2one('procurement.sourcing.response', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one(related='response_id.partner_id', string="Supplier")
    raw_value = fields.Float(digits=(16, 4))
    raw_display = fields.Char()
    normalized_value = fields.Float(digits=(16, 4))
    score = fields.Float()
    is_best = fields.Boolean(string="Best")
    missing = fields.Boolean()
    manual_score = fields.Float()
    manual_comment = fields.Text()

    @api.constrains('manual_score', 'manual_comment')
    def _check_manual_comment(self):
        """A manual score needs its comment.

        Requirements: FR-SPM-0035
        """
        for cell in self:
            if cell.manual_score and not (cell.manual_comment or '').strip():
                raise ValidationError(self.env._("A manual score needs a comment."))
