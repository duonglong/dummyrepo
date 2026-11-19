from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import sourcing

# Days between the creation of an event made from requests and its first deadline.
DEFAULT_DEADLINE_DAYS = 7
# Fields only the module's own actions write (they write with elevated rights after their checks).
WORKFLOW_FIELDS = ('state', 'cancel_reason')


class ProcurementSourcingEvent(models.Model):
    """A request for quotation sent to several invited suppliers at once.

    Requirements: FR-SPM-0031, FR-SPM-0032, FR-SPM-0033, FR-SPM-0042
    """
    _name = 'procurement.sourcing.event'
    _inherit = ['mail.thread']
    _description = 'Sourcing Event'
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(readonly=True, copy=False, default='New')
    title = fields.Char(required=True)
    buyer_id = fields.Many2one(
        'res.users', string="Buyer", required=True, default=lambda self: self.env.user,
        ondelete='restrict', index=True)
    state = fields.Selection([
        ('draft', 'Draft'), ('open', 'Open'), ('awarded', 'Awarded'), ('cancelled', 'Cancelled'),
    ], default='draft', required=True, copy=False, tracking=True, index=True)
    deadline = fields.Datetime(
        required=True, tracking=True, copy=False,
        default=lambda self: fields.Datetime.now() + timedelta(days=DEFAULT_DEADLINE_DAYS))
    line_ids = fields.One2many('procurement.sourcing.event.line', 'event_id', string="Lines", copy=True)
    criterion_ids = fields.One2many('procurement.sourcing.criterion', 'event_id', string="Criteria", copy=True)
    request_ids = fields.Many2many(
        'procurement.request', 'procurement_sourcing_event_request_rel', 'event_id', 'request_id',
        string="Purchase requests", copy=False, check_company=True)
    template_id = fields.Many2one('procurement.sourcing.template', string="Template", ondelete='set null', check_company=True)
    picking_type_id = fields.Many2one(
        'stock.picking.type', string="Delivery location", ondelete='restrict', check_company=True,
        domain="[('code', '=', 'incoming')]")
    terms = fields.Html(string="Commercial terms")
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(
        'res.currency', compute='_compute_currency_id', store=True, precompute=True, readonly=False,
        required=True)
    invited_partner_ids = fields.Many2many(
        'res.partner', 'procurement_sourcing_event_partner_rel', 'event_id', 'partner_id',
        string="Invited suppliers", domain="[('spm_is_supplier', '=', True)]", copy=False)
    supplier_file_ids = fields.Many2many(
        'ir.attachment', 'procurement_sourcing_event_file_rel', 'event_id', 'attachment_id',
        string="Files for suppliers", copy=False,
        help="The only files of this event that invited suppliers can see and download.")
    response_ids = fields.One2many('procurement.sourcing.response', 'event_id', string="Responses", copy=False)
    inform_unsuccessful = fields.Boolean(string="Inform unsuccessful suppliers")
    cancel_reason = fields.Text(readonly=True, copy=False)

    @api.depends('company_id')
    def _compute_currency_id(self):
        for event in self:
            event.currency_id = event.company_id.currency_id

    @api.onchange('template_id')
    def _onchange_template_id(self):
        """Copy the criteria and the settings of the template onto the event.

        Requirements: FR-SPM-0031
        """
        template = self.template_id
        if template:
            self.criterion_ids = [(5, 0, 0)] + [
                (0, 0, {'name': c.name, 'kind': c.kind, 'direction': c.direction,
                        'weight': c.weight, 'sequence': c.sequence})
                for c in template.criterion_ids]
            self.inform_unsuccessful = template.inform_unsuccessful

    @api.constrains('deadline')
    def _check_deadline_not_past(self):
        """Refuse a deadline in the past.

        Requirements: FR-SPM-0031
        """
        now = fields.Datetime.now()
        for event in self:
            if event.deadline and event.deadline < now:
                raise ValidationError(self.env._(
                    "The deadline %(deadline)s is in the past: choose a later date and time.",
                    deadline=fields.Datetime.to_string(event.deadline)))

    @api.model_create_multi
    def create(self, vals_list):
        """Number the event; only a draft is created by a user.

        Requirements: FR-SPM-0031
        """
        for vals in vals_list:
            if not self.env.su:
                vals.pop('cancel_reason', None)
                vals['state'] = 'draft'
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('procurement.sourcing.event') or 'New'
        events = super().create(vals_list)
        for event in events.filtered(lambda e: e.template_id and not e.criterion_ids):
            event._onchange_template_id()
        events._bind_supplier_files(events.supplier_file_ids)
        return events

    def _bind_supplier_files(self, files):
        """Attach the files shared with suppliers to their event, so every buyer of it sees them.

        Requirements: BR-SPM-0003
        A file added before the event's first save belongs to nobody; only the user's own
        files that belong to no other record may be shared.
        """
        for event in self:
            # sudo: reads who made each file and what it belongs to, to refuse a file of someone else
            for attachment in (files & event.supplier_file_ids).sudo():
                if attachment.res_model == self._name and attachment.res_id == event.id:
                    continue
                if (attachment.create_uid != self.env.user or attachment.res_id
                        or attachment.res_model not in (False, self._name)):
                    raise UserError(self.env._("A file shared with suppliers is not available."))
                attachment.write({'res_model': self._name, 'res_id': event.id})

    def write(self, vals):
        """Guard the workflow fields, and tell the suppliers when an open event's deadline moves.

        Requirements: FR-SPM-0033
        The state and the cancellation reason change only through the actions of the event;
        the old and new deadline are kept by the tracking of the field.
        """
        if not self.env.su and any(field in vals for field in WORKFLOW_FIELDS):
            raise AccessError(self.env._(
                "The status of a sourcing event changes with its Send and Cancel buttons."))
        if 'deadline' in vals and any(event.state in ('awarded', 'cancelled') for event in self):
            raise UserError(self.env._("The deadline of an awarded or cancelled event cannot change."))
        previous = {event: event.deadline for event in self}
        shared_before = self.supplier_file_ids
        result = super().write(vals)
        if 'supplier_file_ids' in vals:
            self._bind_supplier_files(self.supplier_file_ids - shared_before)
        if 'deadline' in vals:
            for event in self.filtered(lambda e: e.state == 'open' and e.deadline != previous[e]):
                sourcing.notify_suppliers(
                    self.env, event, sourcing.DEADLINE_TEMPLATE, event.response_ids.partner_id)
        return result

    def action_spm_send(self):
        """Send the event: one response per invited supplier, the invitation e-mail, then Open.

        Requirements: FR-SPM-0032
        Rights are the user's own; the state is written with elevated rights only after
        the draft, supplier and supplier control checks.
        """
        for event in self:
            event.check_access('write')
            if event.state != 'draft':
                raise UserError(self.env._("Only a draft event is sent."))
            if event.deadline <= fields.Datetime.now():
                raise UserError(self.env._(
                    "The deadline %(deadline)s has passed: set a later deadline before sending.",
                    deadline=fields.Datetime.to_string(event.deadline)))
            sourcing.check_invitable(self.env, event)
            suppliers = event.invited_partner_ids.commercial_partner_id
            self.env['procurement.sourcing.response'].create([
                {'event_id': event.id, 'partner_id': partner.id} for partner in suppliers])
            # sudo: the state is a workflow field no user writes directly; the checks are above
            event.sudo().write({'state': 'open'})
            sourcing.notify_suppliers(self.env, event, sourcing.INVITATION_TEMPLATE, suppliers)
        return True

    def action_spm_cancel(self, reason=None):
        """Cancel the event with a reason: suppliers are told, the requests are free again.

        Requirements: FR-SPM-0042
        """
        reason = (reason or '').strip()
        if not reason:
            raise UserError(self.env._("A reason is required to cancel a sourcing event."))
        for event in self:
            event.check_access('write')
            if event.state == 'awarded':
                raise UserError(self.env._("The event %s is already awarded and cannot be cancelled.", event.name))
            if event.state == 'cancelled':
                raise UserError(self.env._("The event %s is already cancelled.", event.name))
            # sudo: the state is a workflow field no user writes directly; the checks are above
            event.sudo().write({'state': 'cancelled', 'cancel_reason': reason})
            sourcing.notify_suppliers(
                self.env, event, sourcing.CANCEL_TEMPLATE, event.response_ids.partner_id)
            event._spm_release_requests()
        return True

    def _spm_release_requests(self):
        """Put the requests of the event that no other live event covers back to Submitted.

        Requirements: FR-SPM-0042
        """
        self.ensure_one()
        free = self.request_ids.filtered(lambda request: request.state == 'sourcing' and not any(
            other.state in ('draft', 'open', 'awarded') for other in request.sourcing_event_ids - self))
        free.write({'state': 'submitted'})

    def action_spm_open_cancel_wizard(self):
        """Open the dialog asking the cancellation reason."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Cancel sourcing event"),
            'res_model': 'procurement.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_ids': self.ids,
                'default_action': 'cancel',
            },
        }
