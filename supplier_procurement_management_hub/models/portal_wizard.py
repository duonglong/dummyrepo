from odoo import models
from odoo.exceptions import UserError


class PortalWizardUser(models.TransientModel):
    """Portal invitation: a Buyer invites contacts of suppliers only.

    Requirements: FR-SPM-0029
    """
    _inherit = 'portal.wizard.user'

    def action_grant_access(self):
        """Requirements: FR-SPM-0029

        A Buyer (not an administrator) is refused for a contact of a company that is no supplier.
        """
        user = self.env.user
        if user.has_group('supplier_procurement_management_hub.group_spm_buyer') \
                and not user.has_group('base.group_system'):
            for row in self:
                if not row.partner_id.commercial_partner_id.sudo().spm_is_supplier:  # sudo: one flag of the company contact
                    raise UserError(self.env._(
                        "%(contact)s is not a contact of a supplier: portal access is refused.",
                        contact=row.partner_id.name))
        return super().action_grant_access()
