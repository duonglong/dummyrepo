"""Acceptance tests of T-005 (onboarding applications: configurable fields),
written from the specification.

Requirements: FR-SPM-0002

The invitation, submission and application-opening scenarios of T-005
(SC-SPM-0001-x, SC-SPM-0002-1, SC-SPM-0003-x) are not here: the design names no
method, route or field key for them yet.
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestOnboardingFieldConfigAcceptance(TransactionCase):
    """Requirements: FR-SPM-0002"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.goods = self.env['procurement.supplier.type'].create({'name': 'Goods T005'})

    def _key(self, word):
        selection = self.env['procurement.supplier.type.field']._fields['field_key'].selection
        keys = [k for k, label in selection if word in k or word in str(label).lower()]
        self.assertTrue(keys, f"a field key for '{word}' exists")
        return keys[0]

    def _rule(self, key, visibility):
        return self.env['procurement.supplier.type.field'].create({
            'type_id': self.goods.id, 'field_key': key, 'visibility': visibility})

    def test_sc_0002_2_legal_name_cannot_be_hidden_on_create(self):
        """SC-SPM-0002-2: marking Legal name hidden is refused with a message
        that legal name is always required."""
        with self.assertRaises((ValidationError, UserError)) as ctx:
            self._rule(self._key('legal'), 'hidden')
        self.assertIn('legal name', str(ctx.exception).lower())

    def test_sc_0002_2_legal_name_cannot_be_hidden_on_write(self):
        """SC-SPM-0002-2: changing an existing Legal name rule to hidden is refused too."""
        rule = self._rule(self._key('legal'), 'required')
        with self.assertRaises((ValidationError, UserError)) as ctx:
            rule.write({'visibility': 'hidden'})
        self.assertIn('legal name', str(ctx.exception).lower())

    def test_sc_0002_2_another_field_can_be_hidden(self):
        """FR-SPM-0002 boundary: only legal name (and e-mail) are always
        required; hiding the website is accepted."""
        rule = self._rule(self._key('website'), 'hidden')
        self.assertEqual(rule.visibility, 'hidden')
