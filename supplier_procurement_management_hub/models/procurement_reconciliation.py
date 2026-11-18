from odoo import api, fields, models

from ..services import reconciliation


class ProcurementReconciliation(models.Model):
    """The matching of one vendor bill with its order and receipts.

    Requirements: FR-SPM-0071, FR-SPM-0072
    Results live here, never on the bill (ADR-SPM-006).
    """
    _name = 'procurement.reconciliation'
    _description = 'Bill Reconciliation'
    _order = 'refreshed_date desc, id desc'

    move_id = fields.Many2one('account.move', string="Vendor bill", required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one(related='move_id.partner_id', store=True)
    company_id = fields.Many2one(related='move_id.company_id', store=True, index=True)
    method = fields.Selection([('two_way', 'Two-way'), ('three_way', 'Three-way')], readonly=True)
    state = fields.Selection([
        ('matched', 'Matched'), ('review', 'To review'), ('error', 'Error'),
    ], required=True, default='matched', readonly=True)
    line_ids = fields.One2many('procurement.reconciliation.line', 'reconciliation_id', string="Lines")
    refreshed_date = fields.Datetime(readonly=True)

    _move_unique = models.Constraint('UNIQUE(move_id)', 'A vendor bill has one reconciliation.')

    def action_spm_run_matching(self):
        """Match the bill again.

        Requirements: FR-SPM-0072
        """
        self.move_id.action_spm_run_matching()
        return True

    @api.model
    def _spm_refresh(self, bills):
        """Match the bills; a thin entry point for models.

        Requirements: FR-SPM-0072
        """
        return reconciliation.refresh(self.env, bills)
