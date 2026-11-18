from odoo import api, fields, models
from odoo.addons.base.models.res_partner_bank import sanitize_account_number
from odoo.exceptions import AccessError, UserError

from ..services import audit, onboarding
from .procurement_supplier_change import SPM_RESTRICTED_GROUPS

# Bank and tax data: the restricted roles and the Buyer (BR-SPM-0004).
SPM_APPLICATION_RESTRICTED_GROUPS = (
    SPM_RESTRICTED_GROUPS + ',supplier_procurement_management_hub.group_spm_buyer')
INVITATION_TEMPLATE = 'supplier_procurement_management_hub.mail_template_supplier_invitation'
CHANGES_TEMPLATE = 'supplier_procurement_management_hub.mail_template_supplier_changes_requested'
REJECTION_TEMPLATE = 'supplier_procurement_management_hub.mail_template_supplier_rejected'
MANAGER_GROUP = 'supplier_procurement_management_hub.group_spm_manager'


class ProcurementSupplierApplication(models.Model):
    """An onboarding request filled by a supplier through a single-use link or by a buyer.

    Requirements: FR-SPM-0001, FR-SPM-0002, FR-SPM-0003
    """
    _name = 'procurement.supplier.application'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Supplier Application'
    _order = 'id desc'

    name = fields.Char(compute='_compute_name', store=True)
    state = fields.Selection([
        ('invited', 'Invited'),
        ('submitted', 'Submitted'),
        ('changes_requested', 'Changes requested'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], default='invited', required=True, copy=False, tracking=True, index=True)
    user_id = fields.Many2one(
        'res.users', string="Responsible buyer", default=lambda self: self.env.user, ondelete='set null')
    submitted_date = fields.Datetime(copy=False, readonly=True)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True, index=True)
    partner_id = fields.Many2one('res.partner', string="Supplier", copy=False, ondelete='restrict')
    is_requalification = fields.Boolean(
        string="Re-qualification", copy=False, readonly=True,
        help="Set when the application re-qualifies a supplier that is already approved.")
    change_request_comment = fields.Text(copy=False, readonly=True)
    rejection_reason = fields.Text(copy=False, readonly=True)

    email = fields.Char(required=True)
    invitation_token_hash = fields.Char(copy=False, groups='base.group_system')
    invitation_expiry = fields.Datetime(copy=False)
    invitation_used_date = fields.Datetime(copy=False)

    supplier_type_id = fields.Many2one(
        'procurement.supplier.type', required=True, ondelete='restrict', tracking=True)
    legal_name = fields.Char()
    street = fields.Char()
    city = fields.Char()
    zip = fields.Char()
    country_id = fields.Many2one('res.country', ondelete='restrict')
    phone = fields.Char()
    website = fields.Char()
    product_category_ids = fields.Many2many('product.category', string="Product categories")
    operating_locations = fields.Text()
    contact_ids = fields.One2many('procurement.supplier.application.contact', 'application_id')
    document_ids = fields.One2many('procurement.supplier.document', 'application_id')
    vat = fields.Char(string="Tax identifier", groups=SPM_APPLICATION_RESTRICTED_GROUPS)
    bank_account_number = fields.Char(groups=SPM_APPLICATION_RESTRICTED_GROUPS)
    bank_name = fields.Char(groups=SPM_APPLICATION_RESTRICTED_GROUPS)
    bank_bic = fields.Char(groups=SPM_APPLICATION_RESTRICTED_GROUPS)
    bank_holder = fields.Char(groups=SPM_APPLICATION_RESTRICTED_GROUPS)
    field_rules = fields.Json(compute='_compute_field_rules')

    @api.depends('legal_name', 'email')
    def _compute_name(self):
        for application in self:
            application.name = application.legal_name or application.email

    @api.depends('supplier_type_id.field_ids.field_key', 'supplier_type_id.field_ids.visibility')
    def _compute_field_rules(self):
        for application in self:
            application.field_rules = application.spm_field_rules()

    # --- write guard --------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        """Refuse a workflow or invitation field in the values of a new application.

        Requirements: FR-SPM-0003
        """
        if not self.env.su:
            # A context default is added inside super().create, after the check: drop it for a user.
            context = {key: value for key, value in self.env.context.items()
                       if not (key.startswith('default_') and key[len('default_'):] in onboarding.WORKFLOW_FIELDS)}
            self = self.with_context(context)
        for vals in vals_list:
            self._spm_refuse_workflow_values(vals, creating=True)
        return super(ProcurementSupplierApplication, self).create(vals_list)

    def _spm_refuse_workflow_values(self, vals, creating=False):
        """Raise when ``vals`` hold a workflow or invitation field and the caller is not elevated.

        On creation the starting value of a field (the state Invited, an empty field) is accepted.
        """
        if self.env.su:
            return
        starting = {'state': 'invited'}
        if any(not creating or vals[name] != starting.get(name, False)
               for name in onboarding.WORKFLOW_FIELDS if name in vals):
            raise AccessError(self.env._(
                "The state, the invitation and the supplier link of an application change through its actions only."))

    def write(self, vals):
        """Refuse direct writes of the workflow and invitation fields, and any write once closed.

        Only the module's own actions change them, with elevated rights after their access check.

        Requirements: FR-SPM-0001, FR-SPM-0003
        """
        self._spm_refuse_workflow_values(vals)
        self._spm_check_open()
        return super().write(vals)

    def _spm_check_open(self):
        """Refuse a change of an approved or rejected application, unless the caller is elevated.

        Requirements: FR-SPM-0003
        """
        if not self.env.su and any(application.state in onboarding.CLOSED_STATES for application in self):
            raise UserError(self.env._("An approved or rejected application cannot be changed."))

    # --- field rules --------------------------------------------------------

    def spm_field_rules(self):
        """Return ``{field_key: 'shown' | 'required' | 'hidden'}`` for the type of the application.

        Requirements: FR-SPM-0002
        """
        self.ensure_one()
        keys = [key for key, _label in self.env['procurement.supplier.type.field']._fields['field_key'].selection]
        rules = dict.fromkeys(keys, 'shown')
        for rule in self.supplier_type_id.field_ids:
            rules[rule.field_key] = rule.visibility
        for key in onboarding.ALWAYS_REQUIRED_KEYS:
            rules[key] = 'required'
        return rules

    def _spm_portal_field_names(self):
        """Return the application fields a supplier may fill: those of the keys not hidden."""
        self.ensure_one()
        rules = self.spm_field_rules()
        return [name for key, names in onboarding.KEY_FIELDS.items() if rules[key] != 'hidden'
                for name in names if name not in onboarding.PORTAL_READONLY_FIELDS]

    # --- invitation -----------------------------------------------------------

    @api.model
    def spm_invite(self, email, supplier_type):
        """Create an application in state Invited and e-mail its single-use link.

        Requirements: FR-SPM-0001
        """
        application = self.create({'email': email, 'supplier_type_id': supplier_type.id})
        application._spm_send_invitation()
        return application

    def action_spm_send_invitation(self):
        """Send (again) the invitation link; an earlier link stops working.

        Requirements: FR-SPM-0001
        """
        for application in self:
            application._spm_send_invitation()
        return True

    def _spm_issue_link(self):
        """Issue a new token, store its hash and expiry, and return the single-use URL."""
        self.ensure_one()
        self.check_access('write')
        token = onboarding.new_token()
        # Elevated (NFR-SPM-0002 named place: invitation token): the hash field is system-only;
        # the caller has already passed the write check above.
        self.sudo().write({
            'invitation_token_hash': onboarding.hash_token(token),
            'invitation_expiry': onboarding.expiry_from_now(),
            'invitation_used_date': False,
        })
        return self.get_base_url() + onboarding.ONBOARDING_PATH + token

    def _spm_send_invitation(self):
        """Issue a new token, store its hash and expiry, and e-mail the link."""
        self.ensure_one()
        if self.state not in onboarding.EDITABLE_STATES:
            raise UserError(self.env._("An invitation can only be sent for an application the supplier can still fill in."))
        url = self._spm_issue_link()
        template = self.env.ref(INVITATION_TEMPLATE).with_context(
            spm_onboarding_url=url, spm_valid_days=onboarding.INVITATION_VALID_DAYS)
        template.send_mail(self.id, force_send=False)

    # --- submission -------------------------------------------------------------

    def _spm_missing_items(self):
        """Return the names of the required fields and documents not provided yet."""
        self.ensure_one()
        # Elevated: only the emptiness of restricted fields is read (the caller passed the write check).
        application = self.sudo()
        labels = dict(self.env['procurement.supplier.type.field']._fields['field_key']._description_selection(self.env))
        missing = []
        for key, rule in self.spm_field_rules().items():
            if rule != 'required' or key not in onboarding.KEY_FIELDS:
                continue
            if not application[onboarding.required_field(key)]:
                missing.append(labels[key])
        provided = application.document_ids.document_type_id
        missing += (application.supplier_type_id.required_document_type_ids - provided).mapped('name')
        return missing

    def action_spm_submit(self):
        """Move the application to Submitted when nothing required is missing.

        Requirements: FR-SPM-0003
        """
        self.check_access('write')
        self.lock_for_update()
        for application in self:
            if application.state not in onboarding.EDITABLE_STATES:
                raise UserError(self.env._("Only an application the supplier can still fill in can be submitted."))
            missing = application._spm_missing_items()
            if missing:
                raise UserError(self.env._("The application cannot be submitted. Missing: %s.", ", ".join(missing)))
            # Elevated: the workflow fields are written by the module's own actions only (write guard).
            application.sudo().write({'state': 'submitted', 'submitted_date': fields.Datetime.now()})
            if application.user_id:
                application.activity_schedule(
                    'mail.mail_activity_data_todo', user_id=application.user_id.id,
                    summary=self.env._("Review the onboarding application"))
        return True

    # --- review -------------------------------------------------------------------

    def _spm_start_review(self, role_group=None):
        """Check the caller's role and rights, lock the applications and require the Submitted state."""
        if role_group and not self.env.user.has_group(role_group):
            raise AccessError(self.env._("Only a Procurement manager can do this."))
        self.check_access('write')
        self.lock_for_update()
        if any(application.state != 'submitted' for application in self):
            raise UserError(self.env._("Only a submitted application can be reviewed."))

    def action_spm_request_changes(self, comment=None):
        """Send a submitted application back to the supplier with a mandatory comment.

        Requirements: FR-SPM-0004
        """
        self._spm_start_review()
        comment = (comment or '').strip()
        if not comment:
            raise UserError(self.env._("A comment is required to request changes."))
        for application in self:
            # Elevated: the workflow fields are written by the module's own actions only (write guard).
            application.sudo().write({'state': 'changes_requested', 'change_request_comment': comment})
            audit.log(self.env, 'approval', application, {'state': 'submitted'},
                      {'state': 'changes_requested'}, comment)
            url = application._spm_issue_link()
            self.env.ref(CHANGES_TEMPLATE).with_context(
                spm_onboarding_url=url, spm_comment=comment,
                spm_valid_days=onboarding.INVITATION_VALID_DAYS).send_mail(application.id, force_send=False)
        return True

    def action_spm_reject(self, reason=None):
        """Reject a submitted application with a mandatory reason; the supplier gets a message without it.

        Requirements: FR-SPM-0006
        """
        self._spm_start_review(MANAGER_GROUP)
        reason = (reason or '').strip()
        if not reason:
            raise UserError(self.env._("A reason is required to reject an application."))
        for application in self:
            # Elevated: the workflow fields are written by the module's own actions only (write guard).
            application.sudo().write({'state': 'rejected', 'rejection_reason': reason})
            audit.log(self.env, 'approval', application, {'state': 'submitted'}, {'state': 'rejected'}, reason)
            self.env.ref(REJECTION_TEMPLATE).send_mail(application.id, force_send=False)
        return True

    def action_spm_approve(self):
        """Approve a submitted application: link or create the one supplier contact.

        Requirements: FR-SPM-0005
        """
        self._spm_start_review(MANAGER_GROUP)
        for application in self:
            partner = application._spm_contact_for_approval()
            # Elevated: the workflow fields are written by the module's own actions only (write guard).
            application.sudo().write({'state': 'approved', 'partner_id': partner.id})
            audit.log(self.env, 'approval', application, {'state': 'submitted'},
                      {'state': 'approved', 'partner_id': partner.id}, '')
        return True

    def _spm_contact_for_approval(self):
        """Return the contact the application becomes, created or updated with its data and approved status.

        Elevated (NFR-SPM-0002 named place: approval): the manager role is checked by the caller;
        the contact is shared by all companies, may belong to another company and gets a status,
        which only elevated code writes. The acting user stays the author of the history.
        """
        self.ensure_one()
        data = onboarding.contact_values(self)
        status = onboarding.approved_status(self.env)
        if not status:
            raise UserError(self.env._(
                "No default status of the Approved kind is configured: set one in the supplier statuses before approving."))
        onboarding.lock_tax_id(self.env, data['vat'])
        partner = self.partner_id or onboarding.find_company_contact(self.env, data['vat'])
        partner_model = self.env['res.partner'].sudo().with_context(  # elevated: see above
            spm_change_origin='approval')
        if partner:
            # An existing contact keeps its own values: only what it lacks is filled in.
            data = {name: value for name, value in data.items()
                    if name not in ('name', 'is_company', 'company_id') and not partner[name]}
            data.pop('spm_product_category_ids', None)
            if self.is_requalification:
                data.pop('email', None)  # the address of a re-qualification only reaches the supplier, never fills its contact
            partner = partner.with_context(spm_change_origin='approval')
            partner.write(dict(data, spm_status_id=status.id,
                               spm_product_category_ids=[(4, category.id) for category in self.product_category_ids]))
        else:
            partner = partner_model.create(  # shared contact and status, see above
                dict(data, spm_status_id=status.id))
        self._spm_copy_children(partner)
        return partner

    def _spm_copy_children(self, partner):
        """Create the bank account and the people of the application on the contact, once each."""
        application = self.sudo()  # elevated: restricted bank fields; the manager role was checked
        account = application.bank_account_number
        if account and sanitize_account_number(account) not in partner.bank_ids.mapped('sanitized_account_number'):
            self.env['res.partner.bank'].sudo().with_context(spm_change_origin='approval').create({  # elevated: bank accounts of a supplier, role checked
                'partner_id': partner.id, 'account_number': account, 'bank_name': application.bank_name,
                'bank_bic': application.bank_bic, 'holder_name': application.bank_holder or partner.name})
        known = set(partner.child_ids.mapped('name'))
        self.env['res.partner'].sudo().create([{  # elevated: people of a shared contact, role checked
            'parent_id': partner.id, 'type': 'contact', 'name': contact.name, 'email': contact.email,
            'phone': contact.phone, 'function': contact.function}
            for contact in application.contact_ids if contact.name not in known])
        application.document_ids.write({'partner_id': partner.id})

    def _spm_open_reason_wizard(self, action, name):
        """Open the reason dialog of a review action on this application."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': 'procurement.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_ids': self.ids,
                'default_action': action,
            },
        }

    def action_spm_open_request_changes_wizard(self):
        """Open the dialog asking the comment for the supplier."""
        return self._spm_open_reason_wizard('request_changes', self.env._("Request changes"))

    def action_spm_open_reject_wizard(self):
        """Open the dialog asking the rejection reason."""
        return self._spm_open_reason_wizard('reject', self.env._("Reject application"))
