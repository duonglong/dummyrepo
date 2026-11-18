from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import reconciliation
from .procurement_exception_route import EXCEPTION_KINDS
from .procurement_reconciliation_exception import EXCEPTION_STATES, RELEASING_STATES

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
    spm_open_exception_count = fields.Integer(string="Open exceptions", compute='_compute_spm_match')
    spm_possible_duplicate_ids = fields.Many2many(
        'account.move', string="Possible duplicates", compute='_compute_spm_match')

    @api.depends('state', 'move_type')
    def _compute_spm_match(self):
        """Requirements: FR-SPM-0072"""
        bills = self.filtered(lambda move: move.id and move.move_type in reconciliation.BILL_TYPES)
        # sudo: the match state is the hub's own record, shown to every bill reader
        found = {recon.move_id.id: recon for recon in self.env['procurement.reconciliation'].sudo().search(
            [('move_id', 'in', bills.ids)])}
        for move in self:
            recon = found.get(move.id)
            move.spm_reconciliation_id = recon
            move.spm_open_exception_count = len(recon.exception_ids.filtered(
                lambda exc: exc.state in ('open', 'in_review'))) if recon else 0
            move.spm_possible_duplicate_ids = recon.duplicate_move_ids if recon else False
            if recon:
                move.spm_match_state = recon.state
            elif move in bills and move.state == 'draft':
                move.spm_match_state = 'unmatched'
            else:
                move.spm_match_state = False

    def _post(self, soft=True):
        """Refuse to post a bill that holds an exception its company wants approved.

        Requirements: FR-SPM-0077, BR-SPM-0016
        Only the posting is held; Odoo's own posting then runs unchanged.
        """
        # sudo: the posting user may lack access to the hub's exceptions; only the held bills are read
        held = self.env['procurement.reconciliation.exception'].sudo().search([
            ('move_id', 'in', self.ids), ('requires_approval', '=', True),
            ('state', 'not in', RELEASING_STATES)])
        if held:
            lines = [self.env._("%(kind)s (%(state)s) on %(bill)s", kind=dict(EXCEPTION_KINDS)[exc.kind],
                                state=dict(EXCEPTION_STATES)[exc.state], bill=exc.move_id.display_name)
                     for exc in held]
            raise UserError(self.env._(
                "These exceptions need approval before the bill can be posted:\n%s", "\n".join(lines)))
        return super()._post(soft=soft)

    def _search_spm_match_state(self, operator, value):
        """Requirements: FR-SPM-0072"""
        if operator not in ('=', '!=') or value not in dict(MATCH_STATES):
            raise UserError(self.env._("Matching state can only be searched with 'is' or 'is not'."))
        # sudo: only ids of the hub's own rows reach the domain
        recons = self.env['procurement.reconciliation'].sudo()
        if value == 'unmatched':
            domain = [('move_type', 'in', list(reconciliation.BILL_TYPES)), ('state', '=', 'draft'),
                      ('id', 'not in', recons._search([]).subselect('move_id'))]
        else:
            domain = [('id', 'in', recons._search([('state', '=', value)]).subselect('move_id'))]
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
        result = super(AccountMove, self.with_context(spm_skip_line_matching=True)).write(vals)
        if MATCHED_MOVE_FIELDS & set(vals):
            self._spm_refresh_matching()
        return result

    def action_spm_run_matching(self):
        """Run the matching of the bill on demand.

        Requirements: FR-SPM-0072
        """
        if not self.env.user.has_group(
                'supplier_procurement_management_hub.group_spm_finance') and not self.env.user.has_group(
                'supplier_procurement_management_hub.group_spm_manager'):
            raise AccessError(self.env._("You are not allowed to run the matching."))
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
        if MATCHED_LINE_FIELDS & set(vals) and not self.env.context.get('spm_skip_line_matching'):
            self.move_id._spm_refresh_matching()
        return result
