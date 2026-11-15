from odoo import api, fields, models
from odoo.exceptions import AccessError

from ..const import DASHBOARD_WIDGETS


class DataQualityDashboardLayout(models.Model):
    """One user's choice of a dashboard widget's place and visibility.

    A user with no line sees the default layout; a reset deletes the user's lines.

    Requirements: FR-DGQ-0037
    """
    _name = 'data.quality.dashboard.layout'
    _description = 'Data Quality Dashboard Layout'
    _order = 'user_id, sequence, id'

    user_id = fields.Many2one(
        'res.users', string='User', required=True, default=lambda self: self.env.user, ondelete='cascade', index=True)
    widget = fields.Selection(DASHBOARD_WIDGETS, required=True)
    sequence = fields.Integer(default=10)
    visible = fields.Boolean(default=True)

    _user_widget_unique = models.Constraint(
        'UNIQUE(user_id, widget)', 'A widget has one place per user.')

    @api.model
    def _access_domain(self, operation):
        """Everyone sees and changes only their own lines; superuser and sudo are not filtered.

        Requirements: FR-DGQ-0037
        """
        domain = super()._access_domain(operation)
        if operation == 'create':
            return domain
        return domain & fields.Domain('user_id', '=', self.env.uid)

    @api.model_create_multi
    def create(self, vals_list):
        """A person creates lines for themselves only.

        Requirements: FR-DGQ-0037
        """
        if not self.env.su and any(vals.get('user_id', self.env.uid) != self.env.uid for vals in vals_list):
            raise AccessError(self.env._("You can only change your own dashboard layout."))
        return super().create(vals_list)
