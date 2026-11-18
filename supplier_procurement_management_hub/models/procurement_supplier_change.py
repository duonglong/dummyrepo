from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

# Roles that may read restricted supplier values (BR-SPM-0004); named once here.
SPM_RESTRICTED_GROUPS = (
    'supplier_procurement_management_hub.group_spm_manager,'
    'supplier_procurement_management_hub.group_spm_finance,'
    'supplier_procurement_management_hub.group_spm_auditor,'
    'supplier_procurement_management_hub.group_spm_admin'
)


class ProcurementSupplierChange(models.Model):
    """One change of watched supplier data with old and new value, user, date and origin.

    Requirements: FR-SPM-0014, FR-SPM-0011, FR-SPM-0015, BR-SPM-0004
    The status history is the rows of the field ``spm_status_id``. Values of
    restricted fields (tax identifier, bank account) are kept apart in the
    ``restricted_*`` fields, readable by the restricted groups only.
    """
    _name = 'procurement.supplier.change'
    _description = 'Supplier Change'
    _order = 'date desc, id desc'

    partner_id = fields.Many2one('res.partner', required=True, index=True, ondelete='cascade', readonly=True)
    field_name = fields.Char(required=True, readonly=True)
    field_label = fields.Char(readonly=True)
    old_value = fields.Text(readonly=True)
    new_value = fields.Text(readonly=True)
    restricted_old_value = fields.Text(
        readonly=True, groups=SPM_RESTRICTED_GROUPS)
    restricted_new_value = fields.Text(
        readonly=True, groups=SPM_RESTRICTED_GROUPS)
    user_id = fields.Many2one('res.users', readonly=True, ondelete='restrict',
                              default=lambda self: self.env.user)
    date = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    origin = fields.Selection([
        ('backoffice', 'Back office'),
        ('portal', 'Portal'),
        ('approval', 'Approval'),
        ('job', 'Scheduled job'),
    ], required=True, default='backoffice', readonly=True)
    reason = fields.Text(readonly=True)
    state = fields.Selection([
        ('applied', 'Applied'),
        ('pending', 'Pending'),
        ('verified', 'Verified'),
        ('refused', 'Refused'),
    ], required=True, default='applied', readonly=True)
    bank_account_number = fields.Char(readonly=True, groups=SPM_RESTRICTED_GROUPS)
    bank_name = fields.Char(readonly=True, groups=SPM_RESTRICTED_GROUPS)
    bank_bic = fields.Char(readonly=True, groups=SPM_RESTRICTED_GROUPS)
    bank_holder = fields.Char(readonly=True, groups=SPM_RESTRICTED_GROUPS)
    verified_by_id = fields.Many2one('res.users', readonly=True, ondelete='restrict')
    verified_date = fields.Datetime(readonly=True)

    @api.model
    def _spm_origin(self):
        """Return the origin of the current change.

        Only an elevated caller (portal, approval, job code) may name another
        origin through the context; a user's own call is always back office.
        """
        if not self.env.su:
            return 'backoffice'
        origin = self.env.context.get('spm_change_origin')
        valid = [key for key, _label in self._fields['origin'].selection]
        return origin if origin in valid else 'backoffice'

    def _spm_bank_description(self):
        """The pending account as one line: number, bank and a holder."""
        self.ensure_one()
        record = self.sudo()  # sudo: the restricted bank fields, for the history line of the caller's own row
        return ' / '.join(part for part in (
            record.bank_account_number, record.bank_name, record.bank_holder) if part)

    @api.model
    def _spm_hold_bank_change(self, partner, details):
        """Store a portal bank change as pending; the verified accounts stay in use.

        Requirements: FR-SPM-0015

        An earlier pending change of the same supplier is refused: only the last one is verified.
        """
        pending = self.sudo()  # sudo: a portal user cannot create history rows; the caller checked the supplier
        earlier = pending.search([
            ('partner_id', '=', partner.id), ('field_name', '=', 'bank_ids'), ('state', '=', 'pending')])
        earlier.write({'state': 'refused', 'reason': self.env._("Replaced by a newer change.")})
        change = pending.create({
            'partner_id': partner.id,
            'field_name': 'bank_ids',
            'field_label': self.env._("Bank accounts"),
            'origin': 'portal',
            'state': 'pending',
            'user_id': self.env.user.id,
            'bank_account_number': details['account_number'],
            'bank_name': details.get('bank_name') or False,
            'bank_bic': details.get('bank_bic') or False,
            'bank_holder': details.get('holder_name') or False,
        })
        change.restricted_new_value = change._spm_bank_description()
        return change

    def action_spm_verify(self):
        """Apply the pending bank changes: the account becomes the supplier's first account.

        Requirements: FR-SPM-0015
        """
        if not self.env.user.has_group('supplier_procurement_management_hub.group_spm_finance') \
                and not self.env.user.has_group('supplier_procurement_management_hub.group_spm_manager'):
            raise AccessError(self.env._("Only a Finance reviewer or a Procurement manager can verify a bank change."))
        self.check_access('read')
        if self.filtered(lambda change: change.state != 'pending'):
            raise UserError(self.env._("Only a pending change can be verified."))
        for change in self:
            # sudo: the role is checked above; the acting user stays the verifier
            elevated = change.sudo()
            elevated._spm_apply_bank_change()
            elevated.write({
                'state': 'verified', 'verified_by_id': self.env.user.id,
                'verified_date': fields.Datetime.now()})
        return True

    def _spm_apply_bank_change(self):
        """Create the verified account, ahead of the supplier's earlier ones (called elevated)."""
        self.ensure_one()
        Bank = self.env['res.partner.bank']
        existing = Bank.with_context(active_test=False).search([('partner_id', '=', self.partner_id.id)])
        values = {
            'partner_id': self.partner_id.id,
            'account_number': self.bank_account_number,
            'holder_name': self.bank_holder or self.partner_id.name,
            'sequence': min(existing.mapped('sequence') or [1]) - 1,
        }
        if self.bank_name or self.bank_bic:
            domain = [('bic', '=', self.bank_bic)] if self.bank_bic else [('name', '=', self.bank_name)]
            bank = self.env['res.bank'].search(domain, limit=1) or self.env['res.bank'].create({
                'name': self.bank_name or self.bank_bic, 'bic': self.bank_bic or False})
            values['bank_id'] = bank.id
        Bank.create(values)
