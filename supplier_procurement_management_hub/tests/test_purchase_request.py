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


class TestPurchaseRequestAccess(TransactionCase):
    """Requirements: FR-SPM-0106, FR-SPM-0030"""

    def setUp(self):
        super().setUp()
        Users = self.env['res.users'].with_context(no_reset_password=True)
        self.manager_user = Users.create({
            'name': 'Dana DM', 'login': 'spm_dana_dm', 'email': 'dana_dm@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_department_manager').id])]})
        self.other_manager_user = Users.create({
            'name': 'Omar DM', 'login': 'spm_omar_dm', 'email': 'omar_dm@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_department_manager').id])]})
        self.ann = Users.create({
            'name': 'Ann R', 'login': 'spm_ann_acc', 'email': 'ann_acc@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_requester').id])]})
        Employee = self.env['hr.employee']
        dana = Employee.create({'name': 'Dana', 'user_id': self.manager_user.id})
        omar = Employee.create({'name': 'Omar', 'user_id': self.other_manager_user.id})
        self.sales = self.env['hr.department'].create({'name': 'Sales SPM', 'manager_id': dana.id})
        self.ops = self.env['hr.department'].create({'name': 'Ops SPM', 'manager_id': omar.id})
        product = self.env['product.product'].create({'name': 'Chair', 'type': 'consu'})
        self.request = self._request(self.sales, product)
        self.other_request = self._request(self.ops, product)

    def _request(self, department, product):
        return self.env['procurement.request'].create({
            'requester_id': self.ann.id, 'department_id': department.id, 'needed_date': '2027-02-01',
            'reason': 'Growth', 'line_ids': [(0, 0, {'product_id': product.id, 'name': product.name, 'quantity': 1})]})

    def test_department_manager_reads_the_requests_of_their_department(self):
        """FR-SPM-0106: a department manager reads the requests and lines of their own department only."""
        Request = self.env['procurement.request'].with_user(self.manager_user)
        domain = [('id', 'in', (self.request | self.other_request).ids)]
        self.assertEqual(Request.search(domain), self.request)
        self.assertEqual(
            self.env['procurement.request.line'].with_user(self.manager_user).search(
                [('request_id', 'in', (self.request | self.other_request).ids)]).request_id,
            self.request)

    def test_department_manager_cannot_change_a_request(self):
        """FR-SPM-0106: the department manager's access is read only."""
        with self.assertRaises(AccessError):
            self.request.with_user(self.manager_user).write({'reason': 'Changed'})

    def test_requester_deletes_their_own_draft(self):
        """FR-SPM-0030: a requester deletes a draft they created."""
        self.request.with_user(self.ann).unlink()
        self.assertFalse(self.request.exists())

    def test_requester_cannot_delete_a_submitted_request(self):
        """FR-SPM-0030: once submitted a request stays."""
        self.request.with_user(self.ann).action_spm_submit()
        with self.assertRaises(AccessError):
            self.request.with_user(self.ann).unlink()
