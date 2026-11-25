from datetime import datetime, time

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import approval, portal_access, supplier_control

ACK_FIELDS = {'spm_acknowledged_date', 'spm_acknowledged_user_id'}
APPROVAL_FIELDS = {'spm_controlled', 'spm_approval_state', 'spm_revision'}


class PurchaseOrder(models.Model):
    """Purchase order guarded by supplier control.

    Requirements: BR-SPM-0006, FR-SPM-0013, FR-SPM-0044, FR-SPM-0047, FR-SPM-0048, FR-SPM-0052
    Built spm_requester_id for T-018 (the requester of the approval policies).
    """
    _inherit = 'purchase.order'

    spm_sourcing_event_id = fields.Many2one(
        'procurement.sourcing.event', string="Sourcing event", copy=False, ondelete='set null',
        index='btree_not_null', check_company=True)
    spm_request_ids = fields.Many2many(
        'procurement.request', 'procurement_purchase_order_request_rel', 'order_id', 'request_id',
        string="Purchase requests", copy=False, check_company=True)
    spm_requester_id = fields.Many2one(
        'res.users', string="Requester", copy=False,
        help="The person the approval policies take the department and the managers from. "
             "Empty: the buyer of the order.")
    spm_controlled = fields.Boolean(string="Controlled", copy=False, readonly=True)
    spm_approval_state = fields.Selection([
        ('none', 'Not submitted'), ('not_required', 'No approval needed'), ('pending', 'To approve'),
        ('approved', 'Approved'), ('rejected', 'Rejected'),
    ], string="Approval", default='none', copy=False, readonly=True, index=True, tracking=True)
    spm_revision = fields.Integer(string="Revision", default=1, copy=False, readonly=True)
    spm_approval_request_ids = fields.One2many(
        'procurement.approval.request', 'order_id', string="Approval requests", copy=False, readonly=True)
    spm_delivery_event_ids = fields.One2many('procurement.delivery.event', 'order_id', string="Delivery history")
    spm_commitment_ids = fields.One2many('procurement.delivery.commitment', 'order_id', string="Delivery commitments")
    spm_acknowledged_date = fields.Datetime(string="Acknowledged on", copy=False, readonly=True)
    spm_acknowledged_user_id = fields.Many2one('res.users', string="Acknowledged by", copy=False,
                                               readonly=True, ondelete='set null')

    @api.model_create_multi
    def create(self, vals_list):
        """Give the goods lines of an order created already confirmed their delivery commitment.

        The requester is never filled here: only the purchase-request flow sets it, else the buyer is used.
        An acknowledgement is recorded only by the supplier's action, never set on a new order;
        nor are the approval fields, which only the approval flow writes.

        Requirements: FR-SPM-0055, FR-SPM-0024, FR-SPM-0048
        """
        if not self.env.su and any(ACK_FIELDS & set(vals) for vals in vals_list):
            raise AccessError(self.env._("Only the supplier acknowledges an order."))
        if not self.env.su and any(APPROVAL_FIELDS & set(vals) for vals in vals_list):
            raise AccessError(self.env._("The approval of an order is set only by its approval flow."))
        orders = super().create(vals_list)
        orders._spm_ensure_commitments()
        return orders

    def write(self, vals):
        """Give the goods lines of an order its delivery commitments when it becomes confirmed.

        Requirements: FR-SPM-0055, FR-SPM-0024, FR-SPM-0048, BR-SPM-0010, FR-SPM-0052
        A write of the confirmed state is refused for a controlled order that is not approved for its
        revision; a material change of the vendor, currency or terms of an order to approve or approved
        starts its approval again, once for the whole save together with its line changes: the order is compared
        with the snapshot of its newest request after the save.
        """
        if not self.env.su and ACK_FIELDS & set(vals):
            raise AccessError(self.env._("Only the supplier acknowledges an order."))
        if not self.env.su and APPROVAL_FIELDS & set(vals):
            raise AccessError(self.env._("The approval of an order is set only by its approval flow."))
        if vals.get('state') == 'purchase':
            approval.check_confirmable(self.filtered(lambda order: order.state != 'purchase'))
        watched = self.filtered(approval.is_watched)
        # the lines of a watched order are not reviewed one by one: the whole order is, once, after the save
        result = super(PurchaseOrder, self.with_context(spm_review_after_save=approval.REVIEW_DEFERRED) if watched else self).write(vals)
        approval.review_changes(watched)
        if vals.get('state') == 'purchase':
            self._spm_ensure_commitments()
        return result

    def _spm_ensure_commitments(self):
        """Create the missing delivery commitment of each goods line of the confirmed orders.

        Requirements: FR-SPM-0055, FR-SPM-0061
        """
        self.order_line._spm_ensure_commitment()

    def _spm_require_own_supplier(self, refusal):
        """Return the order (elevated) when the caller is a portal user of its supplier; raise ``refusal`` if not.

        Requirements: FR-SPM-0024, FR-SPM-0025
        The id comes from the client: the order must exist, be readable by the caller, belong to an
        allowed company and to the caller's own supplier.
        """
        order = self.sudo().exists()  # sudo: the id is checked against the caller's own rights just below
        if not order or not self.env.user.share or order.company_id not in self.env.companies \
                or not self.has_access('read'):
            raise refusal
        try:
            supplier = portal_access.current_supplier(self.env)
        except portal_access.SupplierAccessRefused as supplier_refusal:
            raise refusal from supplier_refusal
        if order.partner_id.commercial_partner_id != supplier:
            raise refusal
        return order

    def spm_acknowledge(self, dates):
        """Record the supplier's acknowledgement and the confirmed date of each line.

        Requirements: FR-SPM-0024
        ``dates`` maps an order line id to the confirmed date; a line left out is confirmed for its
        expected date. Called by a portal user of the order's own supplier on a confirmed order.
        """
        self.ensure_one()
        order = self._spm_require_own_supplier(AccessError(self.env._("You cannot acknowledge this order.")))
        order.lock_for_update()  # two posts of the same form wait for each other and see the first one's mark
        order.invalidate_recordset(['spm_acknowledged_date'])
        if order.state != 'purchase':
            raise UserError(self.env._("Only a confirmed order can be acknowledged."))
        if order.spm_acknowledged_date:
            raise UserError(self.env._("This order is already acknowledged; propose a new date for a line instead."))
        commitments = order.spm_commitment_ids
        unknown = set(dates or {}) - set(commitments.order_line_id.ids)
        if unknown:
            raise UserError(self.env._("A date was given for a line that is not on this order."))
        for commitment in commitments:  # sudo: the supplier has no write right; every check passed above
            confirmed = (dates or {}).get(commitment.order_line_id.id)
            commitment.write({'confirmed_date': commitment.order_line_id._convert_to_middle_of_day(
                datetime.combine(fields.Date.to_date(confirmed), time.min)) if confirmed else commitment.expected_date})
        order.write({  # sudo: see above
            'spm_acknowledged_date': fields.Datetime.now(), 'spm_acknowledged_user_id': self.env.user.id})
        order.message_post(body=self.env._("Order acknowledged by %(user)s.", user=self.env.user.name),
                           subtype_xmlid='mail.mt_note')
        return True

    def _spm_check_supplier(self, warn=True):
        """Refuse when the supplier of an order is not allowed to be confirmed with.

        Requirements: BR-SPM-0006, FR-SPM-0013
        """
        for order in self:
            supplier_control.check_supplier_allowed(
                self.env, order, order.partner_id, order.company_id, 'confirm', warn=warn)

    def button_confirm(self):
        """Refuse the confirmation when the supplier is not allowed or the approval is missing.

        Requirements: BR-SPM-0006, FR-SPM-0013, FR-SPM-0048
        """
        self._spm_check_supplier()
        approval.check_confirmable(self)
        # the approval step reached from this call checks again but does not repeat the warning
        return super(PurchaseOrder, self.with_context(spm_risk_warned=True)).button_confirm()

    def button_approve(self, force=False):
        """Refuse the approval when the supplier is not allowed (the order may wait since its confirmation)
        or when a controlled order is not approved for its revision, whatever ``force``.

        Requirements: BR-SPM-0006, FR-SPM-0013, FR-SPM-0048
        """
        self._spm_check_supplier(warn=not self.env.context.get('spm_risk_warned'))
        approval.check_confirmable(self)
        return super().button_approve(force=force)

    def _approval_allowed(self):
        """Let Odoo's own two-step validation pass an order approved by the hub's policies.

        Requirements: FR-SPM-0048
        """
        self.ensure_one()
        return (self.spm_controlled and approval.is_approved(self)) or super()._approval_allowed()

    def action_spm_submit_approval(self):
        """Submit the orders for approval under the company's policies.

        Requirements: FR-SPM-0047
        """
        for order in self:
            approval.submit(order)
        return True

    def action_spm_open_override_wizard(self):
        """Open the reason dialog to record an override for this order.

        Requirements: FR-SPM-0013
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Override supplier control"),
            'res_model': 'procurement.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_ids': self.ids,
                'default_action': 'override',
            },
        }

    def _get_share_message_domain(self):
        """Messages of the portal page: the supplier sees only those to or from its own company.

        Requirements: FR-SPM-0026
        """
        return portal_access.supplier_message_domain(
            self.env, super()._get_share_message_domain(),
            self.sudo().partner_id.commercial_partner_id)  # sudo: ids of the page's own supplier only


class PurchaseOrderLine(models.Model):  # sdd-allow: model-split the delivery commitment link predates the approval work; the contract and approval hooks sit in purchase_order_line.py
    """Order line carrying its delivery commitment.

    Requirements: FR-SPM-0055
    """
    _inherit = 'purchase.order.line'

    spm_commitment_ids = fields.One2many('procurement.delivery.commitment', 'order_line_id')

    @api.model_create_multi
    def create(self, vals_list):
        """Give a goods line added to a confirmed order its delivery commitment.

        Requirements: FR-SPM-0055
        """
        lines = super().create(vals_list)
        lines._spm_ensure_commitment()
        return lines

    def _spm_ensure_commitment(self):
        """Create the missing commitment of the goods lines of confirmed orders; nothing else is written.

        Requirements: FR-SPM-0055, FR-SPM-0061
        """
        lines = self.filtered(
            lambda line: line.order_id.state == 'purchase' and not line.display_type
            and line.product_id.type == 'consu' and not line.spm_commitment_ids)
        if lines:
            # a Buyer confirming an order has no create right on commitments, which only the system creates
            self.env['procurement.delivery.commitment'].sudo().create(
                [{'order_line_id': line.id} for line in lines])
