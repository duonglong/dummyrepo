"""Shared fixtures of the milestone 2 acceptance tests (T-010 to T-015).

Requirements: BR-SPM-0002, FR-SPM-0016, FR-SPM-0017

Names not fixed by the design are chosen here and recorded in the module notes:
- procurement.request.action_spm_submit(); action_spm_create_sourcing_event()
- procurement.sourcing.event.action_spm_send(); action_spm_cancel(reason)
  (the reason dialog procurement.reason.wizard gets the action key ``cancel``)
- procurement.sourcing.response.spm_submit_version(lines, attachment_ids=None, **terms)
  and spm_enter_quotation(lines, attachment_ids=None, **terms); a line is a dict with
  event_line_id, price_unit, discount, lead_time_days
- procurement.sourcing.question.spm_ask(event_id, text) (model method, run as the portal user);
  procurement.sourcing.question.action_spm_answer(answer, shared=False)
- procurement.supplier.change.action_spm_verify(); portal profile form keys are the
  contact field names plus bank_account_number, bank_name, bank_bic, bank_holder;
  the upload form keys are document_type_id, issue_date, expiry_date and the file ``file``.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import HttpCase

MODULE = 'supplier_procurement_management_hub'
PASSWORD = 'spm-portal-pass'


class SpmPortalCase(HttpCase):
    """Users, suppliers and portal helpers shared by the milestone 2 tests."""

    def setUp(self):
        super().setUp()
        env = self.env
        self.company = env.company
        partner_manager = env.ref('base.group_partner_manager').id

        def internal(name, login, *xmlids):
            return env['res.users'].with_context(no_reset_password=True).create({
                'name': name, 'login': login, 'email': f'{login}@example.com',
                'group_ids': [(6, 0, [env.ref(x).id for x in xmlids] + [partner_manager])],
            })
        self.buyer = internal('Bea Buyer', 'spm_buyer_m2', f'{MODULE}.group_spm_buyer')
        self.buyer2 = internal('Ben Buyer', 'spm_buyer2_m2', f'{MODULE}.group_spm_buyer')
        self.manager = internal('Mia Manager', 'spm_manager_m2', f'{MODULE}.group_spm_manager')
        self.finance = internal('Fay Finance', 'spm_finance_m2', f'{MODULE}.group_spm_finance')
        self.requester = internal('Rex Requester', 'spm_requester_m2', f'{MODULE}.group_spm_requester')
        self.approved = env['procurement.supplier.status'].search(
            [('kind', '=', 'approved'), ('default_for_kind', '=', True)], limit=1)
        self.blocked = env['procurement.supplier.status'].search(
            [('kind', '=', 'blocked'), ('default_for_kind', '=', True)], limit=1)
        self.acme = self._supplier('Acme')
        self.beta = self._supplier('Beta')
        self.gamma = self._supplier('Gamma')
        self.acme_user = self._portal_user(self.acme, 'Jane Acme', 'spm_acme_m2')
        self.beta_user = self._portal_user(self.beta, 'Bob Beta', 'spm_beta_m2')
        self.gamma_user = self._portal_user(self.gamma, 'Gus Gamma', 'spm_gamma_m2')
        self.chair = env['product.product'].create({'name': 'Office chair M2', 'type': 'consu'})
        self.unit = env.ref('uom.product_uom_unit')

    # fixtures ------------------------------------------------------------
    def _supplier(self, name):
        return self.env['res.partner'].create({
            'name': name, 'is_company': True, 'spm_status_id': self.approved.id,
            'email': f'{name.lower()}@supplier.example'})

    def _portal_user(self, supplier, name, login):
        contact = self.env['res.partner'].create({
            'name': name, 'parent_id': supplier.id, 'type': 'contact',
            'email': f'{login}@supplier.example'})
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'partner_id': contact.id, 'login': login, 'password': PASSWORD,
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })

    def _event(self, partners, title='Chairs', deadline=None, send=True, quantity=100.0):
        """A sourcing event of the buyer for the given suppliers; sent (Open) by default."""
        event = self.env['procurement.sourcing.event'].with_user(self.buyer).create({
            'title': title, 'buyer_id': self.buyer.id,
            'deadline': deadline or fields.Datetime.now() + timedelta(days=10),
            'invited_partner_ids': [(6, 0, partners.ids)],
            'line_ids': [(0, 0, {
                'name': 'Chair', 'product_id': self.chair.id,
                'quantity': quantity, 'uom_id': self.unit.id})],
        })
        if send:
            event.with_user(self.buyer).action_spm_send()
        return event

    def _response(self, event, supplier):
        return event.response_ids.filtered(lambda r: r.partner_id == supplier)

    def _order(self, supplier, note=None):
        order = self.env['purchase.order'].create({
            'partner_id': supplier.id,
            'order_line': [(0, 0, {
                'product_id': self.chair.id, 'name': 'Chair', 'product_qty': 5.0,
                'price_unit': 10.0})],
        })
        order.button_confirm()
        return order

    # http ----------------------------------------------------------------
    def _login(self, user):
        self.authenticate(user.login, PASSWORD)

    def _post(self, url, data=None, files=None):
        data = dict(data or {}, csrf_token=self.csrf_token())
        return self.url_open(url, data=data, files=files, allow_redirects=False)

    def _get(self, url):
        return self.url_open(url, allow_redirects=False)

    # mail ----------------------------------------------------------------
    def _sent_to(self, partner, *needles):
        """True when a mail or message reached the partner and holds every needle."""
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
