from odoo import api, fields, models

# Roles that may read restricted supplier values (BR-SPM-0004); named once here.
SPM_RESTRICTED_GROUPS = (
    'supplier_procurement_management_hub.group_spm_manager,'
    'supplier_procurement_management_hub.group_spm_finance,'
    'supplier_procurement_management_hub.group_spm_auditor,'
    'supplier_procurement_management_hub.group_spm_admin'
)


class ProcurementSupplierChange(models.Model):
    """One change of watched supplier data with old and new value, user, date and origin.

    Requirements: FR-SPM-0014, FR-SPM-0011, BR-SPM-0004
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
