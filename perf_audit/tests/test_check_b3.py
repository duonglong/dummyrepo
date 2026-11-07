from odoo.tests.common import TransactionCase, tagged

from ..checks.b3_domain_index import domain_field_names, order_field_names


@tagged('post_install', '-at_install')
class TestDomainParsing(TransactionCase):
    """Requirements: FR-OPA-0018, FR-OPA-0020."""

    def test_domain_leaves_give_first_path_step(self):
        """FR-OPA-0018: dotted paths count their first step; operators and logic are not fields."""
        names = domain_field_names("['|', ('state', '=', 'open'), ('partner_id.country_id', 'in', [1])]")
        self.assertEqual(names, {'state', 'partner_id'})

    def test_unparsable_domain_gives_nothing(self):
        """FR-OPA-0018: text that is not Python reports no field."""
        self.assertEqual(domain_field_names('[(('), set())

    def test_order_parts(self):
        """FR-OPA-0020: directions and extra spaces are dropped."""
        self.assertEqual(order_field_names('priority desc, id asc'), ['priority', 'id'])
