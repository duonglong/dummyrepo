"""Own tests of the notification service and the reminder jobs (T-034).

Requirements: BR-SPM-0019, FR-SPM-0091, FR-SPM-0092, FR-SPM-0093, FR-SPM-0094, FR-SPM-0095
"""
from freezegun import freeze_time

from odoo.exceptions import UserError

from ..services import notification
from .contract_common import SpmContractCase
from .delivery_common import SpmDeliveryCase


class TestNotificationService(SpmDeliveryCase):
    """Requirements: BR-SPM-0019, FR-SPM-0095"""

    def setUp(self):
        super().setUp()
        self.event = self._event(self.beta, title='Chairs svc')
        self.template = self.env.ref('supplier_procurement_management_hub.mail_template_quote_reminder')

    def test_br_0019_claim_is_true_once_per_key(self):
        """BR-SPM-0019: the second claim of the same key is refused, a new due date is a new key."""
        partner = self.beta_user.partner_id
        self.assertTrue(notification.claim(self.env, 'k', self.event, partner, '2026-12-01'))
        self.assertFalse(notification.claim(self.env, 'k', self.event, partner, '2026-12-01'))
        self.assertTrue(notification.claim(self.env, 'k', self.event, partner, '2026-12-02'))

    def test_br_0019_send_once_sends_one_message(self):
        """BR-SPM-0019, FR-SPM-0095: two calls with the same key send one message made from the template."""
        partner = self.beta_user.partner_id
        first = notification.send_once(self.env, 'k', self.event, partner, '2026-12-01', self.template)
        second = notification.send_once(self.env, 'k', self.event, partner, '2026-12-01', self.template)
        self.assertEqual((first, second), (1, 0))
        message = self.env['mail.message'].search([
            ('model', '=', self.event._name), ('res_id', '=', self.event.id), ('partner_ids', 'in', partner.ids)])
        self.assertEqual(len(message), 1)
        self.assertIn(self.event.title, message.subject)

    def test_fr_0092_answered_supplier_is_not_waited_for(self):
        """FR-SPM-0092: an event whose only invited supplier answered has nobody left to remind."""
        self.assertIn(self.event, notification.open_events_waiting(self.env))
        line = self.event.line_ids
        self._response(self.event, self.beta).with_user(self.beta_user).spm_submit_version([{
            'event_line_id': line.id, 'price_unit': 12.5, 'discount': 0.0, 'lead_time_days': 10}])
        self.assertNotIn(self.event, notification.open_events_waiting(self.env))


