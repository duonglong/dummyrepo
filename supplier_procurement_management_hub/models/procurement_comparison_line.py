from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class ProcurementComparisonLine(models.Model):
    """One cell of a comparison: a line, a criterion or the total of one response.

    Requirements: FR-SPM-0035, FR-SPM-0037
    """
    _name = 'procurement.comparison.line'
    _description = 'Quotation Comparison Cell'
    _order = 'row_kind, event_line_id, criterion_id, term_key, response_id'

    comparison_id = fields.Many2one('procurement.comparison', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='comparison_id.company_id', store=True, index=True)
    row_kind = fields.Selection([
        ('line', 'Unit price'), ('criterion', 'Criterion'), ('total', 'Total'), ('term', 'Term'),
    ], required=True)
    term_key = fields.Selection([
        ('total_cost', 'Total cost'), ('lead_time', 'Lead time'), ('payment_terms', 'Payment terms'),
        ('warranty', 'Warranty (months)'), ('validity', 'Valid until'),
    ])
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

    def write(self, vals):
        """Manual scores are given while the event is open; once awarded they are evidence.

        Requirements: FR-SPM-0038
        """
        if not self.env.su and ('manual_score' in vals or 'manual_comment' in vals) and any(
                cell.comparison_id.event_id.state != 'open' for cell in self):
            raise UserError(self.env._("Manual scores can only be given while the event is open."))
        return super().write(vals)
