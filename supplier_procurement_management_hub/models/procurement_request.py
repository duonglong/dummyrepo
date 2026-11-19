from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import sourcing

BUYER_GROUP = 'supplier_procurement_management_hub.group_spm_buyer'
# Fields a Requester never writes directly: the workflow and the scope of the request.
BUYER_ONLY_FIELDS = ('state', 'requester_id', 'buyer_id', 'company_id')


class ProcurementRequest(models.Model):
    """A requester's purchase request, followed by its requester and handled by buyers.

    Requirements: FR-SPM-0030
    """
    _name = 'procurement.request'
    _inherit = ['mail.thread']
    _description = 'Purchase Request'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    requester_id = fields.Many2one(
        'res.users', required=True, default=lambda self: self.env.user, ondelete='restrict',
        index=True, copy=False)
    department_id = fields.Many2one('hr.department', ondelete='set null')
    needed_date = fields.Date(required=True)
    reason = fields.Text(required=True)
    line_ids = fields.One2many('procurement.request.line', 'request_id', string="Lines", copy=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('sourcing', 'In sourcing'),
        ('ordered', 'Ordered'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, copy=False, tracking=True, index=True)
    buyer_id = fields.Many2one('res.users', string="Buyer", ondelete='set null', copy=False)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company, index=True)
    sourcing_event_ids = fields.Many2many(
        'procurement.sourcing.event', 'procurement_sourcing_event_request_rel', 'request_id', 'event_id',
        string="Sourcing events", copy=False, bypass_search_access=True)

    def _spm_is_buyer(self) -> bool:
        return self.env.su or self.env.user.has_group(BUYER_GROUP)

    @api.model_create_multi
    def create(self, vals_list):
        """Number the request and keep a Requester to their own draft.

        Requirements: FR-SPM-0030
        """
        is_buyer = self._spm_is_buyer()
        for vals in vals_list:
            if not is_buyer:
                vals['requester_id'] = self.env.user.id
                vals['state'] = 'draft'
                vals.pop('buyer_id', None)
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('procurement.request') or 'New'
        return super().create(vals_list)

    def write(self, vals):
        """Refuse a Requester's direct change of the state or scope of a request.

        Requirements: FR-SPM-0030
        """
        if not self._spm_is_buyer() and any(field in vals for field in BUYER_ONLY_FIELDS):
            raise AccessError(self.env._("Only a Buyer changes the status or the scope of a request."))
        return super().write(vals)

    def _spm_buyers(self):
        """Return the users of the Buyer role who work in the request's company."""
        self.ensure_one()
        group = self.env.ref(BUYER_GROUP)
        # sudo: a Requester cannot read the members of the Buyer group; only the company filter is applied
        return group.sudo().all_user_ids.filtered(
            lambda user: user.active and self.company_id in user.company_ids)

    def action_spm_submit(self):
        """Submit the request and notify the buyers of its company.

        Requirements: FR-SPM-0030
        The requester (or a buyer) submits; the state is written with sudo only
        after that check, because a Requester has no right to write the state.
        """
        for request in self:
            request.check_access('write')
            if not (request.requester_id == self.env.user or self._spm_is_buyer()):
                raise AccessError(self.env._("Only the requester submits a request."))
            if request.state != 'draft':
                raise UserError(self.env._("Only a draft request is submitted."))
            if not request.line_ids:
                raise UserError(self.env._("Add at least one line to the request."))
            # sudo: a Requester has no right to write the state; the owner and state checks are above
            request.sudo().write({'state': 'submitted'})
            partners = request._spm_buyers().partner_id
            if partners:
                request.message_notify(
                    partner_ids=partners.ids,
                    subject=self.env._("New purchase request %s", request.name),
                    body=self.env._(
                        "%(requester)s submitted the purchase request %(name)s, needed by %(date)s.",
                        requester=request.requester_id.name, name=request.name,
                        date=request.needed_date))
        return True

    def action_spm_create_sourcing_event(self):
        """Create one sourcing event from the submitted requests and mark them In sourcing.

        Requirements: FR-SPM-0031
        Lines of the same product and unit are merged. The buyer's own rights apply to every
        request and to the new event.
        """
        if not self._spm_is_buyer():
            raise AccessError(self.env._("Only a Buyer creates a sourcing event."))
        if not self:
            raise UserError(self.env._("Select at least one submitted request."))
        self.check_access('write')
        if any(request.state != 'submitted' for request in self):
            raise UserError(self.env._("Only submitted requests are sourced: submit the draft ones first."))
        if len(self.company_id) > 1:
            raise UserError(self.env._("Requests of different companies cannot share a sourcing event."))
        event = self.env['procurement.sourcing.event'].create({
            'title': ", ".join(self.mapped('name')),
            'company_id': self.company_id.id,
            'request_ids': [(6, 0, self.ids)],
            'line_ids': [(0, 0, values) for values in sourcing.merge_request_lines(self)],
        })
        self.write({'state': 'sourcing'})
        return {
            'type': 'ir.actions.act_window', 'res_model': event._name, 'res_id': event.id,
            'view_mode': 'form', 'target': 'current',
        }