class TestNotificationOnceKeys(SpmDeliveryCase):
    """Requirements: BR-SPM-0019, FR-SPM-0091, FR-SPM-0093"""

    def _document(self, doc_type, partner=None):
        return self.env['procurement.supplier.document'].with_user(self.buyer).create({
            'partner_id': (partner or self.acme).id, 'document_type_id': doc_type.id,
            'issue_date': '2026-01-01', 'expiry_date': '2026-12-15', 'verification_state': 'verified'})

    def test_fr_0091_two_documents_of_one_supplier_give_two_activities(self):
        """FR-SPM-0091: the buyer's to-do is once per document and due date, not once per supplier."""
        self.acme.spm_responsible_user_id = self.buyer
        types = self.env['procurement.document.type'].create([
            {'name': 'Insurance own', 'reminder_days': 30, 'needs_expiry': True},
            {'name': 'ISO own', 'reminder_days': 30, 'needs_expiry': True}])
        for doc_type in types:
            notification.remind_document(self.env, self._document(doc_type))
        activities = self.env['mail.activity'].sudo().search([
            ('user_id', '=', self.buyer.id), ('res_model', '=', 'res.partner'), ('res_id', '=', self.acme.id)])
        self.assertEqual(len(activities), 2)

    def test_fr_0093_two_lines_of_one_order_give_two_activities(self):
        """FR-SPM-0093: the to-do is keyed on the line, so two late lines of one order both get one."""
        order = self._dorder()
        for line in (order.order_line, self._dorder().order_line):
            notification.schedule_once(
                self.env, 'delivery_overdue', line, self.buyer, '2026-12-12', 'Chase', target=order)
        count = self.env['mail.activity'].sudo().search_count([
            ('user_id', '=', self.buyer.id), ('res_model', '=', 'purchase.order'), ('res_id', '=', order.id)])
        self.assertEqual(count, 2)

    def test_br_0001_key_has_the_company_of_its_record_and_follows_the_company_rule(self):
        """BR-SPM-0001: a manager of another company does not read the key of this company's event."""
        company_b = self.env['res.company'].create({'name': 'Company B T034'})
        event = self._event(self.beta, title='Keys own')
        notification.claim(self.env, 'k', event, self.beta_user.partner_id, '2026-12-01')
        key = self.env['procurement.notification.log'].search([('res_id', '=', event.id)])
        self.assertEqual(key.company_id, event.company_id)
        manager_b = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Manager B', 'login': 'spm_mgr_b_t034', 'company_id': company_b.id,
            'company_ids': [(6, 0, company_b.ids)],
            'group_ids': [(6, 0, [self.env.ref('supplier_procurement_management_hub.group_spm_manager').id])]})
        self.assertFalse(self.env['procurement.notification.log'].with_user(manager_b).search([
            ('id', '=', key.id)]))

    def test_br_0019_a_recipient_without_address_is_not_claimed(self):
        """BR-SPM-0019: nothing is claimed for a partner no mail can reach, so a later run retries it."""
        partner = self.env['res.partner'].create({'name': 'No address T034'})
        event = self._event(self.beta, title='Unreached own')
        missing = []
        sent = notification.send_once(self.env, 'k', event, partner, '2026-12-01', self.env.ref(
            'supplier_procurement_management_hub.mail_template_quote_reminder'), missing)
        self.assertEqual((sent, missing), (0, [partner.display_name]))
        self.assertTrue(notification.claim(self.env, 'k', event, partner, '2026-12-01'))

    def test_br_0019_a_missing_buyer_is_reported_when_nothing_else_was_done(self):
        """BR-SPM-0019: a supplier without portal user and without buyer is an error of the run."""
        lonely = self.env['res.partner'].create({'name': 'Lonely T034', 'is_company': True})
        doc_type = self.env['procurement.document.type'].create({
            'name': 'Lonely type', 'reminder_days': 30, 'needs_expiry': True})
        with self.assertRaises(UserError):
            notification.remind_document(self.env, self._document(doc_type, lonely))


class TestContractReminderPeriod(SpmContractCase):
    """Requirements: FR-SPM-0094"""

    def test_fr_0094_default_period_and_own_period(self):
        """FR-SPM-0094: the reminder period is 30 days by default and a contract may set its own."""
        contract = self._active_contract(end='2027-12-31', notice=60)
        self.assertEqual(contract.reminder_days, 30)
        contract.reminder_days = 5
        with freeze_time('2027-10-02 08:00:00'):
            self.assertEqual(notification.remind_contract(self.env, contract), 0)
        with freeze_time('2027-10-28 08:00:00'):
            self.assertEqual(notification.remind_contract(self.env, contract), 1)

    def test_nfr_0009_reminder_day_is_the_day_of_the_company(self):
        """NFR-SPM-0009: at 20:00 UTC on 2027-10-01 it is already 2027-10-02 in Auckland: 30 days before."""
        contract = self._active_contract(end='2027-12-31', notice=60)
        self.company.partner_id.tz = 'Pacific/Auckland'
        with freeze_time('2027-10-01 20:00:00'):
            self.assertEqual(notification.remind_contract(self.env, contract), 1)

    def test_br_0019_owner_without_address_is_not_claimed_for_the_mail(self):
        """BR-SPM-0019: an owner no mail can reach has no mail key (a later run retries), the to-do is kept."""
        contract = self._active_contract(end='2027-12-31', notice=60)
        contract.owner_id.partner_id.email = False
        with freeze_time('2027-10-02 08:00:00'):
            self.assertEqual(notification.remind_contract(self.env, contract), 0)
        kinds = self.env['procurement.notification.log'].search([('res_id', '=', contract.id)]).mapped('kind')
        self.assertEqual(kinds, ['contract_notice_activity'])
