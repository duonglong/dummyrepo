from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import portal_access


class ProcurementPortalLog(models.Model):
    """Supplier portal activity; nobody changes or deletes an entry.

    Requirements: FR-SPM-0027
    Built for T-005 (minimal: refused invitation links are logged).
    """
    _name = 'procurement.portal.log'
    _description = 'Portal Activity'
    _order = 'date desc, id desc'

    date = fields.Datetime(required=True, index=True, default=fields.Datetime.now)
    user_id = fields.Many2one('res.users', ondelete='set null')
    partner_id = fields.Many2one('res.partner', ondelete='set null')
    action = fields.Selection([
        ('view', 'Viewed'),
        ('download', 'Downloaded'),
        ('submit', 'Submitted'),
        ('denied', 'Denied'),
        ('login', 'Logged in'),
    ], required=True)
    res_model = fields.Char()
    res_id = fields.Many2oneReference(model_field='res_model')
    result = fields.Selection([('ok', 'Accepted'), ('refused', 'Refused')])
    detail = fields.Char()

    def write(self, vals):
        """Requirements: FR-SPM-0027 (refused for everyone)."""
        raise UserError(self.env._("Portal activity entries cannot be changed."))

    def unlink(self):
        """Requirements: FR-SPM-0027 (refused for everyone)."""
        raise UserError(self.env._("Portal activity entries cannot be deleted."))


class ResUsers(models.Model):
    """Writes the portal activity entry of a supplier user's login.

    Requirements: FR-SPM-0027
    """
    _inherit = 'res.users'

    @api.model
    def _update_last_login(self):
        super()._update_last_login()
        if portal_access.is_supplier_user(self.env.user):
            portal_access.log(self.env, 'login', 'ok')
