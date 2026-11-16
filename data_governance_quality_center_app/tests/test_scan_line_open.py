from odoo.exceptions import UserError

from .acceptance_helpers import AcceptanceCase


class TestScanLineOpen(AcceptanceCase):
    """A result line of a scan opens the record behind it, as an issue does.

    The screen is not driven: the arch of the scan form is read for the button, and the
    button's method is called as the user who clicks it.

    Requirements: FR-DGQ-0030
    """

    def setUp(self):
        super().setUp()
        self.operator = self.user('operator', company=self.cx)
        self.rule = self.make_rule('product.template', 'range', ['x_dgq_cost'], has_max=True, max_value=10)

    def line_of(self, product):
        """The result line a finished scan wrote for the product."""
        product.x_dgq_cost = 20
        scan = self.run_scan([self.rule], models=['product.template'])
        return self.failing(scan, self.rule)[product.id]

    def test_bug_b_0224_the_result_list_of_a_scan_has_an_open_record_button(self):
        """Regression test for B-0224: the result lines in the scan form carry the Open Record button.

        It cannot click the button: it reads the arch of the form the list sits in.

        Requirements: FR-DGQ-0030
        """
        arch = self.env['data.quality.scan'].get_view(view_type='form')['arch']
        results = arch.split('name="line_ids"', 1)[1].split('</field>', 1)[0]
        self.assertIn('name="action_open_record"', results)

    def test_bug_b_0224_a_result_line_opens_the_record_under_the_users_rights(self):
        """Regression test for B-0224 (SC-DGQ-0030-1): the user is taken to the form of the failing record.

        Requirements: FR-DGQ-0030
        """
        product = self.product('Costly')
        action = self.line_of(product).with_user(self.operator).action_open_record()
        self.assertEqual(
            (action['res_model'], action['res_id'], action['view_mode']), ('product.template', product.id, 'form'))

    def test_bug_b_0224_a_result_line_of_a_deleted_record_gives_a_message(self):
        """Regression test for B-0224 (SC-DGQ-0030-2): a deleted record is a message, not an error page.

        Requirements: FR-DGQ-0030
        """
        product = self.product('Gone')
        line = self.line_of(product)
        product.unlink()
        with self.assertRaisesRegex(UserError, 'no longer exists'):
            line.with_user(self.operator).action_open_record()

    def test_bug_b_0224_a_result_line_of_a_record_the_user_may_not_read_gives_a_message(self):
        """Regression test for B-0224 (SC-DGQ-0030-2): a record of another company is refused with a message.

        Requirements: FR-DGQ-0030
        """
        other = self.product('Other company', company=self.cy)
        line = self.line_of(self.product('Costly'))
        line.sudo().res_id = other.id
        with self.assertRaisesRegex(UserError, 'may not read'):
            line.with_user(self.operator).action_open_record()
