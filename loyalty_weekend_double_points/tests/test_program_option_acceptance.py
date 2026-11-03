from odoo.tests import Form, tagged

from odoo.addons.sale.tests.common import SaleCommon

from .weekend_sale_helpers import WeekendSaleMixin


@tagged('post_install', '-at_install')
class TestProgramOptionAcceptance(WeekendSaleMixin, SaleCommon):
    """Acceptance tests of T-002: the option on the program and weekday behaviour."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['loyalty.program'].search([]).sudo().write({'active': False})

    def setUp(self):
        super().setUp()
        self._set_company_tz()

    def test_option_shown_and_off_on_new_loyalty_program(self):
        """Requirements: FR-DLP-001; scenario SC-DLP-001-1."""
        with Form(self.env['loyalty.program'].sudo()) as form:
            form.name = 'P'
            form.program_type = 'loyalty'
            self.assertFalse(form._get_modifier('weekend_double_points', 'invisible'))
            self.assertFalse(form.weekend_double_points)
        program = self.env['loyalty.program'].sudo().create({'name': 'P', 'program_type': 'loyalty'})
        self.assertFalse(program.weekend_double_points)

    def test_option_hidden_on_gift_card_program(self):
        """Requirements: FR-DLP-001, BR-DLP-003; scenario SC-DLP-001-2."""
        with Form(self.env['loyalty.program'].sudo()) as form:
            form.name = 'P'
            form.program_type = 'gift_card'
            self.assertTrue(form._get_modifier('weekend_double_points', 'invisible'))

    def test_option_resets_when_type_changes_to_ewallet(self):
        """Requirements: FR-DLP-001, BR-DLP-003; scenario SC-DLP-001-3."""
        program = self.env['loyalty.program'].sudo().create({
            'name': 'P', 'program_type': 'loyalty', 'weekend_double_points': True})
        self.assertTrue(program.weekend_double_points)
        program.program_type = 'ewallet'
        self.assertFalse(program.weekend_double_points)
        with Form(program.sudo()) as form:
            self.assertTrue(form._get_modifier('weekend_double_points', 'invisible'))

    def test_weekday_wednesday_earns_normal_points(self):
        """Requirements: FR-DLP-004; scenario SC-DLP-004-1."""
        program = self._make_program(weekend=True)
        self.assertTrue(program.weekend_double_points)
        self._confirm_order_at(self.WED_LOCAL, price=100.0)
        self.assertEqual(self._card_points(program), 100)

    def test_friday_2330_local_is_still_a_weekday(self):
        """Requirements: FR-DLP-004, BR-DLP-001; scenario SC-DLP-004-2 (just below the boundary)."""
        program = self._make_program(weekend=True)
        self.assertTrue(program.weekend_double_points)
        self._confirm_order_at(self.FRI_2330_LOCAL, price=100.0)
        self.assertEqual(self._card_points(program), 100)

    def test_monday_0000_local_is_a_weekday(self):
        """Requirements: FR-DLP-004, BR-DLP-001; boundary just above Sunday (Sunday 23:00 UTC)."""
        program = self._make_program(weekend=True)
        self.assertTrue(program.weekend_double_points)
        self._confirm_order_at(self.MON_0000_LOCAL, price=100.0)
        self.assertEqual(self._card_points(program), 100)

    def test_option_off_on_saturday_earns_normal_points(self):
        """Requirements: FR-DLP-004; scenario SC-DLP-004-3."""
        program = self._make_program(weekend=False)
        self.assertFalse(program.weekend_double_points)
        self._confirm_order_at(self.SAT_10H_LOCAL, price=100.0)
        self.assertEqual(self._card_points(program), 100)
