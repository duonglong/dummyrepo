from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

# The days after its date during which the author may still change an evaluation.
EDIT_WINDOW_DAYS = 7


class ProcurementSupplierEvaluation(models.Model):
    """A qualitative evaluation of a supplier: criteria ratings and a comment, restricted to the buyer roles.

    Requirements: FR-SPM-0085, BR-SPM-0004
    Only its author changes it, for 7 days after its date; the supplier never sees it.
    """
    _name = 'procurement.supplier.evaluation'
    _description = 'Supplier Evaluation'
    _order = 'date desc, id desc'

    partner_id = fields.Many2one('res.partner', string="Supplier", required=True, ondelete='cascade', index=True)
    author_id = fields.Many2one('res.users', string="Author", required=True, default=lambda self: self.env.user,
                                ondelete='restrict', index=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    rating_ids = fields.One2many('procurement.supplier.evaluation.rating', 'evaluation_id', string="Ratings")
    comment = fields.Text()
    editable_until = fields.Date(compute='_compute_editable_until', store=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)

    @api.depends('date')
    def _compute_editable_until(self):
        """Requirements: FR-SPM-0085"""
        for evaluation in self:
            evaluation.editable_until = evaluation.date + timedelta(days=EDIT_WINDOW_DAYS) if evaluation.date else False

    def _spm_check_editable(self):
        """Refuse a change by anyone but the author, or after the window.

        Requirements: FR-SPM-0085
        """
        if self.env.su:
            return
        today = fields.Date.context_today(self)
        for evaluation in self:
            if evaluation.author_id != self.env.user:
                raise AccessError(self.env._("Only the author can change an evaluation."))
            if evaluation.editable_until and today > evaluation.editable_until:
                raise UserError(self.env._("An evaluation can only be changed within %s days of its date.",
                                           EDIT_WINDOW_DAYS))

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0085"""
        evaluations = super().create(vals_list)
        if not self.env.su and any(author != self.env.user for author in evaluations.author_id):
            raise AccessError(self.env._("An evaluation is recorded under your own name."))
        return evaluations

    def write(self, vals):
        """Requirements: FR-SPM-0085"""
        self._spm_check_editable()
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0085"""
        self._spm_check_editable()
        return super().unlink()
