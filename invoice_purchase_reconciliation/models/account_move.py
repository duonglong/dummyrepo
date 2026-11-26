from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import reconciliation
from ..services.reconciliation import STATES
from .ipr_exception import EXCEPTION_KINDS

IPR_GUARDED_FIELDS = (
    'ipr_state', 'ipr_waiting_since', 'ipr_checked_date', 'ipr_checked_by_id',
    'ipr_decided_by_id', 'ipr_decided_date', 'ipr_decision_comment')

IPR_CHECK_GROUPS = 'account.group_account_invoice,purchase.group_purchase_user'


class AccountMove(models.Model):
    _inherit = 'account.move'

    ipr_state = fields.Selection(
        STATES, string='Reconciliation', required=True, default='not_checked',
        readonly=True, copy=False, index=True)
    ipr_waiting_since = fields.Datetime(
        string='Waiting Since', readonly=True, copy=False)
    ipr_checked_date = fields.Datetime(string='Checked On', readonly=True, copy=False)
    ipr_checked_by_id = fields.Many2one(
        'res.users', string='Checked By', readonly=True, copy=False, ondelete='set null')
    ipr_decided_by_id = fields.Many2one(
        'res.users', string='Decided By', readonly=True, copy=False, ondelete='set null')
    ipr_decided_date = fields.Datetime(string='Decided On', readonly=True, copy=False)
    ipr_decision_comment = fields.Text(string='Decision Comment', readonly=True, copy=False)
    ipr_check_line_ids = fields.One2many(
        'ipr.check.line', 'move_id', string='Comparison', copy=False, readonly=True)
    ipr_exception_ids = fields.One2many(
        'ipr.exception', 'move_id', string='Exceptions', copy=False, readonly=True)
    ipr_history_ids = fields.One2many(
        'ipr.history', 'move_id', string='Reconciliation History', copy=False, readonly=True)
    ipr_exception_count = fields.Integer(
        string='Exception Count', compute='_compute_ipr_exception_count')
    ipr_exception_kinds = fields.Char(
        string='Exception Kinds', compute='_compute_ipr_exception_kinds')
    ipr_in_scope = fields.Boolean(string='Reconciled', compute='_compute_ipr_in_scope')

    @api.depends('ipr_exception_ids')
    def _compute_ipr_exception_count(self):
        """Count the exceptions of the latest check.

        Requirements: FR-IPR-0010
        """
        for move in self:
            move.ipr_exception_count = len(move.ipr_exception_ids)

    @api.depends('ipr_exception_ids.kind')
    @api.depends_context('lang')
    def _compute_ipr_exception_kinds(self):
        """List the kinds of exception in the reader's language.

        Requirements: FR-IPR-0010
        """
        labels = dict(self.env['ipr.exception']._fields['kind']._description_selection(self.env))
        for move in self:
            present = set(move.ipr_exception_ids.mapped('kind'))
            kinds = [kind for kind, _label in EXCEPTION_KINDS if kind in present]
            move.ipr_exception_kinds = ', '.join(labels[kind] for kind in kinds) or False

    @api.depends('move_type')
    def _compute_ipr_in_scope(self):
        """Mark the documents the reconciliation applies to.

        Requirements: BR-IPR-0004
        """
        for move in self:
            move.ipr_in_scope = move._ipr_is_in_scope()

    def _ipr_is_in_scope(self):
        """Tell whether the bill is a vendor bill; credit notes and others are not checked.

        Requirements: BR-IPR-0004
        """
        self.ensure_one()
        return self.move_type == 'in_invoice'

    @api.model_create_multi
    def create(self, vals_list):
        """Refuse a status or decision value that does not come from a check or a decision.

        Requirements: FR-IPR-0010
        """
        if not self.env.su:
            for vals in vals_list:
                if any(vals.get(name) not in (None, False, 'not_checked')
                       for name in IPR_GUARDED_FIELDS):
                    raise UserError(self._ipr_status_write_message())
        return super().create(vals_list)

    def write(self, vals):
        """Refuse a change of the status or decision fields that does not come from the app.

        Requirements: FR-IPR-0010
        """
        if not self.env.su and any(name in vals for name in IPR_GUARDED_FIELDS):
            raise UserError(self._ipr_status_write_message())
        return super().write(vals)

    def _ipr_status_write_message(self):
        return self.env._('The reconciliation status can only be changed by a check or a decision.')

    def action_ipr_check(self):
        """Run the reconciliation check on draft vendor bills.

        Requirements: FR-IPR-0001, FR-IPR-0010
        """
        if not self.env.user.has_groups(IPR_CHECK_GROUPS):
            raise AccessError(self.env._('You are not allowed to run the reconciliation check.'))
        if any(not move._ipr_is_in_scope() for move in self):
            raise UserError(self.env._('Only vendor bills can be checked.'))
        if any(move.state != 'draft' for move in self):
            raise UserError(self.env._('Only draft bills can be checked.'))
        self._ipr_run_check()
        return True

    def _ipr_run_check(self):
        """Compare each bill with its orders and receipts and record the outcome.

        Takes the row lock on the bills itself, so every caller is covered.

        Requirements: FR-IPR-0001, FR-IPR-0003, FR-IPR-0004, FR-IPR-0005, FR-IPR-0006,
        FR-IPR-0010, FR-IPR-0020
        """
        self.lock_for_update()
        for move in self:
            tolerances = self.env['ipr.tolerance']._ipr_for_company(move.company_id)
            rows, exceptions = reconciliation.compare(
                move._ipr_comparison_inputs(), move._ipr_bill_tolerances(tolerances))
            # sudo: users may only read the comparison, only a check writes it
            move_sudo = move.sudo()
            exception_data = move_sudo._ipr_store_results(rows, exceptions)
            state = reconciliation.verdict(rows, [])
            move_sudo.write({
                'ipr_state': state,
                'ipr_checked_date': fields.Datetime.now(),
                'ipr_checked_by_id': self.env.user.id,
                'ipr_waiting_since': fields.Datetime.now() if state == 'waiting' else False,
            })
            move._ipr_add_history('check', state, exceptions=exception_data, tolerances=tolerances)

    def _ipr_bill_tolerances(self, tolerances):
        """Return the tolerances to compare with, the fixed price in the bill currency.

        Requirements: FR-IPR-0007
        """
        self.ensure_one()
        if tolerances['price_type'] == 'fixed':
            price_value = tolerances['currency']._convert(
                tolerances['price_amount'], self.currency_id, self.company_id,
                self.invoice_date or fields.Date.context_today(self), round=False)
        else:
            price_value = tolerances['price_percent']
        return {
            'price_type': tolerances['price_type'],
            'price_value': price_value,
            'qty_type': tolerances['qty_type'],
            'qty_value': tolerances['qty_value'],
        }

    def _ipr_comparison_inputs(self):
        """Read the product lines with their order lines in one batch.

        Requirements: FR-IPR-0001, FR-IPR-0006
        """
        self.ensure_one()
        date = self.invoice_date or fields.Date.context_today(self)
        lines = self.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
        seen = set()
        inputs = []
        for line in lines.sorted(lambda line: (line.sequence, line.id)):
            order_line = line.purchase_line_id
            vals = {
                'key': line.id, 'move_line': line, 'on_order': bool(order_line),
                'first_of_order_line': order_line.id not in seen,
                'ordered_qty': 0.0, 'received_qty': 0.0, 'billed_qty': 0.0,
                'ordered_price': 0.0, 'billed_price': 0.0,
            }
            seen.add(order_line.id)
            if order_line:
                uom = line.product_uom_id or order_line.uom_id
                vals.update({
                    'ordered_qty': order_line.product_qty,
                    'received_qty': order_line.qty_received,
                    'billed_qty': order_line.qty_invoiced,
                    'ordered_price': order_line.currency_id._convert(
                        order_line.price_unit_discounted, self.currency_id,
                        self.company_id, date, round=False),
                    'billed_price': uom._compute_price(
                        line.price_unit * (1 - line.discount / 100), order_line.uom_id),
                })
            inputs.append(vals)
        return inputs

    def _ipr_store_results(self, rows, exceptions):
        """Replace the comparison lines and exceptions of the bill; return the history data.

        Requirements: FR-IPR-0001, FR-IPR-0003, FR-IPR-0004
        """
        self.ensure_one()
        self.ipr_check_line_ids.unlink()
        self.ipr_exception_ids.unlink()
        check_lines = self.env['ipr.check.line'].create([{
            'move_id': self.id,
            'sequence': index,
            'move_line_id': row['move_line'].id,
            'purchase_line_id': row['move_line'].purchase_line_id.id,
            'product_id': row['move_line'].product_id.id,
            'name': row['move_line'].product_id.display_name or row['move_line'].name,
            'uom_id': row['move_line'].purchase_line_id.uom_id.id,
            'ordered_qty': row['ordered_qty'],
            'received_qty': row['received_qty'],
            'billed_qty': row['billed_qty'],
            'qty_difference': row['qty_difference'],
            'ordered_price': row['ordered_price'],
            'billed_price': row['billed_price'],
            'price_difference': row['price_difference'],
            'result': row['result'],
            'price_exception': row['price_exception'],
            'qty_exception': row['qty_exception'],
        } for index, row in enumerate(rows)])
        line_by_key = {row['key']: line for row, line in zip(rows, check_lines)}
        created = self.env['ipr.exception'].create([{
            'move_id': self.id,
            'kind': exception['kind'],
            'check_line_id': line_by_key[exception['key']].id,
        } for exception in exceptions])
        return [{
            'kind': record.kind,
            'product': record.check_line_id.name,
            'ordered_price': record.check_line_id.ordered_price,
            'billed_price': record.check_line_id.billed_price,
            'price_difference': record.check_line_id.price_difference,
            'received_qty': record.check_line_id.received_qty,
            'billed_qty': record.check_line_id.billed_qty,
            'qty_difference': record.check_line_id.qty_difference,
        } for record in created]

    def _ipr_add_history(self, event, state_after, comment=None, exceptions=None, tolerances=None):
        """Add one history entry per bill, copying what keeps it readable alone.

        Requirements: FR-IPR-0020
        """
        tolerances = tolerances or {}
        exceptions = exceptions or []
        vals_list = [{
            'move_id': move.id,
            'move_name': move.name if move.name and move.name != '/' else False,
            'partner_id': move.partner_id.id,
            'partner_name': move.partner_id.name,
            'bill_reference': move.ref,
            'company_id': move.company_id.id,
            'user_id': self.env.user.id,
            'event': event,
            'state_after': state_after,
            'comment': comment,
            'price_tolerance_type': tolerances.get('price_type'),
            'price_tolerance_percent': tolerances.get('price_percent'),
            'price_tolerance_amount': tolerances.get('price_amount'),
            'tolerance_currency_id': tolerances.get('currency') and tolerances['currency'].id,
            'qty_tolerance_type': tolerances.get('qty_type'),
            'qty_tolerance_value': tolerances.get('qty_value'),
            'exceptions_data': exceptions,
            'exception_count': len(exceptions),
        } for move in self]
        # sudo: no user holds a create right on the history, only this helper writes it
        return self.env['ipr.history'].sudo().create(vals_list)

    def unlink(self):
        """Record the deletion of a vendor bill that has history, then delete it.

        Requirements: FR-IPR-0022
        """
        # sudo: a user who deletes the bill may not hold the right to read its history
        with_history = self.sudo().filtered(
            lambda move: move.move_type == 'in_invoice' and move.ipr_history_ids)
        by_last_state = defaultdict(lambda: self.browse())
        for move in with_history:
            by_last_state[move.ipr_history_ids.sorted('id')[-1].state_after] |= move
        for last_state, moves in by_last_state.items():
            moves._ipr_add_history('delete', last_state, comment=self.env._('Bill deleted'))
        return super().unlink()
