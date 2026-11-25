"""Acceptance tests of T-012 (purchase requests), written from the specification.

Requirements: FR-SPM-0030

The request form is driven with Form (its fields are named by the design:
department, needed-by date, reason and lines of product or description,
quantity and unit); the Submit button is procurement.request.action_spm_submit().
"""
from datetime import date

from odoo.exceptions import AccessError
from odoo.tests import Form
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestPurchaseRequestAcceptance(TransactionCase):
    """Requirements: FR-SPM-0030"""

    def setUp(self):
        super().setUp()
        env = self.env
        partner_manager = env.ref('base.group_partner_manager').id

        def user(name, login, group, company=None):
            company = company or env.company
            return env['res.users'].with_context(no_reset_password=True).create({
                'name': name, 'login': login, 'email': f'{login}@example.com',
                'company_id': company.id, 'company_ids': [(6, 0, company.ids)],
                'group_ids': [(6, 0, [env.ref(f'{MODULE}.{group}').id, partner_manager])],
            })
        self.ann = user('Ann Requester', 'spm_ann_m2', 'group_spm_requester')
        self.bob = user('Bob Requester', 'spm_bob_m2', 'group_spm_requester')
        self.buyer = user('Bea Buyer', 'spm_buyer_m2r', 'group_spm_buyer')
        self.other_company = env['res.company'].create({'name': 'Other Co M2'})
        self.other_buyer = user('Otto Buyer', 'spm_otto_m2', 'group_spm_buyer', self.other_company)
        self.facilities = env['hr.department'].create({'name': 'Facilities'})
        self.chair = env['product.product'].create({'name': 'Office chair', 'type': 'consu'})
        self.unit = env.ref('uom.product_uom_unit')

    def _draft(self, user, quantity=40.0, needed='2027-01-15', reason='New floor'):
        form = Form(self.env['procurement.request'].with_user(user))
        form.department_id = self.facilities
        form.needed_date = needed
        form.reason = reason
        with form.line_ids.new() as line:
            line.product_id = self.chair
            line.quantity = quantity
            line.uom_id = self.unit
        return form.save()

    def _notified(self, request, user):
        messages = self.env['mail.message'].sudo().search([
            ('model', '=', 'procurement.request'), ('res_id', '=', request.id)])
        partners = messages.partner_ids | messages.notification_ids.res_partner_id
        return user.partner_id in partners

    def test_sc_0030_1_requester_submits_a_request(self):
        """SC-SPM-0030-1: a Requester of Facilities submits 40 chairs needed by
        2027-01-15 'New floor'; it is Submitted and the buyers of the company
        are notified (not a buyer of another company)."""
        request = self._draft(self.ann)
        self.assertEqual(request.state, 'draft')
        request.with_user(self.ann).action_spm_submit()
        self.env.flush_all()
        self.assertEqual(request.state, 'submitted')
        self.assertEqual(request.requester_id, self.ann)
        self.assertEqual(request.department_id, self.facilities)
        self.assertEqual(request.needed_date, date(2027, 1, 15))
        self.assertEqual(request.reason, 'New floor')
        self.assertEqual(request.line_ids.quantity, 40.0)
        self.assertTrue(self._notified(request, self.buyer))
        self.assertFalse(self._notified(request, self.other_buyer))

    def test_sc_0030_2_requester_sees_only_her_own_requests(self):
        """SC-SPM-0030-2: Ann opens her requests and sees only her own, with
        its status; Bob's request cannot be read by her."""
        ann_request = self._draft(self.ann)
        bob_request = self._draft(self.bob, quantity=5.0)
        ann_request.with_user(self.ann).action_spm_submit()
        bob_request.with_user(self.bob).action_spm_submit()
        seen = self.env['procurement.request'].with_user(self.ann).search([])
        self.assertEqual(seen, ann_request)
        self.assertEqual(seen.state, 'submitted')
        with self.assertRaises(AccessError):
            bob_request.with_user(self.ann).read(['name'])

    def test_sc_0030_1_buyers_see_the_requests_of_their_company(self):
        """FR-SPM-0030: a Buyer sees the requests of the company; a buyer of
        another company sees none."""
        request = self._draft(self.ann)
        request.with_user(self.ann).action_spm_submit()
        self.assertEqual(self.env['procurement.request'].with_user(self.buyer).search([]), request)
        self.assertFalse(self.env['procurement.request'].with_user(self.other_buyer).search([]))
