from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import audit, supplier_control
from .procurement_supplier_change import SPM_RESTRICTED_GROUPS

# Watched fields whose values only the restricted groups may read (BR-SPM-0004).
SPM_RESTRICTED_WATCHED = ('vat', 'spm_risk_level')
SPM_WATCHED = (
    'name', 'street', 'street2', 'city', 'zip', 'state_id', 'country_id', 'vat',
    'spm_product_category_ids', 'spm_status_id', 'spm_risk_level',
    'spm_requalification_date',
)
SPM_BANK_WATCHED = {'account_number', 'bank_name', 'holder_name', 'partner_id'}
SPM_REASON_KINDS = ('suspended', 'blocked')
# The status and the stored kind copied from it: both are written only elevated.
SPM_GUARDED_STATUS_FIELDS = ('spm_status_id', 'spm_status_kind')


class ResPartner(models.Model):
    """The one supplier master: lifecycle status, change history.

    Requirements: FR-SPM-0008, FR-SPM-0011, FR-SPM-0014, BR-SPM-0004, BR-SPM-0005
    """
    _inherit = 'res.partner'

    spm_status_id = fields.Many2one(
        'procurement.supplier.status', string="Supplier status", copy=False,
        ondelete='restrict', index='btree_not_null', tracking=False)
    spm_status_kind = fields.Selection(
        related='spm_status_id.kind', string="Status kind", store=True, index=True)
    spm_supplier_type_id = fields.Many2one(
        'procurement.supplier.type', string="Supplier type", ondelete='restrict')
    spm_is_supplier = fields.Boolean(
        string="Is a supplier", compute='_compute_spm_is_supplier', store=True, index=True)
    spm_product_category_ids = fields.Many2many(
        'product.category', 'spm_partner_product_category_rel', 'partner_id', 'category_id',
        string="Supplied categories")
    spm_operating_locations = fields.Text(string="Operating locations")
    spm_requalification_date = fields.Date(
        string="Re-qualification date", index=True, copy=False)
    spm_risk_level = fields.Selection([
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
    ], string="Risk level", copy=False, groups=SPM_RESTRICTED_GROUPS)
    spm_change_ids = fields.One2many(
        'procurement.supplier.change', 'partner_id', string="Change history", readonly=True)
    spm_document_ids = fields.One2many(
        'procurement.supplier.document', 'partner_id', string="Documents")
    spm_compliance_state = fields.Selection([
        ('valid', 'Valid'),
        ('expiring', 'Expiring'),
        ('to_verify', 'To verify'),
        ('rejected', 'Rejected'),
        ('expired_or_missing', 'Expired or missing'),
    ], string="Document compliance", compute='_compute_spm_compliance',
        search='_search_spm_compliance_state')
    spm_compliance_summary = fields.Text(
        string="Required documents", compute='_compute_spm_compliance')

    @api.depends('spm_status_id', 'spm_supplier_type_id', 'supplier_rank')
    def _compute_spm_is_supplier(self):
        """Requirements: FR-SPM-0005

        A contact is a supplier once it has a hub status or type, or Odoo ranks it as a vendor.
        """
        for partner in self:
            partner.spm_is_supplier = bool(
                partner.spm_status_id or partner.spm_supplier_type_id or partner.supplier_rank > 0)

    # --- document compliance --------------------------------------------

    @api.depends(
        'spm_supplier_type_id.required_document_type_ids',
        'spm_document_ids.verification_state', 'spm_document_ids.expiry_date',
        'spm_document_ids.is_current', 'spm_document_ids.document_type_id')
    @api.depends_context('company', 'uid')
    def _compute_spm_compliance(self):
        """Requirements: FR-SPM-0008

        The worst state among the required documents, and one line per
        required type with its state.
        """
        labels = dict(self.env['procurement.supplier.document']._fields["validity_state"]
                      ._description_selection(self.env), missing=self.env._("Missing"))
        for partner in self:
            # elevated: a summary of the documents, readable with the contact
            elevated = partner.sudo()
            rows = supplier_control.compliance_rows(elevated.env, elevated)
            partner.spm_compliance_state = supplier_control.compliance_state(rows)
            partner.spm_compliance_summary = '\n'.join(
                f"{document_type.name}: {labels[state]}" for document_type, state in rows) or False

    def _search_spm_compliance_state(self, operator, value):
        """Requirements: FR-SPM-0008"""
        if operator not in ('=', '!=', 'in', 'not in'):
            raise UserError(self.env._("Unsupported search on the document compliance."))
        values = {value} if isinstance(value, str) else set(value)
        # elevated: the candidates are filtered by the compliance of their documents
        candidates = self.sudo().search([('spm_supplier_type_id.required_document_type_ids', '!=', False)])
        matching = candidates.filtered(
            lambda partner: (partner.spm_compliance_state in values) == (operator in ('=', 'in')))
        return [('id', 'in', matching.ids)]

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

    def _spm_is_history_subject(self):
        """Tell whether the history of this contact is kept.

        Only suppliers have a history: a contact whose commercial contact has a
        hub status, type or supplier rank, or that is neither an employee, a user
        nor a customer. Employees, users and customers never do, so their data
        (bank accounts of employees, native rules) is never copied into the
        supplier history.
        """
        commercial = self.commercial_partner_id.sudo()  # elevated: reads only flags
        if commercial.employee_ids or commercial.user_ids:
            return False
        return bool(
            commercial.spm_status_id or commercial.spm_supplier_type_id
            or commercial.supplier_rank > 0 or commercial.customer_rank == 0)

    def _spm_change_rows(self, before, after, origin, reason=None):
        """Return the change rows and the audit calls of one partner's changed watched fields."""
        self.ensure_one()
        rows = []
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
            rows.append(row)
            if name == 'spm_status_id':
                audit.log(self.env, 'supplier_status', self, {name: old}, {name: new}, reason or '')
            elif name in SPM_RESTRICTED_WATCHED:
                audit.log(self.env, 'restricted_field', self, {name: old}, {name: new}, '')
        return rows

    def _spm_check_status_allowed(self, vals, writing=False):
        """Refuse a status written by a caller that is not elevated.

        The lifecycle action, approval and job code write the status elevated
        after their own role and reason checks; nothing the client sends (a
        context key) opens this path. A Procurement manager may also write a
        status that needs no reason directly (never the stored kind, never
        Suspended or Blocked, and never on create).
        """
        if self.env.su or not any(name in vals for name in SPM_GUARDED_STATUS_FIELDS):
            return
        if writing and self._spm_manager_may_write_status(vals):
            return
        raise AccessError(self.env._(
            "The supplier status can only be changed with the Change status action."))

    def _spm_manager_may_write_status(self, vals):
        """Return True for a Procurement manager writing only a reason-free status."""
        if 'spm_status_kind' in vals or not self.env.user.has_group(
                'supplier_procurement_management_hub.group_spm_manager'):
            return False
        status = self.env['procurement.supplier.status'].sudo().browse(vals['spm_status_id']).exists()
        return bool(status) and status.kind not in SPM_REASON_KINDS

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0011"""
        for vals in vals_list:
            self._spm_check_status_allowed(vals)
        if not self.env.su:
            # native create adds context defaults inside super(): a client default must not set the status
            self = self.with_context(default_spm_status_id=False)
        return super().create(vals_list)

    def write(self, vals):
        """Requirements: FR-SPM-0011, FR-SPM-0014"""
        self._spm_check_status_allowed(vals, writing=True)
        watched = [name for name in SPM_WATCHED if name in vals]
        if not watched:
            return super().write(vals)
        becomes_supplier = bool({'spm_status_id', 'spm_supplier_type_id'}.intersection(vals))
        tracked = self if becomes_supplier else self.filtered(
            lambda partner: partner._spm_is_history_subject())
        before = {partner.id: partner._spm_snapshot(watched) for partner in tracked}
        result = super().write(vals)
        if becomes_supplier:
            tracked = tracked.filtered(lambda partner: partner._spm_is_history_subject())
        origin = self.env['procurement.supplier.change']._spm_origin()
        reason = self.env.context.get('spm_status_reason')
        rows = []
        for partner in tracked:
            rows += partner._spm_change_rows(
                before[partner.id], partner._spm_snapshot(watched), origin, reason)
        if rows:
            self.env['procurement.supplier.change'].sudo().create(rows)  # elevated: system history
        return result

    # --- lifecycle ------------------------------------------------------

    def action_spm_set_status(self, status, reason=None):
        """Change the supplier status; Suspended and Blocked kinds need a reason.

        Requirements: FR-SPM-0011, BR-SPM-0005
        """
        if not self.env.user.has_group('supplier_procurement_management_hub.group_spm_manager'):
            raise AccessError(self.env._("Only a Procurement manager can change the supplier status."))
        status = self.env['procurement.supplier.status'].browse(
            status.id if isinstance(status, models.BaseModel) else status).exists()
        if not status:
            raise UserError(self.env._("The supplier status does not exist."))
        self.check_access('read')  # the company boundary stays: only the missing write right is lifted below
        reason = (reason or '').strip()
        if status.kind in SPM_REASON_KINDS and not reason:
            raise UserError(self.env._("A reason is required to set the status %s.", status.name))
        origin = self.env['procurement.supplier.change']._spm_origin()
        # elevated: the role and the reason are checked above; the acting user stays the manager
        self.sudo().with_context(spm_status_reason=reason, spm_change_origin=origin).write(
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
    Accounts of contacts that are not suppliers (employees, customers) are
    never logged: their readers are narrower than the history's.
    """
    _inherit = 'res.partner.bank'

    def _spm_describe(self):
        """Return the account number with its bank and a holder that differs from the contact."""
        self.ensure_one()
        record = self.sudo()  # elevated: system history
        holder = record.holder_name if record.holder_name != record.partner_id.name else ''
        parts = [record.account_number, record.bank_name, holder]
        return ' / '.join(part for part in parts if part)

    def _spm_log_banks(self, entries):
        """Write the change rows and audit entries of ``(partner, old, new)`` entries of suppliers."""
        entries = [entry for entry in entries if entry[0]._spm_is_history_subject()]
        if not entries:
            return
        Change = self.env['procurement.supplier.change']
        origin = Change._spm_origin()
        Change.sudo().create([{  # elevated: system history
            'partner_id': partner.id,
            'field_name': 'bank_ids',
            'field_label': self.env._("Bank accounts"),
            'restricted_old_value': old,
            'restricted_new_value': new,
            'user_id': self.env.user.id,
            'origin': origin,
        } for partner, old, new in entries])
        for partner, old, new in entries:
            audit.log(self.env, 'restricted_field', partner, {'bank_ids': old}, {'bank_ids': new}, '')

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0014"""
        banks = super().create(vals_list)
        banks._spm_log_banks([(bank.partner_id, '', bank._spm_describe()) for bank in banks])
        return banks

    def write(self, vals):
        """Requirements: FR-SPM-0014"""
        if not SPM_BANK_WATCHED.intersection(vals):
            return super().write(vals)
        before = {bank.id: (bank.partner_id, bank._spm_describe()) for bank in self}
        result = super().write(vals)
        entries = []
        for bank in self:
            old_partner, old_text = before[bank.id]
            new_text = bank._spm_describe()
            if old_partner != bank.partner_id:
                entries += [(old_partner, old_text, ''), (bank.partner_id, '', new_text)]
            elif old_text != new_text:
                entries.append((bank.partner_id, old_text, new_text))
        self._spm_log_banks(entries)
        return result

    def unlink(self):
        """Requirements: FR-SPM-0014"""
        self._spm_log_banks([(bank.partner_id, bank._spm_describe(), '') for bank in self])
        return super().unlink()
