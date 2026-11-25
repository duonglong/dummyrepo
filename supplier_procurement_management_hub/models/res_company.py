from odoo import api, fields, models
from odoo.exceptions import ValidationError

RISK_ACTIONS = [('none', 'None'), ('warn', 'Warn'), ('block', 'Block')]
SPM_DAY_FIELDS = (
    'spm_on_time_tolerance_days', 'spm_delivery_escalation_days', 'spm_duplicate_bill_days',
    'spm_quote_reminder_days', 'spm_closing_soon_days', 'spm_requalification_months',
    'spm_portal_inactivity_days', 'spm_document_retention_years',
)
SPM_AUDITED_FIELDS = (
    'spm_supplier_control', 'spm_risk_low_action', 'spm_risk_medium_action',
    'spm_risk_high_action',
) + SPM_DAY_FIELDS


class ResCompany(models.Model):
    """Per-company procurement settings.

    Requirements: BR-SPM-0001, FR-SPM-0107
    """
    _name = 'res.company'
    _inherit = ['res.company', 'procurement.audit.mixin']

    spm_supplier_control = fields.Boolean(string="Supplier control")
    spm_risk_low_action = fields.Selection(RISK_ACTIONS, string="Low risk", default='none', required=True)
    spm_risk_medium_action = fields.Selection(RISK_ACTIONS, string="Medium risk", default='warn', required=True)
    spm_risk_high_action = fields.Selection(RISK_ACTIONS, string="High risk", default='block', required=True)
    spm_on_time_tolerance_days = fields.Integer(string="On-time tolerance (days)", default=0)
    spm_delivery_escalation_days = fields.Integer(string="Delivery escalation (days)", default=3)
    spm_duplicate_bill_days = fields.Integer(string="Duplicate bill window (days)", default=7)
    spm_quote_reminder_days = fields.Integer(string="Quote reminder (days)", default=2)
    spm_closing_soon_days = fields.Integer(string="Closing soon (days)", default=3)
    spm_requalification_months = fields.Integer(string="Re-qualification (months)", default=12)
    spm_portal_inactivity_days = fields.Integer(string="Portal inactivity (days)", default=365)
    spm_document_retention_years = fields.Integer(string="Document retention (years)", default=7)

    _spm_audited_fields = SPM_AUDITED_FIELDS

    @api.constrains(*SPM_DAY_FIELDS)
    def _check_spm_delays(self):
        """Requirements: FR-SPM-0108"""
        for company in self:
            for name in SPM_DAY_FIELDS:
                if company[name] < 0:
                    raise ValidationError(self.env._("%s cannot be negative.", company._fields[name]._description_string(self.env)))
