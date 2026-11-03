from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged('post_install', '-at_install')
class TestPosScreenData(TestPointOfSaleHttpCommon):
    """Data the point of sale screen needs to double the points."""

    def test_company_timezone_is_loaded(self):
        """Requirements: FR-DLP-007, NFR-REL-001: the screen decides the weekend in the company timezone."""
        self.main_pos_config.company_id.tz = 'Europe/Brussels'
        self.main_pos_config.with_user(self.pos_user).open_ui()
        session = self.main_pos_config.current_session_id
        data = session.load_data({'only_records': True})
        company = data['res.company'][0]
        self.assertEqual(company['tz'], 'Europe/Brussels')
