"""Shared fixtures of the approval acceptance tests (T-018, T-019, T-020).

Requirements: BR-SPM-0009, BR-SPM-0010, FR-SPM-0043, FR-SPM-0047

Names not fixed by the design are chosen here and recorded in the module notes:
- procurement.approval.policy.spm_evaluate(order) is the evaluation of the policies
  for an order (services.approval.evaluate): it returns a dict with 'policies' (the
  matching policies, a recordset), 'steps' (a list of dicts in step order, each with
  'policy_id', 'sequence', 'name', 'mode' and 'approver_ids', a list of user ids) and
  'cumulated_amount' (company currency); it refuses with a UserError naming the step
  when a step has no active approver;
- the order's Submit button is purchase.order.action_spm_submit_approval() (named by
  the design); the open approval request is the last of spm_approval_request_ids;
- procurement.approval.step.action_spm_approve(comment=None) and
  action_spm_reject(reason=None) are the Approve and Reject buttons (the reason
  dialog is left to the build), run as the deciding user;
- procurement.approval.step.spm_inbox() returns the inbox of the session user as a list
  of dicts (step_id, on_behalf_of_id, order_id), oldest first.
Approver types are users, manager, group; modes are all and any.
"""
from odoo import fields
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class SpmApprovalCase(TransactionCase):

    def setUp(self):
        super().setUp()
        env = self.env
        self.env = env(context=dict(env.context, tracking_disable=False))
        env = self.env
        self.company = env.company
        partner_manager = env.ref('base.group_partner_manager').id

        def user(name, login, group):
            return env['res.users'].with_context(no_reset_password=True).create({
                'name': name, 'login': login, 'email': f'{login}@example.com',
                'company_id': self.company.id, 'company_ids': [(6, 0, self.company.ids)],
                'group_ids': [(6, 0, [env.ref(f'{MODULE}.{group}').id, partner_manager])],
            })
        self.buyer = user('Bea Buyer', 'spm_buyer_m3', 'group_spm_buyer')
        self.ann = user('Ann Requester', 'spm_ann_m3', 'group_spm_requester')
        self.bob = user('Bob Approver', 'spm_bob_m3', 'group_spm_department_manager')
        self.carla = user('Carla Approver', 'spm_carla_m3', 'group_spm_department_manager')
        self.dave = user('Dave Other', 'spm_dave_m3', 'group_spm_department_manager')
        self.eve = user('Eve Approver', 'spm_eve_m3', 'group_spm_department_manager')
        self.auditor = user('Ada Auditor', 'spm_auditor_m3', 'group_spm_auditor')
        self.acme = env['res.partner'].create({'name': 'Acme M3', 'is_company': True})
        self.beta = env['res.partner'].create({'name': 'Beta M3', 'is_company': True})
        self.chair = env['product.product'].create({'name': 'Office chair M3A', 'type': 'consu'})
        self.it_category = env['product.category'].create({'name': 'IT M3'})
        self.laptop = env['product.product'].create({
            'name': 'Laptop M3', 'type': 'consu', 'categ_id': self.it_category.id})
        self.cable = env['product.product'].create({
            'name': 'Cable M3', 'type': 'consu', 'categ_id': self.it_category.id})
        self.unit = env.ref('uom.product_uom_unit')

    # people ----------------------------------------------------------------
    def _employees(self):
        """Ann reports to Bob who reports to Carla (the employee hierarchy)."""
        employee = self.env['hr.employee']
        carla = employee.create({'name': 'Carla', 'user_id': self.carla.id})
        bob = employee.create({'name': 'Bob', 'user_id': self.bob.id, 'parent_id': carla.id})
        ann = employee.create({'name': 'Ann', 'user_id': self.ann.id, 'parent_id': bob.id})
        return ann, bob, carla

    # policies --------------------------------------------------------------
    def _step(self, name, sequence, approver_type='users', users=None, mode='any', **extra):
        vals = {'name': name, 'sequence': sequence, 'approver_type': approver_type, 'mode': mode}
        if users:
            vals['user_ids'] = [(6, 0, users.ids)]
        vals.update(extra)
        return (0, 0, vals)

    def _policy(self, name='Policy', sequence=10, steps=(), **conditions):
        values = {'name': name, 'sequence': sequence, 'company_id': self.company.id,
                  'step_ids': list(steps)}
        for key in ('partner_ids', 'product_category_ids', 'product_ids'):
            if key in conditions:
                values[key] = [(6, 0, conditions.pop(key).ids)]
        values.update(conditions)
        return self.env['procurement.approval.policy'].create(values)

    # orders ----------------------------------------------------------------
    def _order(self, partner=None, price=600.0, qty=10.0, product=None, requester=None,
               currency=None, date_order=None, extra_lines=()):
        """A draft order of the Buyer: by default 10 chairs at 600 = 6,000 (company currency)."""
        values = {
            'partner_id': (partner or self.acme).id,
            'user_id': self.buyer.id,
            'order_line': [(0, 0, {
                'product_id': (product or self.chair).id, 'name': 'Line', 'product_qty': qty,
                'price_unit': price, 'tax_ids': [(6, 0, [])]})] + list(extra_lines),
        }
        if requester:
            values['spm_requester_id'] = requester.id
        if currency:
            values['currency_id'] = currency.id
        if date_order:
            values['date_order'] = date_order
        return self.env['purchase.order'].with_user(self.buyer).create(values)

    def _submit(self, order):
        order.with_user(self.buyer).action_spm_submit_approval()
        self.env.flush_all()
        return self._request(order)

    def _request(self, order):
        return order.spm_approval_request_ids.sorted('id')[-1:]

    def _approve_open_steps(self, order, comment=None):
        """Approve every open step as one of its approvers, until none is open."""
        request = self._request(order)
        guard = 0
        while request.step_ids.filtered(lambda s: s.state == 'open') and guard < 10:
            guard += 1
            for step in request.step_ids.filtered(lambda s: s.state == 'open'):
                for approver in step.approver_ids:
                    if step.state == 'open':
                        step.with_user(approver).action_spm_approve(comment)
        self.env.flush_all()
        return request

    def _steps(self, request):
        return request.step_ids.sorted(lambda s: (s.sequence, s.id))

    def _sent_to(self, partner, *needles):
        """True when a mail or message reached the partner and holds every needle."""
        self.env.flush_all()
        for mail in self.env['mail.mail'].sudo().search([]):
            reached = partner in mail.recipient_ids or (
                partner.email and partner.email.lower() in (mail.email_to or '').lower())
            body = f'{mail.subject or ""} {mail.body_html or ""}'
            if reached and all(n in body for n in needles):
                return True
        for message in self.env['mail.message'].sudo().search([]):
            reached = partner in message.partner_ids or partner in message.notification_ids.res_partner_id
            body = f'{message.subject or ""} {message.body or ""}'
            if reached and all(n in body for n in needles):
                return True
        return False

    def _today(self):
        return fields.Date.today()
