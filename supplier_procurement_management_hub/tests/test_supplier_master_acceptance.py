"""Acceptance tests of T-003 (supplier master), written from the specification.

Requirements: FR-SPM-0014, FR-SPM-0108, BR-SPM-0005
"""
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestSupplierMasterAcceptance(TransactionCase):
    """Requirements: FR-SPM-0014, FR-SPM-0108"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.buyer = self.env['res.users'].create({
            'name': 'Bea Buyer',
            'login': 'spm_buyer_t003',
            'email': 'spm_buyer_t003@example.com',
            'group_ids': [(6, 0, [
                self.env.ref(f'{MODULE}.group_spm_buyer').id,
                self.env.ref('base.group_partner_manager').id,
            ])],
        })

    def test_sc_0014_1_address_change_is_in_the_change_history(self):
        """SC-SPM-0014-1: a Buyer changes Acme's address; the history shows
        both values, the buyer, the date and origin back office."""
        acme = self.env['res.partner'].create({
            'name': 'Acme', 'is_company': True, 'street': 'Main Street 1'})
        acme.with_user(self.buyer).write({'street': 'Harbour Road 5'})
        self.env.flush_all()
        change = acme.spm_change_ids.filtered(
            lambda c: 'Harbour Road 5' in (c.new_value or ''))
        self.assertEqual(len(change), 1)
        self.assertIn('Main Street 1', change.old_value)
        self.assertEqual(change.user_id, self.buyer)
        self.assertTrue(change.date)
        self.assertEqual(change.origin, 'backoffice')

    def test_sc_0108_1_defaults_of_each_status_kind_and_document_types(self):
        """SC-SPM-0108-1 (statuses and document types): a fresh install ships
        a default status of each of the seven kinds, one default per kind for
        approved, conditional and re-qualification, and document types.
        The price tolerance part of the scenario is built by T-025."""
        Status = self.env['procurement.supplier.status']
        kinds = [k for k, _label in Status._fields['kind'].selection]
        self.assertEqual(
            set(kinds),
            {'prospect', 'in_review', 'approved', 'conditional',
             'requalification_due', 'suspended', 'blocked'})
        for kind in kinds:
            self.assertTrue(Status.search([('kind', '=', kind)]),
                            f"a default status of kind {kind}")
        for kind in ('approved', 'conditional', 'requalification_due'):
            self.assertEqual(
                len(Status.search([('kind', '=', kind), ('default_for_kind', '=', True)])),
                1, f"one default status for kind {kind}")
        self.assertTrue(self.env['procurement.document.type'].search([]))

    def test_sc_0108_1_configuration_change_writes_an_audit_entry(self):
        """BR-SPM-0018 / FR-SPM-0108: renaming a configured status (a
        configuration change) writes an audit entry holding old and new values."""
        status = self.env['procurement.supplier.status'].search([('kind', '=', 'approved')], limit=1)
        old_name = status.name
        status.write({'name': 'Preferred supplier'})
        self.env.flush_all()
        entries = self.env['procurement.audit.log'].search([
            ('res_model', '=', 'procurement.supplier.status'),
            ('res_id', '=', status.id)])
        self.assertTrue(entries)
        self.assertIn('Preferred supplier', str(entries.mapped('new_values')))
        self.assertIn(old_name, str(entries.mapped('old_values')))
