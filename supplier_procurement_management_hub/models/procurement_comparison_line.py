from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services import sourcing
from ..services.sourcing import SCORE_MAX
from .procurement_sourcing_response import SPM_SCORE_GROUPS


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
    response_score = fields.Float(
        related='response_id.score', string="Weighted score", groups=SPM_SCORE_GROUPS)
    response_rank = fields.Integer(related='response_id.rank', string="Rank", groups=SPM_SCORE_GROUPS)
    raw_value = fields.Float(digits=(16, 4))
    raw_display = fields.Char()
    normalized_value = fields.Float(digits=(16, 4))
    score = fields.Float()
    is_best = fields.Boolean(string="Best")
    missing = fields.Boolean()
    manual_score = fields.Float()
    criterion_kind = fields.Selection(related='criterion_id.kind')
    event_state = fields.Selection(related='comparison_id.event_id.state')
    manual_comment = fields.Text()
    yes_no_value = fields.Selection(
        [('yes', 'Yes'), ('no', 'No')], string="Answer",
        help="The buyer's answer to a yes/no criterion; empty counts as no and is flagged missing.")

    @api.constrains('manual_score', 'manual_comment')
    def _check_manual_comment(self):
        """A manual score needs its comment.

        Requirements: FR-SPM-0035
        """
        for cell in self:
            if cell.manual_score and not (cell.manual_comment or '').strip():
                raise ValidationError(self.env._("A manual score needs a comment."))

    @api.constrains('manual_score')
    def _check_manual_score_range(self):
        """A manual score is a criterion score, from 0 to 100.

        Requirements: BR-SPM-0008
        """
        for cell in self:
            if not 0.0 <= cell.manual_score <= SCORE_MAX:
                raise ValidationError(self.env._("A manual score is between 0 and 100."))

    def unlink(self):
        """The cells of an awarded event are evidence and are never deleted.

        Requirements: FR-SPM-0041
        """
        sourcing.check_event_not_awarded(self.env, self.comparison_id.event_id)
        return super().unlink()

    def write(self, vals):
        """Manual scores are given while the event is open; once awarded the cells are evidence.

        Requirements: FR-SPM-0038, FR-SPM-0041, BR-SPM-0008
        """
        sourcing.check_event_not_awarded(self.env, self.comparison_id.event_id)
        if not self.env.su and {'manual_score', 'manual_comment', 'yes_no_value'}.intersection(vals) and any(
                cell.comparison_id.event_id.state != 'open' for cell in self):
            raise UserError(self.env._("Manual scores can only be given while the event is open."))
        return super().write(vals)
