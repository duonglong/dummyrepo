from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services import audit

RISK_BAND_KEYS = {'spm_risk_medium_from': 'spm.risk_medium_from',
                  'spm_risk_high_from': 'spm.risk_high_from'}


class ResConfigSettings(models.TransientModel):
    """The Procurement Hub block of the settings screen.

    Requirements: FR-SPM-0108, BR-SPM-0018
    """
    _inherit = 'res.config.settings'

    spm_supplier_control = fields.Boolean(related='company_id.spm_supplier_control', readonly=False)
    spm_risk_low_action = fields.Selection(related='company_id.spm_risk_low_action', readonly=False)
    spm_risk_medium_action = fields.Selection(related='company_id.spm_risk_medium_action', readonly=False)
    spm_risk_high_action = fields.Selection(related='company_id.spm_risk_high_action', readonly=False)
    spm_on_time_tolerance_days = fields.Integer(related='company_id.spm_on_time_tolerance_days', readonly=False)
    spm_delivery_escalation_days = fields.Integer(related='company_id.spm_delivery_escalation_days', readonly=False)
    spm_duplicate_bill_days = fields.Integer(related='company_id.spm_duplicate_bill_days', readonly=False)
    spm_quote_reminder_days = fields.Integer(related='company_id.spm_quote_reminder_days', readonly=False)
    spm_closing_soon_days = fields.Integer(related='company_id.spm_closing_soon_days', readonly=False)
    spm_requalification_months = fields.Integer(related='company_id.spm_requalification_months', readonly=False)
    spm_portal_inactivity_days = fields.Integer(related='company_id.spm_portal_inactivity_days', readonly=False)
    spm_document_retention_years = fields.Integer(related='company_id.spm_document_retention_years', readonly=False)
    spm_risk_medium_from = fields.Integer(string="Medium risk from", default=30,
                                          config_parameter='spm.risk_medium_from')
    spm_risk_high_from = fields.Integer(string="High risk from", default=60,
                                        config_parameter='spm.risk_high_from')

    @api.constrains('spm_risk_medium_from', 'spm_risk_high_from')
    def _check_spm_risk_bands(self):
        """Requirements: FR-SPM-0108"""
        for settings in self:
            if not 0 <= settings.spm_risk_medium_from < settings.spm_risk_high_from <= 100:
                raise ValidationError(self.env._("The medium risk band must start below the high risk band, which is at most 100."))

    def set_values(self):
        """Save the settings and audit a change of the shared risk bands.

        Requirements: BR-SPM-0018, FR-SPM-0108

        The company values are audited by the company itself (audit mixin);
        the bands are system parameters, so they are compared here.
        """
        params = self.env['ir.config_parameter'].sudo()
        old = {name: params.get_str(key, '') for name, key in RISK_BAND_KEYS.items()}
        result = super().set_values()
        new = {name: params.get_str(key, '') for name, key in RISK_BAND_KEYS.items()}
        changed = [name for name in new if new[name] != old[name]]
        if changed:
            audit.log(self.env, 'configuration', self.env.company,
                      {name: old[name] for name in changed},
                      {name: new[name] for name in changed}, 'settings')
        return result
