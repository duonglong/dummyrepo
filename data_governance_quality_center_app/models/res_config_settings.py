from odoo import fields, models
from odoo.exceptions import ValidationError

from ..const import (
    ESCALATION_DAYS, ISSUE_RETENTION_DAYS, ISSUE_RETENTION_PARAMETER, NOTIFY_EMAIL_DEFAULT,
    NOTIFY_EMAIL_PARAMETER, SCAN_LINE_RETENTION_DAYS, SCAN_LINE_RETENTION_PARAMETER,
    SEVERITY_WEIGHT_DEFAULTS, SEVERITY_WEIGHT_PARAMETER)
from ..services import audit


class ResConfigSettings(models.TransientModel):
    """The app's block in Odoo's Settings: scoring weights, scan frequency, escalation, notifications, retention.

    Requirements: FR-DGQ-0076
    """
    _inherit = 'res.config.settings'

    dgq_weight_info = fields.Integer(
        string='Information weight', config_parameter=SEVERITY_WEIGHT_PARAMETER % 'info',
        default=SEVERITY_WEIGHT_DEFAULTS['info'],
        help="How much a failed information check counts in the quality score. A positive whole number, at most the warning weight.")
    dgq_weight_warning = fields.Integer(
        string='Warning weight', config_parameter=SEVERITY_WEIGHT_PARAMETER % 'warning',
        default=SEVERITY_WEIGHT_DEFAULTS['warning'],
        help="How much a failed warning check counts in the quality score. A positive whole number, at most the critical weight.")
    dgq_weight_critical = fields.Integer(
        string='Critical weight', config_parameter=SEVERITY_WEIGHT_PARAMETER % 'critical',
        default=SEVERITY_WEIGHT_DEFAULTS['critical'],
        help="How much a failed critical check counts in the quality score. A positive whole number.")
    dgq_scan_frequency = fields.Selection(
        [('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly')],
        string='Default scan frequency', config_parameter='data_quality.scan_frequency', default='daily',
        help="How often a new scheduled scan runs when its schedule does not say otherwise.")
    dgq_escalation_days = fields.Integer(
        string='Escalate after (days)', config_parameter='data_quality.escalation_days', default=ESCALATION_DAYS,
        help="Days an issue stays overdue before it is escalated.")
    dgq_notify_email = fields.Boolean(
        string='E-mail assignment notices', config_parameter=NOTIFY_EMAIL_PARAMETER,
        default=NOTIFY_EMAIL_DEFAULT,
        help="Also send an e-mail to the person an issue is assigned to; they are always notified in Odoo.")
    dgq_scan_line_retention_days = fields.Integer(
        string='Keep scan details (days)', config_parameter=SCAN_LINE_RETENTION_PARAMETER,
        default=SCAN_LINE_RETENTION_DAYS,
        help="Scan result lines and import files older than this are deleted by the daily cleanup. Scan totals and scores stay.")
    dgq_issue_retention_days = fields.Integer(
        string='Keep closed issues (days)', config_parameter=ISSUE_RETENTION_PARAMETER,
        default=ISSUE_RETENTION_DAYS,
        help="Resolved and Ignored issues closed longer ago than this are deleted by the daily cleanup.")

    def set_values(self):
        """Refuse weights or periods that are not allowed, apply the rest and write the change to the audit trail.

        Requirements: FR-DGQ-0076, FR-DGQ-0072
        """
        self._dgq_check_values()
        before = self.default_get([name for name in self._fields if name.startswith('dgq_')])
        super().set_values()
        changes = {
            name: [before[name], self[name]] for name in before if before[name] != self[name]}
        if changes:
            audit.log(self.env, 'setting_changed', changes=changes,
                      description=self.env._("Settings changed: %s", ", ".join(self._fields[name].string for name in changes)))

    def _dgq_check_values(self):
        """Weights are positive whole numbers with information <= warning <= critical; periods are at least one day.

        Requirements: FR-DGQ-0076
        """
        self.ensure_one()
        weights = (self.dgq_weight_info, self.dgq_weight_warning, self.dgq_weight_critical)
        if min(weights) < 1:
            raise ValidationError(self.env._("Every severity weight must be a positive whole number."))
        if self.dgq_weight_info > self.dgq_weight_warning:
            raise ValidationError(self.env._("The information weight cannot exceed the warning weight."))
        if self.dgq_weight_warning > self.dgq_weight_critical:
            raise ValidationError(self.env._("The warning weight cannot exceed the critical weight."))
        periods = (self.dgq_escalation_days, self.dgq_scan_line_retention_days, self.dgq_issue_retention_days)
        if min(periods) < 1:
            raise ValidationError(self.env._("The escalation delay and the retention periods must be at least 1 day."))
