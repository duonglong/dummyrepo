from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import reconciliation

# Changes of a bill that can alter its matching.
MATCHED_MOVE_FIELDS = {
    'invoice_line_ids', 'line_ids', 'partner_id', 'currency_id', 'invoice_date', 'date', 'ref',
    'fiscal_position_id', 'move_type', 'company_id',
}
MATCHED_LINE_FIELDS = {
    'quantity', 'price_unit', 'tax_ids', 'product_uom_id', 'product_id', 'purchase_line_id',
    'discount', 'display_type',
}
MATCH_STATES = [('unmatched', 'Unmatched'), ('matched', 'Matched'), ('review', 'To review'), ('error', 'Error')]


class AccountMove(models.Model):
    """Vendor bills show their matching; nothing is stored on them.

    Requirements: FR-SPM-0072
    """
    _inherit = 'account.move'

    spm_reconciliation_id = fields.Many2one(
        'procurement.reconciliation', string="Matching", compute='_compute_spm_match')
    spm_match_state = fields.Selection(
        MATCH_STATES, string="Matching state", compute='_compute_spm_match', search='_search_spm_match_state')

    @api.depends('state', 'move_type')
    def _compute_spm_match(self):
        """Requirements: FR-SPM-0072"""
        bills = self.filtered(lambda move: move.id and move.move_type in reconciliation.BILL_TYPES)
        found = {recon.move_id.id: recon for recon in self.env['procurement.reconciliation'].search(
            [('move_id', 'in', bills.ids)])}
        for move in self:
            recon = found.get(move.id)
            move.spm_reconciliation_id = recon
            if recon:
                move.spm_match_state = recon.state
            elif move in bills and move.state == 'draft':
                move.spm_match_state = 'unmatched'
            else:
                move.spm_match_state = False

    def _search_spm_match_state(self, operator, value):
        """Requirements: FR-SPM-0072"""
        if operator not in ('=', '!=') or value not in dict(MATCH_STATES):
            raise UserError(self.env._("Matching state can only be searched with equals."))
        recon_moves = self.env['procurement.reconciliation'].search([]).move_id.ids
        if value == 'unmatched':
            domain = [('move_type', 'in', list(reconciliation.BILL_TYPES)), ('state', '=', 'draft'),
                      ('id', 'not in', recon_moves)]
        else:
            matched = self.env['procurement.reconciliation'].search([('state', '=', value)]).move_id.ids
            domain = [('id', 'in', matched)]
        return domain if operator == '=' else ['!'] + domain

    def _spm_refresh_matching(self):
        """Match the draft vendor bills of the recordset that carry a purchase link.

        Requirements: FR-SPM-0072
        """
        bills = self.filtered(lambda move: move.move_type in reconciliation.BILL_TYPES and move.state == 'draft')
        if bills:
            reconciliation.refresh(self.env, bills)

    @api.model_create_multi
    def create(self, vals_list):
        """Match new draft vendor bills.

        Requirements: FR-SPM-0072
        """
        moves = super().create(vals_list)
        moves._spm_refresh_matching()
        return moves

    def write(self, vals):
        """Match the draft vendor bills again when a matched value changed.

        Requirements: FR-SPM-0072
        """
        result = super().write(vals)
        if MATCHED_MOVE_FIELDS & set(vals):
            self._spm_refresh_matching()
        return result

    def action_spm_run_matching(self):
        """Run the matching of the bill on demand.

        Requirements: FR-SPM-0072
        """
        self._spm_refresh_matching()
        return True


class AccountMoveLine(models.Model):
    """Editing a bill line matches its bill again.

    Requirements: FR-SPM-0072
    """
    _inherit = 'account.move.line'

    def write(self, vals):
        """Requirements: FR-SPM-0072"""
        result = super().write(vals)
        if MATCHED_LINE_FIELDS & set(vals):
            self.move_id._spm_refresh_matching()
        return result
