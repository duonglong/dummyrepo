from odoo import fields, models
from odoo.exceptions import UserError


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
