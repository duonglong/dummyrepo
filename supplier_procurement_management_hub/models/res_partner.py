from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import audit

SPM_RESTRICTED_GROUPS = (
    'supplier_procurement_management_hub.group_spm_manager,'
    'supplier_procurement_management_hub.group_spm_finance,'
    'supplier_procurement_management_hub.group_spm_auditor,'
    'supplier_procurement_management_hub.group_spm_admin'
)
# Watched fields whose values only the restricted groups may read (BR-SPM-0004).
SPM_RESTRICTED_WATCHED = ('vat', 'spm_risk_level')
SPM_WATCHED = (
    'name', 'street', 'street2', 'city', 'zip', 'state_id', 'country_id', 'vat',
    'spm_product_category_ids', 'spm_status_id', 'spm_risk_level',
    'spm_requalification_date',
)
SPM_REASON_KINDS = ('suspended', 'blocked')


class ResPartner(models.Model):
    """The one supplier master: lifecycle status, change history.

    Requirements: FR-SPM-0011, FR-SPM-0014, BR-SPM-0004, BR-SPM-0005
    """
    _inherit = 'res.partner'

    spm_status_id = fields.Many2one(
        'procurement.supplier.status', string="Supplier status", copy=False,
        ondelete='restrict', index='btree_not_null', tracking=False)
    spm_status_kind = fields.Selection(
        related='spm_status_id.kind', string="Status kind", store=True, index=True)
    spm_supplier_type_id = fields.Many2one(
        'procurement.supplier.type', string="Supplier type", ondelete='restrict')
    spm_product_category_ids = fields.Many2many(
        'product.category', 'spm_partner_product_category_rel', 'partner_id', 'category_id',
        string="Supplied categories")
    spm_requalification_date = fields.Date(
        string="Re-qualification date", index=True, copy=False)
    spm_risk_level = fields.Selection([
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
    ], string="Risk level", copy=False, groups=SPM_RESTRICTED_GROUPS)
    spm_change_ids = fields.One2many(
        'procurement.supplier.change', 'partner_id', string="Change history", readonly=True)

    # --- change history -------------------------------------------------

    def _spm_snapshot(self, names):
        """Return the displayed value of each watched field of one partner."""
        self.ensure_one()
        record = self.sudo()  # elevated: system history
        values = {}
        for name in names:
            field = record._fields[name]
            value = record[name]
            if field.type == 'many2one':
                values[name] = value.display_name or ''
            elif field.type == 'many2many':
                values[name] = ', '.join(value.mapped('display_name'))
            elif field.type in ('date', 'datetime'):
                values[name] = field.convert_to_export(value, record) or ''
            elif field.type == 'selection':
                values[name] = dict(field._description_selection(self.env)).get(value, '') if value else ''
            else:
                values[name] = value or ''
        return values

    def _spm_record_changes(self, before, after, reason=None):
        """Write one change row per changed watched field and the audit entries."""
        Change = self.env['procurement.supplier.change'].sudo()  # elevated: system history
        origin = Change._spm_origin()
        for name, new in after.items():
            old = before[name]
            if old == new:
                continue
            row = {
                'partner_id': self.id,
                'field_name': name,
                'field_label': self._fields[name]._description_string(self.env),
                'user_id': self.env.user.id,
                'origin': origin,
                'reason': reason if name == 'spm_status_id' else False,
            }
            if name in SPM_RESTRICTED_WATCHED:
                row.update(restricted_old_value=old, restricted_new_value=new)
            else:
                row.update(old_value=old, new_value=new)
            Change.create(row)
            if name == 'spm_status_id':
                audit.log(self.env, 'supplier_status', self, {name: old}, {name: new}, reason or '')
            elif name in SPM_RESTRICTED_WATCHED:
                audit.log(self.env, 'restricted_field', self, {name: old}, {name: new}, '')

    def _spm_check_status_allowed(self, vals):
        """Refuse a status written outside the lifecycle action, approval or job."""
        if 'spm_status_id' in vals and not self.env.su \
                and not self.env.context.get('spm_status_allowed'):
            raise AccessError(self.env._(
                "The supplier status can only be changed with the Change status action."))

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0011"""
        for vals in vals_list:
            self._spm_check_status_allowed(vals)
        return super().create(vals_list)

    def write(self, vals):
        """Requirements: FR-SPM-0011, FR-SPM-0014"""
        self._spm_check_status_allowed(vals)
        watched = [name for name in SPM_WATCHED if name in vals]
        if not watched:
            return super().write(vals)
        before = {partner.id: partner._spm_snapshot(watched) for partner in self}
        result = super().write(vals)
        reason = self.env.context.get('spm_status_reason')
        for partner in self:
            partner._spm_record_changes(
                before[partner.id], partner._spm_snapshot(watched), reason)
        return result

    # --- lifecycle ------------------------------------------------------

    def action_spm_set_status(self, status, reason=None):
        """Change the supplier status; Suspended and Blocked kinds need a reason.

        Requirements: FR-SPM-0011, BR-SPM-0005
        """
        if not self.env.user.has_group('supplier_procurement_management_hub.group_spm_manager'):
            raise AccessError(self.env._("Only a Procurement manager can change the supplier status."))
        reason = (reason or '').strip()
        if status.kind in SPM_REASON_KINDS and not reason:
            raise UserError(self.env._("A reason is required to set the status %s.", status.name))
        self.with_context(spm_status_allowed=True, spm_status_reason=reason).write(
            {'spm_status_id': status.id})
        return True

    def action_spm_open_status_wizard(self):
        """Open the reason dialog to change the status of this supplier."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Change status"),
            'res_model': 'procurement.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_ids': self.ids,
                'default_action': 'set_status',
            },
        }


class ResPartnerBank(models.Model):
    """Bank account changes of a supplier go to its change history.

    Requirements: FR-SPM-0014, BR-SPM-0004
    """
    _inherit = 'res.partner.bank'

    def _spm_log_bank(self, partner, old, new):
        """Write the change row and audit entry of a bank account change."""
        Change = self.env['procurement.supplier.change'].sudo()  # elevated: system history
        Change.create({
            'partner_id': partner.id,
            'field_name': 'bank_ids',
            'field_label': self.env._("Bank accounts"),
            'restricted_old_value': old,
            'restricted_new_value': new,
            'user_id': self.env.user.id,
            'origin': Change._spm_origin(),
        })
        audit.log(self.env, 'restricted_field', partner, {'bank_ids': old}, {'bank_ids': new}, '')

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0014"""
        banks = super().create(vals_list)
        for bank in banks:
            bank._spm_log_bank(bank.partner_id, '', bank.sudo().account_number or '')  # elevated: system history
        return banks

    def write(self, vals):
        """Requirements: FR-SPM-0014"""
        if 'account_number' not in vals:
            return super().write(vals)
        before = {bank.id: bank.sudo().account_number or '' for bank in self}  # elevated: system history
        result = super().write(vals)
        for bank in self:
            if before[bank.id] != (bank.sudo().account_number or ''):  # elevated: system history
                bank._spm_log_bank(bank.partner_id, before[bank.id], bank.sudo().account_number or '')  # elevated: system history
        return result

    def unlink(self):
        """Requirements: FR-SPM-0014"""
        for bank in self:
            bank._spm_log_bank(bank.partner_id, bank.sudo().account_number or '', '')  # elevated: system history
        return super().unlink()
