from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import onboarding


class ProcurementSupplierApplicationContact(models.Model):
    """A person of the applying supplier; becomes a contact only at approval.

    Requirements: FR-SPM-0002
    """
    _name = 'procurement.supplier.application.contact'
    _description = 'Supplier Application Contact'
    _order = 'id'

    application_id = fields.Many2one(
        'procurement.supplier.application', required=True, ondelete='cascade', index=True)
    name = fields.Char(required=True)
    email = fields.Char()
    phone = fields.Char()
    function = fields.Char()

    def _spm_check_open(self, applications):
        """Refuse a change of the contacts of an approved or rejected application, unless elevated."""
        if not self.env.su and any(application.state in onboarding.CLOSED_STATES for application in applications):
            raise UserError(self.env._("An approved or rejected application cannot be changed."))

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0003"""
        applications = self.env['procurement.supplier.application'].browse(
            [vals['application_id'] for vals in vals_list if vals.get('application_id')])
        self._spm_check_open(applications)
        return super().create(vals_list)

    def write(self, vals):
        """Requirements: FR-SPM-0003"""
        applications = self.application_id
        if vals.get('application_id'):
            applications |= self.env['procurement.supplier.application'].browse(vals['application_id'])
        self._spm_check_open(applications)
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0003"""
        self._spm_check_open(self.application_id)
        return super().unlink()
