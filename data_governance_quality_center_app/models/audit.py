from odoo import fields, models
from odoo.exceptions import UserError


class DataQualityAudit(models.Model):
    """One entry of the governance trail, written by ``services.audit.log`` and never changed.

    Requirements: FR-DGQ-0072, FR-DGQ-0073
    """
    _name = 'data.quality.audit'
    _description = 'Data Quality Audit Entry'
    _order = 'date desc, id desc'
    _log_access = False

    date = fields.Datetime(
        string='Time', required=True, index=True, default=fields.Datetime.now, readonly=True)
    user_id = fields.Many2one('res.users', string='User', ondelete='set null', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', index=True, readonly=True)
    event = fields.Selection([
        ('rule_created', 'Rule created'),
        ('rule_changed', 'Rule changed'),
        ('rule_deleted', 'Rule deleted'),
        ('issue_assigned', 'Issue assigned'),
        ('issue_resolved', 'Issue resolved'),
        ('issue_ignored', 'Issue ignored'),
        ('issue_reopened', 'Issue reopened'),
        ('scan_started', 'Scan started'),
        ('scan_ended', 'Scan ended'),
        ('correction_approved', 'Correction approved'),
        ('correction_run', 'Correction run'),
        ('correction_rolled_back', 'Correction rolled back'),
        ('merge_done', 'Merge done'),
        ('merge_rolled_back', 'Merge rolled back'),
        ('setting_changed', 'Setting changed'),
        ('purge', 'Audit purge'),
    ], string='Event', required=True, index=True, readonly=True)
    res_model = fields.Char(string='Model', index=True, readonly=True)
    res_id = fields.Integer(string='Record ID', readonly=True)
    record_name = fields.Char(string='Record', readonly=True)
    field_names = fields.Char(string='Fields', readonly=True)
    changes = fields.Json(
        string='Values', readonly=True,
        help="Each changed field with its value before and after. Values of sensitive fields "
             "are never kept, only the fact that they changed.")
    description = fields.Char(string='Description', readonly=True)

    def write(self, vals):
        """Refuse every change, whoever asks.

        Requirements: FR-DGQ-0073
        """
        raise UserError(self.env._("Audit entries cannot be changed."))

    def unlink(self):
        """Refuse every deletion, whoever asks; the purge is the only way entries go.

        Requirements: FR-DGQ-0073
        """
        raise UserError(self.env._("Audit entries cannot be deleted. Use the purge."))
