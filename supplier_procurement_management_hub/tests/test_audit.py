"""Own tests of T-002: audit mixin, company settings and the protected trail.

Requirements: BR-SPM-0018, FR-SPM-0107, FR-SPM-0109
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase


class TestAuditCompanySettings(TransactionCase):
    """Requirements: BR-SPM-0018, FR-SPM-0108"""

    def setUp(self):
        super().setUp()
        self.company = self.env['res.company'].create({'name': 'Audit Co T002'})
        self.Log = self.env['procurement.audit.log']

    def test_company_setting_change_is_audited(self):
        """Requirements: BR-SPM-0018: a changed company setting writes an entry."""
        self.company.spm_quote_reminder_days = 5
        entry = self.Log.search([('res_id', '=', self.company.id), ('comment', '=', 'changed')])
        self.assertEqual(entry.old_values, {'spm_quote_reminder_days': 2})
        self.assertEqual(entry.new_values, {'spm_quote_reminder_days': 5})
        self.assertEqual(entry.company_id, self.company)
        self.assertEqual(entry.event, 'configuration')

    def test_unchanged_setting_writes_no_entry(self):
        """Requirements: BR-SPM-0018"""
        count = self.Log.search_count([('res_id', '=', self.company.id)])
        self.company.spm_quote_reminder_days = 2
        self.assertEqual(self.Log.search_count([('res_id', '=', self.company.id)]), count)

    def test_negative_delay_is_refused(self):
        """Requirements: FR-SPM-0108"""
        with self.assertRaises(ValidationError):
            self.company.spm_closing_soon_days = -1

    def test_superuser_cannot_delete_an_entry(self):
        """Requirements: BR-SPM-0018: refused for everyone, superuser included."""
        entry = self.Log.search([('res_id', '=', self.company.id)], limit=1)
        with self.assertRaises(UserError):
            entry.sudo().unlink()
        with self.assertRaises(UserError):
            entry.sudo().write({'comment': 'x'})

    def test_risk_band_change_is_audited(self):
        """Requirements: BR-SPM-0018"""
        settings = self.env['res.config.settings'].create({'spm_risk_medium_from': 35})
        settings.set_values()
        entry = self.Log.search([('comment', '=', 'settings')], limit=1)
        self.assertEqual(entry.new_values['spm_risk_medium_from'], '35')
