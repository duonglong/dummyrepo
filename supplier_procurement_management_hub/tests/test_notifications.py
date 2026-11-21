"""Own tests of the notification service and the reminder jobs (T-034).

Requirements: BR-SPM-0019, FR-SPM-0092, FR-SPM-0094, FR-SPM-0095
"""
from freezegun import freeze_time

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
