from odoo.tests.common import tagged

from .test_checks_b135_acceptance import CustomModelCase


@tagged('post_install', '-at_install')
class TestB5Order(CustomModelCase):
    """Requirements: FR-OPA-0020."""

    def test_only_costly_parts_of_a_longer_order_are_named(self):
        """FR-OPA-0020: in 'x_partner_id desc, id' the many2one is costly and id is not."""
        self.make_x_line(rows=200000, order='x_partner_id desc, id')
        found = self.findings(self.start_run(), 'B5').filtered(lambda f: f.table_name == 'x_line')
        self.assertEqual(len(found), 1)
        self.assertEqual(found.evidence['costly_fields'], [{'field': 'x_partner_id', 'reason': 'many2one'}])
