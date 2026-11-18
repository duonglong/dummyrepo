from odoo import api, fields, models


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

    @api.model_create_multi
    def create(self, vals_list):
        """Refuse a contact on a closed application, judged on the stored result (defaults included).

        Requirements: FR-SPM-0003
        """
        contacts = super().create(vals_list)
        contacts.application_id._spm_check_open()
        return contacts

    def write(self, vals):
        """Requirements: FR-SPM-0003"""
        applications = self.application_id
        if vals.get('application_id'):
            applications |= applications.browse(vals['application_id'])
        applications._spm_check_open()
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0003"""
        self.application_id._spm_check_open()
        return super().unlink()
