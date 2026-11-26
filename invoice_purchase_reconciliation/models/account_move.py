import logging
from collections import defaultdict

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_datetime

from ..services import reconciliation
from ..services.reconciliation import STATES
from .ipr_exception import EXCEPTION_KINDS

_logger = logging.getLogger(__name__)

IPR_GUARDED_FIELDS = (
    'ipr_state', 'ipr_waiting_since', 'ipr_checked_date', 'ipr_checked_by_id',
    'ipr_decided_by_id', 'ipr_decided_date', 'ipr_decision_comment')

IPR_CHECK_GROUPS = 'account.group_account_invoice,purchase.group_purchase_user'
IPR_HELD_STATES = ('waiting', 'rejected')
IPR_DECISION_GROUPS = 'purchase.group_purchase_manager,account.group_account_manager'


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
    ipr_can_decide = fields.Boolean(string='Can Decide', compute='_compute_ipr_can_decide')

    @api.depends('ipr_state', 'create_uid')
    @api.depends_context('uid')
    def _compute_ipr_can_decide(self):
        """Tell whether the current user may approve or reject the bill now.

        Requirements: BR-IPR-0003, FR-IPR-0023
        """
        is_approver = self.env.user.has_groups(IPR_DECISION_GROUPS)
        for move in self:
            move.ipr_can_decide = (
                is_approver and move.ipr_state == 'waiting'
                and move.create_uid != self.env.user)

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

        A change of the supplier, reference or currency of a checked draft bill that really
        differs from the stored value sends the bill back to Not checked.

        Requirements: FR-IPR-0010, FR-IPR-0018
        """
        if not self.env.su and any(name in vals for name in IPR_GUARDED_FIELDS):
            raise UserError(self._ipr_status_write_message())
        if not reconciliation.WATCHED_MOVE_FIELDS_SET.intersection(vals):
            return super().write(vals)
        names = reconciliation.WATCHED_MOVE_FIELDS
        before = {move: [move[name] for name in names] for move in self._ipr_resettable()}
        result = super().write(vals)
        for move, old in before.items():
            if old != [move[name] for name in names]:
                move._ipr_reset()
        return result

    def _ipr_resettable(self):
        """Return the checked draft vendor bills: the ones a change makes obsolete.

        Requirements: FR-IPR-0018
        """
        return self.filtered(
            lambda move: move.state == 'draft' and move._ipr_is_in_scope()
            and move.ipr_state != 'not_checked')

    def _ipr_reset(self):
        """Set the checked draft bills back to Not checked and record one 'reset' entry each.

        Bills already Not checked are left alone, so one write adds one entry per bill.

        Requirements: FR-IPR-0018, FR-IPR-0020
        """
        bills = self._ipr_resettable()
        if not bills:
            return
        # sudo: users may not write the status nor the results, only a reset clears them
        bills_sudo = bills.sudo()
        bills_sudo.ipr_exception_ids.unlink()
        bills_sudo.ipr_check_line_ids.unlink()
        bills_sudo.write({
            'ipr_state': 'not_checked',
            'ipr_waiting_since': False,
            'ipr_decided_by_id': False,
            'ipr_decided_date': False,
            'ipr_decision_comment': False,
        })
        bills._ipr_add_history(
            'reset', 'not_checked', comment=self.env._('The bill changed after the check.'))

    def _ipr_status_write_message(self):
        return self.env._('The reconciliation status can only be changed by a check or a decision.')

    def _ipr_held(self):
        """Return the vendor bills whose exceptions are waiting or were rejected.

        Requirements: FR-IPR-0016, FR-IPR-0017
        """
        return self.filtered(
            lambda move: move._ipr_is_in_scope() and move.ipr_state in IPR_HELD_STATES)

    def _ipr_bill_label(self):
        """Name a bill for a message: its number, else its reference, else its display name."""
        self.ensure_one()
        return self.name if self.name and self.name != '/' else (self.ref or self.display_name)

    def _ipr_prepare_post(self):
        """Lock and check the Not checked vendor bills of the selection; return the held bills.

        The results stay: this step raises nothing on a Waiting bill.

        Requirements: FR-IPR-0002, FR-IPR-0016
        """
        bills = self.filtered(lambda move: move.state == 'draft' and move._ipr_is_in_scope())
        if not bills:
            return bills
        bills.lock_for_update()
        bills.invalidate_recordset(IPR_GUARDED_FIELDS)
        bills.filtered(lambda move: move.ipr_state == 'not_checked')._ipr_run_check()
        return bills._ipr_held()

    def _ipr_hold_notification(self, fresh_only=False):
        """Check the Not checked bills; return the 'not posted' notification when a bill is held.

        The notification names each held bill with its open exceptions and the results of the
        check stay: nothing is raised. With fresh_only, only a bill this call has just checked
        triggers it; a bill already held is then left to the posting guard, which refuses.

        Requirements: FR-IPR-0002, FR-IPR-0016
        """
        fresh = self.filtered(lambda move: move.ipr_state == 'not_checked')
        held = self._ipr_prepare_post()
        if not (held & fresh if fresh_only else held):
            return False
        bills = ', '.join(
            '%s (%s)' % (move._ipr_bill_label(), move.ipr_exception_kinds or '') for move in held)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning',
                'sticky': True,
                'message': self.env._(
                    "Not posted: %(bills)s wait for a manager's approval.", bills=bills),
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_post(self):
        """Check the Not checked vendor bills, then post only when none is held.

        Requirements: FR-IPR-0002, FR-IPR-0016
        """
        return self._ipr_hold_notification() or super().action_post()

    def _post(self, soft=True):
        """Refuse to post a vendor bill whose exceptions are not approved, whatever the route.

        Requirements: FR-IPR-0002, FR-IPR-0016
        """
        drafts = self.filtered(lambda move: move.state == 'draft' and move._ipr_is_in_scope())
        drafts.filtered(lambda move: move.ipr_state == 'not_checked')._ipr_run_check()
        held = drafts._ipr_held()
        if held:
            raise UserError('\n'.join(
                self.env._(
                    '%(bill)s cannot be posted: its reconciliation exceptions are not approved (%(exceptions)s).',
                    bill=move._ipr_bill_label(), exceptions=move.ipr_exception_kinds or '')
                for move in held))
        return super()._post(soft=soft)

    @api.model
    def _autopost_draft_entries(self, batch_size=100):
        """Check the due Not checked vendor bills, one savepoint each, before Odoo posts them.

        Requirements: FR-IPR-0002, FR-IPR-0016
        """
        due = self.search([
            ('state', '=', 'draft'),
            ('date', '<=', fields.Date.context_today(self)),
            ('auto_post', '!=', 'no'),
            ('move_type', '=', 'in_invoice'),
            ('ipr_state', '=', 'not_checked'),
        ], limit=batch_size)
        for move in due:
            # the job's own try-lock: a bill someone is editing waits for the next run
            move.invalidate_recordset(IPR_GUARDED_FIELDS)
            move = move.try_lock_for_update().filtered(lambda m: m.ipr_state == 'not_checked')
            if move:
                try:
                    with self.env.cr.savepoint():
                        move._ipr_run_check()
                except Exception:  # noqa: BLE001 - one bill's failed check must not stop the job
                    _logger.error(
                        'Reconciliation check failed for bill %s of company %s',
                        move.id, move.company_id.id, exc_info=True)
            # keep the check: Odoo rolls back when it cannot post a held bill
            self.env['ir.cron']._commit_progress()
        return super()._autopost_draft_entries(batch_size=batch_size)

    def _ipr_refuse_payment(self):
        """Refuse a payment when a vendor bill of the selection is held; name the held bills.

        Requirements: FR-IPR-0017
        """
        held = self._ipr_held()
        if held:
            raise UserError(self.env._(
                'Payment refused: %(bills)s wait for approval of their reconciliation exceptions or were rejected.',
                bills=', '.join(held.mapped(lambda move: move._ipr_bill_label()))))

    def action_force_register_payment(self):
        """Refuse to register a payment for a held vendor bill.

        Requirements: FR-IPR-0017
        """
        self._ipr_refuse_payment()
        return super().action_force_register_payment()

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
        FR-IPR-0009, FR-IPR-0010, FR-IPR-0020
        """
        self.lock_for_update()
        for move in self:
            tolerances = self.env['ipr.tolerance']._ipr_for_company(move.company_id)
            rows, exceptions = reconciliation.compare(
                move._ipr_comparison_inputs(), move._ipr_bill_tolerances(tolerances))
            # sudo: users may only read the comparison, only a check writes it
            move_sudo = move.sudo()
            duplicates = move._ipr_find_duplicates()
            exception_data = move_sudo._ipr_store_results(rows, exceptions, duplicates)
            state = reconciliation.verdict(rows, duplicates)
            move_sudo.write({
                'ipr_state': state,
                'ipr_checked_date': fields.Datetime.now(),
                'ipr_checked_by_id': self.env.user.id,
                'ipr_waiting_since': fields.Datetime.now() if state == 'waiting' else False,
            })
            move._ipr_add_history('check', state, exceptions=exception_data, tolerances=tolerances)

    def action_ipr_approve(self, comment=None):
        """Approve the exceptions of the bill; the comment is optional.

        Requirements: BR-IPR-0003, FR-IPR-0012, FR-IPR-0014, FR-IPR-0015
        """
        return self._ipr_decide('approve', comment)

    def action_ipr_reject(self, reason):
        """Reject the exceptions of the bill; a reason is required.

        Requirements: BR-IPR-0003, FR-IPR-0013, FR-IPR-0014, FR-IPR-0015
        """
        return self._ipr_decide('reject', reason)

    def _ipr_decide(self, decision, comment):
        """Check the caller, the creator and the state on the server, then record the decision.

        The row lock makes a concurrent second decision wait for the first and see its result.

        Requirements: BR-IPR-0003, FR-IPR-0012, FR-IPR-0013, FR-IPR-0014, FR-IPR-0015
        """
        self.ensure_one()
        if not self.env.user.has_groups(IPR_DECISION_GROUPS):
            raise AccessError(self.env._(
                'Only a purchase or accounting manager can approve or reject reconciliation exceptions.'))
        if self.create_uid == self.env.user:
            raise UserError(self.env._('You entered this bill: another manager must decide.'))
        comment = (comment or '').strip()
        if decision == 'reject' and not comment:
            raise UserError(self.env._('Give a reason for the rejection.'))
        self.lock_for_update()
        self.invalidate_recordset(IPR_GUARDED_FIELDS)
        if self.ipr_state != 'waiting':
            raise UserError(self._ipr_not_waiting_message())
        state = 'approved' if decision == 'approve' else 'rejected'
        # sudo: approvers hold no write right on the status, only a decision changes it
        self.sudo().write({
            'ipr_state': state,
            'ipr_decided_by_id': self.env.user.id,
            'ipr_decided_date': fields.Datetime.now(),
            'ipr_decision_comment': comment or False,
        })
        self._ipr_add_history(decision, state, comment=comment or None)
        return True

    def _ipr_not_waiting_message(self):
        """Name the reason a decision is refused on a bill that no longer waits.

        Requirements: FR-IPR-0015
        """
        self.ensure_one()
        if self.ipr_state == 'not_checked':
            return self.env._('This bill was changed and must be checked again.')
        if not self.ipr_decided_by_id:
            return self.env._('This bill is not waiting for approval.')
        return self.env._(
            'This bill was already decided by %(user)s on %(date)s.',
            user=self.ipr_decided_by_id.name,
            date=format_datetime(self.env, self.ipr_decided_date))

    def _ipr_find_duplicates(self):
        """Return the other vendor bills of the company with the same supplier and reference.

        One query on the bill's company and supplier company; cancelled bills and bills
        without a reference never match, and references compare without case or edge spaces.

        Requirements: BR-IPR-0002, FR-IPR-0009
        """
        self.ensure_one()
        reference = reconciliation.normalize_ref(self.ref)
        if not reference:
            return self.browse()
        self.env.flush_all()
        self.env.cr.execute("""
            SELECT id
              FROM account_move
             WHERE move_type = 'in_invoice'
               AND company_id = %s
               AND commercial_partner_id = %s
               AND state != 'cancel'
               AND id != %s
               AND lower(btrim(ref)) = %s
             ORDER BY id""", (self.company_id.id, self.commercial_partner_id.id, self.id, reference))
        return self.browse([row[0] for row in self.env.cr.fetchall()])

    def init(self):
        """Index the vendor bills by company, supplier and normalised reference.

        Requirements: FR-IPR-0009
        """
        super().init()
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS account_move_ipr_duplicate_ref_idx
                ON account_move (company_id, commercial_partner_id, lower(btrim(ref)))
             WHERE move_type = 'in_invoice'""")

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

    def _ipr_store_results(self, rows, exceptions, duplicates):
        """Replace the comparison lines and exceptions of the bill; return the history data.

        Requirements: FR-IPR-0001, FR-IPR-0003, FR-IPR-0004, FR-IPR-0009
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
        if duplicates:
            created |= self.env['ipr.exception'].create({
                'move_id': self.id,
                'kind': 'duplicate',
                'duplicate_move_ids': [Command.set(duplicates.ids)],
            })
        return [{
            'kind': record.kind,
            'product': record.check_line_id.name,
            'ordered_price': record.check_line_id.ordered_price,
            'billed_price': record.check_line_id.billed_price,
            'price_difference': record.check_line_id.price_difference,
            'received_qty': record.check_line_id.received_qty,
            'billed_qty': record.check_line_id.billed_qty,
            'qty_difference': record.check_line_id.qty_difference,
            'other_bills': record.duplicate_move_ids.mapped('display_name'),
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
        # the lines go with the bill: that is a deletion, not a change to reset on
        return super(AccountMove, self.with_context(ipr_skip_reset=True)).unlink()
