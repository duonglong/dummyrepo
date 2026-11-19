from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import audit
from ..services import contract as contract_service
from ..services.contract import EDITABLE_STATES

MANAGER_GROUP = 'supplier_procurement_management_hub.group_spm_manager'
ADMIN_GROUP = 'supplier_procurement_management_hub.group_spm_admin'
# fields only the workflow actions and the daily job write, through _spm_write_lifecycle
LIFECYCLE_FIELDS = (
    'name', 'state', 'author_id', 'revised_by_id', 'approved_by_id', 'version', 'version_ids')


class ProcurementContract(models.Model):
    """A supplier contract with owner, dates, notice, currency, terms, scope, lines and versions.

    Requirements: FR-SPM-0062, FR-SPM-0064, FR-SPM-0065
    """
    _name = 'procurement.contract'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Purchase Contract'
    _order = 'date_start desc, id desc'
    _check_company_auto = True

    name = fields.Char(readonly=True, copy=False, default='New')
    reference = fields.Char(string="Supplier reference", copy=False)
    partner_id = fields.Many2one(
        'res.partner', string="Supplier", required=True, ondelete='restrict', index=True, tracking=True)
    owner_id = fields.Many2one(
        'res.users', string="Owner", required=True, default=lambda self: self.env.user,
        ondelete='restrict', tracking=True)
    author_id = fields.Many2one(
        'res.users', string="Author", default=lambda self: self.env.user, readonly=True,
        copy=False, ondelete='restrict')
    revised_by_id = fields.Many2one(
        'res.users', string="Revised by", readonly=True, copy=False, ondelete='restrict',
        help="Who made the changes of the version in preparation; empty for the first version.")
    approved_by_id = fields.Many2one('res.users', string="Approved by", readonly=True, copy=False, ondelete='restrict')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('to_approve', 'To approve'),
        ('approved', 'Approved'),
        ('active', 'Active'),
        ('revision', 'In revision'),
        ('expired', 'Expired'),
    ], default='draft', required=True, copy=False, tracking=True, index=True)
    version = fields.Integer(default=1, readonly=True, copy=False)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company, index=True)
    date_start = fields.Date(string="Start date", required=True, tracking=True)
    date_end = fields.Date(string="End date", required=True, tracking=True)
    notice_days = fields.Integer(string="Notice period (days)")
    notice_date = fields.Date(compute='_compute_notice_date', store=True)
    currency_id = fields.Many2one(
        'res.currency', required=True, default=lambda self: self.env.company.currency_id)
    payment_term_id = fields.Many2one(
        'account.payment.term', string="Payment terms", ondelete='restrict', check_company=True)
    scope_kind = fields.Selection([
        ('products', 'Products'),
        ('categories', 'Product categories'),
        ('service', 'Service'),
    ], string="Scope", default='products', required=True)
    product_category_ids = fields.Many2many('product.category', string="Product categories")
    service_description = fields.Text()
    ceiling_value = fields.Monetary(string="Value ceiling", currency_field='currency_id')
    ceiling_qty = fields.Float(string="Quantity ceiling", digits='Product Unit')
    line_ids = fields.One2many('procurement.contract.line', 'contract_id', string="Lines", copy=True)
    version_ids = fields.One2many('procurement.contract.version', 'contract_id', string="Earlier versions")

    @api.depends('date_end', 'notice_days')
    def _compute_notice_date(self):
        for contract in self:
            contract.notice_date = (
                contract.date_end - timedelta(days=contract.notice_days) if contract.date_end else False)

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        """Refuse an end date before the start date.

        Requirements: FR-SPM-0062
        """
        for contract in self:
            if contract.date_start and contract.date_end and contract.date_end < contract.date_start:
                raise ValidationError(self.env._("The end date is before the start date."))

    @api.constrains('notice_days', 'ceiling_value', 'ceiling_qty')
    def _check_amounts(self):
        """Requirements: FR-SPM-0062"""
        for contract in self:
            if contract.notice_days < 0 or contract.ceiling_value < 0 or contract.ceiling_qty < 0:
                raise ValidationError(self.env._("The notice period and the ceilings cannot be negative."))

    @api.model_create_multi
    def create(self, vals_list):
        """Number the contract and start it as a draft written by the current user.

        Requirements: FR-SPM-0062
        """
        for vals in vals_list:
            vals['state'] = 'draft'
            vals['version'] = 1
            vals['author_id'] = self.env.user.id
            for name in ('approved_by_id', 'revised_by_id', 'version_ids'):
                vals.pop(name, None)
            vals['name'] = self.env['ir.sequence'].next_by_code('procurement.contract') or 'New'
        return super().create(vals_list)

    def write(self, vals):
        """Refuse a change of the workflow fields, and of the terms outside a draft or a revision.

        Requirements: FR-SPM-0062, FR-SPM-0064, FR-SPM-0065
        The number, state, authors, approver and version change only through the workflow actions
        and the daily job; the company only while the contract is a draft.
        """
        if set(vals) & set(LIFECYCLE_FIELDS):
            raise UserError(self.env._("The state, the authors and the version of a contract change through its actions."))
        guarded = set(contract_service.TERM_FIELDS) | {'partner_id'}
        if set(vals) & guarded and any(contract.state not in EDITABLE_STATES for contract in self):
            raise UserError(self.env._("The terms of a contract can only be changed in a draft or a revision."))
        if 'company_id' in vals and any(contract.state != 'draft' for contract in self):
            raise UserError(self.env._("The company of a contract can only be changed in a draft."))
        return super().write(vals)

    def _spm_write_lifecycle(self, vals):
        """Write the workflow fields (the actions and the daily job only), tracking included."""
        return super().write(vals)

    def unlink(self):
        """Refuse the deletion of a contract that left the draft, or whose lines were ordered from.

        Requirements: FR-SPM-0065
        """
        if any(contract.state != 'draft' for contract in self):
            raise UserError(self.env._("Only a draft contract can be deleted."))
        # sudo: a contract manager may lack the right to read purchase orders; only a count is read
        if self.env['purchase.order.line'].sudo().search_count([('spm_contract_line_id.contract_id', 'in', self.ids)]):
            raise UserError(self.env._("Orders were placed under this contract, it cannot be deleted."))
        return super().unlink()

    def _spm_is_admin(self):
        return self.env.user.has_group(ADMIN_GROUP) or self.env.user.has_group('base.group_system')

    def _spm_check_manager(self):
        if not (self.env.user.has_group(MANAGER_GROUP) or self._spm_is_admin()):
            raise AccessError(self.env._("Only a Procurement manager handles contracts."))

    def action_spm_submit(self):
        """Send a draft contract or a revision to approval.

        Requirements: FR-SPM-0064
        """
        self._spm_check_manager()
        self.check_access('write')
        for contract in self:
            if contract.state not in EDITABLE_STATES:
                raise UserError(self.env._("Only a draft contract or a revision is sent to approval."))
        self._spm_write_lifecycle({'state': 'to_approve'})
        return True

    def action_spm_approve(self):
        """Approve a contract: Active from its start date, Approved while it waits for it.

        Requirements: FR-SPM-0064, FR-SPM-0065
        The author may not approve their own contract unless Administrator. The approval of a
        revision applies the new terms as the next version.
        """
        self._spm_check_manager()
        self.check_access('write')
        today = fields.Date.context_today(self)
        for contract in self:
            if contract.state != 'to_approve':
                raise UserError(self.env._("Only a contract waiting for approval is approved."))
            if self.env.user in (contract.author_id | contract.revised_by_id) and not self._spm_is_admin():
                raise UserError(self.env._("Another manager must approve this contract."))
            revising = contract.version_ids.filtered(lambda version: version.version == contract.version)
            state = 'active' if contract.date_start <= today else 'approved'
            values = {'state': state, 'approved_by_id': self.env.user.id}
            if revising:
                values['version'] = contract.version + 1
            contract._spm_write_lifecycle(values)
            audit.log(self.env, 'contract', contract, {}, {'version': contract.version},
                      'revised' if revising else 'activated' if state == 'active' else 'approved')
        return True

    def action_spm_revise(self):
        """Freeze the current terms as a read-only version and open the contract for changes.

        Requirements: FR-SPM-0065
        """
        self._spm_check_manager()
        self.check_access('write')
        for contract in self:
            if contract.state != 'active':
                raise UserError(self.env._("Only an active contract is revised."))
            # sudo: no group may create versions directly, the version is read-only for every user;
            # the manager and write-access checks are above
            self.env['procurement.contract.version'].sudo().create({
                'contract_id': contract.id,
                'version': contract.version,
                'kind': 'revision',
                'author_id': (contract.revised_by_id or contract.author_id).id,
                'approved_by_id': contract.approved_by_id.id,
                'date_start': contract.date_start,
                'date_end': contract.date_end,
                'terms': contract_service.build_terms(contract),
            })
            contract._spm_write_lifecycle({'state': 'revision', 'revised_by_id': self.env.user.id})
        return True

    @api.model
    def _spm_collect_due(self):
        """Return the contracts to move on today: approved ones at their start, active ones past their end.

        Requirements: FR-SPM-0064
        """
        today = fields.Date.context_today(self)
        return self.search([
            '|', '&', ('state', '=', 'approved'), ('date_start', '<=', today),
            '&', ('state', '=', 'active'), ('date_end', '<', today)], order='id')

    def _spm_advance_state(self):
        """Make an approved contract Active at its start date, an active one Expired after its end.

        Requirements: FR-SPM-0064
        Returns the number of notifications sent (none).
        """
        self.ensure_one()
        if self.state == 'approved':
            self._spm_write_lifecycle({'state': 'active'})
            audit.log(self.env, 'contract', self, {}, {'version': self.version}, 'activated')
        else:
            self._spm_write_lifecycle({'state': 'expired'})
        return 0

    @api.model
    def _spm_update_states(self):
        """Run the daily step of the contracts in one go (the scheduled job runs it record by record).

        Requirements: FR-SPM-0064
        """
        for contract in self._spm_collect_due():
            contract._spm_advance_state()


class ProcurementJob(models.Model):  # sdd-allow: model-split the job runner is extended per code by the task that owns the job (procurement_job.py, not a file of this task)
    """The daily start and expiry of contracts runs as a job of the app (ADR-SPM-007).

    Requirements: FR-SPM-0064, FR-SPM-0096
    """
    _inherit = 'procurement.job'

    code = fields.Selection(selection_add=[('contract_states', 'Contract start and expiry')],
        ondelete={'contract_states': 'cascade'})

    def _spm_collect_contract_states(self):
        """Return the contracts due today (see ``procurement.contract._spm_collect_due``).

        Requirements: FR-SPM-0064
        """
        return self.env['procurement.contract']._spm_collect_due()

    def _spm_process_contract_states(self, contract):
        """Move one contract on and return the notifications sent.

        Requirements: FR-SPM-0064
        """
        return contract._spm_advance_state()
