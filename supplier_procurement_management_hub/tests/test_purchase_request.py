"""Own tests of the purchase request guards (T-012).

Requirements: FR-SPM-0030
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestPurchaseRequest(TransactionCase):
    """Requirements: FR-SPM-0030"""

    def setUp(self):
        super().setUp()
        self.ann = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Ann R', 'login': 'spm_ann_req', 'email': 'ann_req@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_requester').id])],
        })
        self.product = self.env['product.product'].create({'name': 'Desk', 'type': 'consu'})

    def _draft(self):
        form = Form(self.env['procurement.request'].with_user(self.ann))
        form.needed_date = '2027-02-01'
        form.reason = 'Growth'
        with form.line_ids.new() as line:
            line.product_id = self.product
            line.quantity = 2
        return form.save()

    def test_line_defaults_and_numbering(self):
        """The line takes its description and unit from the product; the request is numbered."""
        request = self._draft()
        self.assertEqual(request.line_ids.name, self.product.display_name)
        self.assertEqual(request.line_ids.uom_id, self.product.uom_id)
        self.assertTrue(request.name.startswith('REQ'))
        self.assertEqual(request.requester_id, self.ann)

    def test_requester_cannot_write_state(self):
        """A Requester cannot set the state of a request directly."""
        request = self._draft()
        with self.assertRaises(AccessError):
            request.with_user(self.ann).write({'state': 'submitted'})

    def test_submit_twice_and_lines_locked(self):
        """A submitted request cannot be submitted again nor have its lines changed."""
        request = self._draft()
        request.with_user(self.ann).action_spm_submit()
        with self.assertRaises(UserError):
            request.with_user(self.ann).action_spm_submit()
        with self.assertRaises(UserError):
            request.line_ids.with_user(self.ann).write({'quantity': 9})

    def test_submit_needs_a_line(self):
        """An empty request is refused."""
        request = self._draft()
        request.line_ids.with_user(self.ann).unlink()
        with self.assertRaises(UserError):
            request.with_user(self.ann).action_spm_submit()
