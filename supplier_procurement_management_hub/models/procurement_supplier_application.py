from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import onboarding
from .procurement_supplier_change import SPM_RESTRICTED_GROUPS

# Bank and tax data: the restricted roles and the Buyer (BR-SPM-0004).
SPM_APPLICATION_RESTRICTED_GROUPS = (
    SPM_RESTRICTED_GROUPS + ',supplier_procurement_management_hub.group_spm_buyer')
INVITATION_TEMPLATE = 'supplier_procurement_management_hub.mail_template_supplier_invitation'


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

    def _spm_send_invitation(self):
        """Issue a new token, store its hash and expiry, and e-mail the link."""
        self.ensure_one()
        if self.state not in onboarding.EDITABLE_STATES:
            raise UserError(self.env._("An invitation can only be sent for an application the supplier can still fill in."))
        self.check_access('write')
        token = onboarding.new_token()
        # Elevated (NFR-SPM-0002 named place: invitation token): the hash field is system-only;
        # the caller has already passed the write check above.
        self.sudo().write({
            'invitation_token_hash': onboarding.hash_token(token),
            'invitation_expiry': onboarding.expiry_from_now(),
            'invitation_used_date': False,
        })
        url = self.get_base_url() + onboarding.ONBOARDING_PATH + token
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
