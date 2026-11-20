"""Unit tests of the risk services and guards (T-030).

Requirements: BR-SPM-0017, BR-SPM-0020, FR-SPM-0088, FR-SPM-0089, FR-SPM-0090
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase

from ..services import risk, supplier_control

MODULE = 'supplier_procurement_management_hub'


class TestRisk(TransactionCase):
    """Requirements: BR-SPM-0017, BR-SPM-0020, FR-SPM-0088, FR-SPM-0089, FR-SPM-0090"""

    def test_level_bands_follow_the_settings(self):
        """BR-SPM-0017: the limits come from the settings, by default 30 and 60."""
        self.assertEqual([risk.level_for(self.env, s) for s in (0, 29.9, 30, 59.9, 60, 100)],
                         ['low', 'low', 'medium', 'medium', 'high', 'high'])
        params = self.env['ir.config_parameter'].sudo()
        params.set_int('spm.risk_medium_from', 10)
        params.set_int('spm.risk_high_from', 20)
        self.assertEqual(risk.level_for(self.env, 15), 'medium')
        self.assertEqual(risk.level_for(self.env, 20), 'high')

    def test_weighted_total(self):
        """BR-SPM-0017: weighted average, 0 without weight."""
        self.assertAlmostEqual(risk.weighted_total([(20, 50), (80, 50)]), 50.0)
        self.assertAlmostEqual(risk.weighted_total([(20, 10), (80, 30)]), 65.0)
        self.assertEqual(risk.weighted_total([(50, 0)]), 0.0)

    def test_result_fields_are_refused_to_a_user(self):
        """FR-SPM-0088: a user cannot write the level of an assessment."""
        manager = self.env['res.users'].create({
            'name': 'M', 'login': 'risk_unit_manager', 'email': 'm@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_manager').id])]})
        partner = self.env['res.partner'].create({'name': 'Gamma'})
        with self.assertRaises(AccessError):
            self.env['procurement.risk.assessment'].with_user(manager).create({
                'partner_id': partner.id, 'level': 'low'})
        assessment = self.env['procurement.risk.assessment'].with_user(manager).create({
            'partner_id': partner.id})
        with self.assertRaises(UserError):
            assessment.action_spm_complete()  # no category

    def test_screening_cannot_be_changed_or_claim_integration(self):
        """FR-SPM-0089: a user's outcome is manual, never changed or deleted."""
        user = self.env['res.users'].create({
            'name': 'B', 'login': 'risk_unit_buyer', 'email': 'b@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_buyer').id])]})
        partner = self.env['res.partner'].create({'name': 'Delta'})
        screening = self.env['procurement.screening'].with_user(user).create({
            'partner_id': partner.id, 'source': 'List', 'result': 'match', 'origin': 'integration'})
        self.assertEqual(screening.origin, 'manual')
        self.assertEqual(screening.user_id, user)
        with self.assertRaises(UserError):
            screening.write({'result': 'clear'})
        with self.assertRaises(UserError):
            screening.unlink()

    def test_no_control_no_risk_finding(self):
        """FR-SPM-0090: a company without supplier control restricts nothing."""
        partner = self.env['res.partner'].create({'name': 'Eps'})
        partner.spm_risk_level = 'high'
        self.env.company.spm_supplier_control = False
        self.assertEqual(supplier_control.check(self.env, partner, 'confirm', self.env.company, partner), [])
