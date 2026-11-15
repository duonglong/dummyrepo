from odoo import api, fields, models
from odoo.exceptions import AccessError


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
    widget = fields.Selection([
        ('score', 'Score'),
        ('trend', 'Trend'),
        ('records', 'Records'),
        ('issues', 'Issues'),
        ('by_model', 'By model'),
        ('by_company', 'By company'),
        ('lowest', 'Lowest scores'),
        ('recent', 'Recent issues'),
    ], required=True)
    sequence = fields.Integer(default=10)
    visible = fields.Boolean(default=True)

    _user_widget_unique = models.Constraint(
        'UNIQUE(user_id, widget)', 'A widget has one place per user.')

    @api.model
    def _access_domain(self, operation):
        """Everyone sees, creates and changes only their own lines; superuser and sudo are not filtered.

        Requirements: FR-DGQ-0037
        """
        domain = super()._access_domain(operation)
        return domain & fields.Domain('user_id', '=', self.env.uid)

    def write(self, vals):
        """A line never changes hands: its owner cannot be written outside sudo.

        Requirements: FR-DGQ-0037
        """
        if 'user_id' in vals and not self.env.su and vals['user_id'] != self.env.uid:
            raise AccessError(self.env._("You can only change your own dashboard layout."))
        return super().write(vals)
