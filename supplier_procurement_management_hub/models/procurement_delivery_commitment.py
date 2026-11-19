from contextlib import contextmanager
from datetime import datetime, time, timezone

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

from ..services import delivery


class ProcurementDeliveryCommitment(models.Model):
    """The delivery promise of one confirmed goods line: three dates, quantities read from receipts.

    Requirements: FR-SPM-0055, FR-SPM-0059, FR-SPM-0061, BR-SPM-0012
    Quantities and dates are computed from Odoo's own receipts; no stock move is ever written here.
    """
    _name = 'procurement.delivery.commitment'
    _description = 'Delivery Commitment'
    _order = 'agreed_date, id'

    order_line_id = fields.Many2one('purchase.order.line', string="Order line", required=True,
                                    ondelete='cascade', index=True, readonly=True)
    order_id = fields.Many2one(related='order_line_id.order_id', store=True, index=True)
    partner_id = fields.Many2one(related='order_line_id.partner_id', store=True, index=True)
    product_id = fields.Many2one(related='order_line_id.product_id', store=True)
    company_id = fields.Many2one(related='order_line_id.company_id', store=True, index=True)
    expected_date = fields.Datetime(related='order_line_id.date_planned', store=True)
    confirmed_date = fields.Datetime(string="Confirmed date", help="The date the supplier promised.")
    agreed_date = fields.Datetime(compute='_compute_agreed_date', store=True)
    actual_date = fields.Datetime(compute='_compute_receipt_facts', store=True)
    ordered_qty = fields.Float(compute='_compute_quantities', store=True, digits='Product Unit')
    received_qty = fields.Float(compute='_compute_quantities', store=True, digits='Product Unit')
    remaining_qty = fields.Float(compute='_compute_quantities', store=True, digits='Product Unit')
    is_partial = fields.Boolean(string="Partial", compute='_compute_quantities', store=True)
    has_backorder = fields.Boolean(string="Backorder", compute='_compute_receipt_facts', store=True)
    overdue_days = fields.Integer(compute='_compute_overdue_days')
    is_overdue = fields.Boolean(string="Overdue", compute='_compute_overdue_days', search='_search_is_overdue')
    receipt_ids = fields.Many2many('stock.picking', string="Receipts", compute='_compute_receipt_ids')
    state = fields.Selection([
        ('open', 'Open'), ('done', 'Received'), ('cancel', 'Cancelled'),
    ], compute='_compute_state', store=True)
    schedule_ids = fields.One2many('procurement.delivery.schedule', 'commitment_id', string="Schedules")
    proposal_ids = fields.One2many('procurement.delivery.proposal', 'commitment_id', string="Date proposals")
    discrepancy_ids = fields.One2many('procurement.delivery.discrepancy', 'commitment_id', string="Discrepancies")

    _order_line_unique = models.Constraint('UNIQUE(order_line_id)', 'An order line has one delivery commitment.')

    def _spm_rounding(self):
        """Return the rounding of the quantities, the product unit precision of the database."""
        return 10 ** -self.env['decimal.precision'].precision_get('Product Unit')

    @api.depends('confirmed_date', 'expected_date')
    def _compute_agreed_date(self):
        """Requirements: BR-SPM-0012"""
        for commitment in self:
            commitment.agreed_date = delivery.agreed(commitment.confirmed_date, commitment.expected_date)

    @api.depends('order_line_id.product_qty', 'order_line_id.qty_received')
    def _compute_quantities(self):
        """Read the quantities from the order line, which reads Odoo's receipts.

        Requirements: FR-SPM-0055, FR-SPM-0061
        """
        for commitment in self:
            line = commitment.order_line_id
            ordered = line.product_qty  # same unit as qty_received: the line's own
            received = line.qty_received
            rounding = commitment._spm_rounding()
            commitment.ordered_qty = ordered
            commitment.received_qty = received
            commitment.remaining_qty = max(ordered - received, 0.0)
            commitment.is_partial = (float_compare(received, 0.0, precision_rounding=rounding) > 0
                                     and float_compare(commitment.remaining_qty, 0.0, precision_rounding=rounding) > 0)

    @api.depends('order_line_id.move_ids.state', 'order_line_id.move_ids.date',
                 'order_line_id.move_ids.picking_id.backorder_id')
    def _compute_receipt_facts(self):
        """The date of the last done receipt and whether a backorder receipt is open.

        Requirements: FR-SPM-0055, FR-SPM-0059, FR-SPM-0061
        """
        for commitment in self:
            moves = commitment.order_line_id.move_ids.filtered(lambda m: not m._is_purchase_return())
            done = moves.filtered(lambda m: m.state == 'done')
            commitment.actual_date = max(done.mapped('date')) if done else False
            commitment.has_backorder = bool(moves.filtered(
                lambda m: m.state not in ('done', 'cancel') and m.picking_id.backorder_id))

    @api.depends('order_line_id.move_ids.picking_id')
    def _compute_receipt_ids(self):
        """Requirements: FR-SPM-0061"""
        for commitment in self:
            commitment.receipt_ids = commitment.order_line_id.move_ids.picking_id

    @api.depends('remaining_qty', 'order_id.state')
    def _compute_state(self):
        for commitment in self:
            if commitment.order_id.state == 'cancel':
                commitment.state = 'cancel'
            elif float_compare(commitment.remaining_qty, 0.0, precision_rounding=commitment._spm_rounding()) <= 0:
                commitment.state = 'done'
            else:
                commitment.state = 'open'

    @api.depends('agreed_date', 'remaining_qty', 'state')
    def _compute_overdue_days(self):
        """Days late of an open commitment with a remaining quantity whose agreed date is before today.

        Requirements: FR-SPM-0059
        """
        today = fields.Date.context_today(self)
        for commitment in self:
            agreed_day = commitment.agreed_date and fields.Date.context_today(self, commitment.agreed_date)
            remaining = commitment.remaining_qty if commitment.state == 'open' else 0.0
            commitment.overdue_days = delivery.overdue_days(
                agreed_day, today, remaining, commitment._spm_rounding())
            commitment.is_overdue = commitment.overdue_days > 0

    def _search_is_overdue(self, operator, value):
        """Search the overdue lines with the same day cut as the computed days (the user's day).

        Requirements: FR-SPM-0059
        """
        today = fields.Date.context_today(self)
        day_start = datetime.combine(today, time.min, tzinfo=self.env.tz).astimezone(timezone.utc)
        overdue = [('state', '=', 'open'), ('remaining_qty', '>', 0),
                   ('agreed_date', '<', day_start.replace(tzinfo=None))]
        if operator in ('in', 'not in'):
            positive = (operator == 'in') == (True in value)
        else:
            positive = (operator == '=') == bool(value)
        return overdue if positive else ['!'] + ['&'] * (len(overdue) - 1) + overdue

    def _spm_proposing_supplier_check(self):
        """Refuse unless the caller is a portal user of this commitment's own supplier, on a readable order.

        Requirements: FR-SPM-0025
        The id comes from the client: the order must exist, be readable by the caller, belong to an
        allowed company and to the caller's own supplier.
        """
        self.ensure_one()
        refused = AccessError(self.env._("You cannot propose a delivery date for this line."))
        return self.sudo().exists().order_id.with_env(self.env)._spm_require_own_supplier(refused)

    def spm_propose(self, reason, date=None, lines=None):
        """Store a pending date or split proposal of the supplier's portal user for the buyer.

        Requirements: FR-SPM-0025
        ``lines`` is a list of ``{'date', 'quantity'}``; the agreed dates stay as they are until the Buyer
        accepts. The order's buyer gets an activity.
        """
        order = self._spm_proposing_supplier_check()
        commitment = self.sudo()  # sudo: the supplier has no right on commitments; checked above
        reason = (reason or '').strip()
        if not reason:
            raise UserError(self.env._("Give the reason of the new date."))
        if order.state != 'purchase' or commitment.state != 'open':
            raise UserError(self.env._("A date can only be proposed for an open line of a confirmed order."))
        if bool(date) == bool(lines):
            raise UserError(self.env._("Propose one date, or split dates with their quantities."))
        if any(line['quantity'] <= 0 for line in lines or []):
            raise UserError(self.env._("Each part of a split needs a quantity above zero."))
        commitment.lock_for_update()  # a double submit waits here and then sees the first proposal
        commitment.invalidate_recordset(['proposal_ids'])
        if commitment.proposal_ids.filtered(lambda p: p.state == 'pending'):
            raise UserError(self.env._("A proposal for this line is already waiting for the buyer."))
        split = [(0, 0, {'date': fields.Datetime.to_datetime(line['date']), 'quantity': line['quantity']})
                 for line in lines or []]
        proposal = self.env['procurement.delivery.proposal'].sudo().create({  # sudo: a portal user has no create right
            'commitment_id': commitment.id, 'proposed_by_id': self.env.user.id, 'reason': reason,
            'date': fields.Datetime.to_datetime(date) if date else min(
                fields.Datetime.to_datetime(line['date']) for line in lines),
            'line_ids': split,
        })
        if order.user_id:
            proposal.activity_schedule(
                'mail.mail_activity_data_todo', user_id=order.user_id.id,
                summary=self.env._("Delivery date proposal for %(order)s", order=order.name), note=reason)
        return proposal

    def _check_schedule_total(self):
        """Refuse schedules whose quantities do not add up to the ordered quantity.

        Requirements: FR-SPM-0056
        """
        for commitment in self:
            if not commitment.schedule_ids:
                continue
            total = sum(commitment.schedule_ids.mapped('quantity'))
            if not delivery.schedules_match(total, commitment.ordered_qty,
                                            commitment._spm_rounding()):
                raise ValidationError(self.env._(
                    "The schedules total %(total)s of %(ordered)s ordered.",
                    total=f"{total:g}", ordered=f"{commitment.ordered_qty:g}"))

    @contextmanager
    def _spm_schedule_batch(self):
        """Mark these commitments as being written with all their schedules, checked once afterwards.

        The mark lives in the cursor, never in the context a client can send.
        """
        batch = self.env.cr.precommit.data.setdefault('spm_schedule_batch', set())
        added = set(self.ids) - batch
        batch |= added
        try:
            yield
        finally:
            batch -= added

    def _spm_check_outside_batch(self):
        """Check the schedule totals of the commitments that are not inside a commitment write.

        Requirements: FR-SPM-0056
        """
        batch = self.env.cr.precommit.data.get('spm_schedule_batch', ())
        self.filtered(lambda c: c.id not in batch)._check_schedule_total()

    @api.model_create_multi
    def create(self, vals_list):
        """Create the commitments, then their schedules, and check the totals.

        Requirements: FR-SPM-0056
        """
        schedule_commands = [vals.get('schedule_ids') for vals in vals_list]
        commitments = super().create([{k: v for k, v in vals.items() if k != 'schedule_ids'} for vals in vals_list])
        for commitment, commands in zip(commitments, schedule_commands):
            if commands:
                commitment.write({'schedule_ids': commands})
        return commitments

    def write(self, vals):
        """Check the schedules once the whole write is done, not after each of them.

        Requirements: FR-SPM-0056
        Only the confirmed date, the schedules and the discrepancies are edited by a person; the rest is read from the receipts.
        """
        if not self.env.su and set(vals) - {'confirmed_date', 'schedule_ids', 'discrepancy_ids'}:
            raise AccessError(self.env._("Only the confirmed date, the schedules and the discrepancies of a commitment can be changed."))
        if 'schedule_ids' not in vals:
            return super().write(vals)
        with self._spm_schedule_batch():
            result = super().write(vals)
        self._check_schedule_total()
        return result
