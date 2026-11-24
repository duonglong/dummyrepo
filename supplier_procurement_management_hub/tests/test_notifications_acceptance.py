"""Acceptance tests of T-034 (reminder jobs and editable notification templates), written from the specification.

Requirements: BR-SPM-0019, FR-SPM-0091, FR-SPM-0092, FR-SPM-0093, FR-SPM-0094, FR-SPM-0095

Names the design does not fix, chosen here (see the module notes):
- the jobs are found by a word of their ``procurement.job`` code (document, quote/sourcing, deliver,
  contract, reminder, escalat, notif) and run with ``procurement.job._spm_run(code)``; the design lists
  document expiry, quote reminders, delivery escalation and contract reminders as scheduled actions, in
  one or several jobs, so every job matching a word is run;
- a notification is seen as a message or e-mail that reached the recipient's partner, an activity as a
  ``mail.activity`` of the buyer or owner on the record;
- the quote reminder template is the template of the sourcing event or response whose name holds
  "reminder"; a French edit is written by an Administrator with the French language in the context
  (the template form is not named by the specification, so the form step is left to the build).
"""
from freezegun import freeze_time

from .contract_common import SpmContractCase
from .delivery_common import SpmDeliveryCase

MODULE = 'supplier_procurement_management_hub'
JOB_WORDS = ('document', 'quote', 'sourcing', 'deliver', 'contract', 'reminder', 'notif', 'escalat', 'expir')


class SpmReminderMixin:
    """Helpers shared by the four reminder test classes."""

    def _run_jobs(self, when, words=JOB_WORDS):
        """Run, at ``when``, every scheduled job whose code holds one of ``words``."""
        codes = [c for c, _label in self.env['procurement.job']._fields['code'].selection
                 if any(w in c for w in words)]
        self.assertTrue(codes, "a scheduled job for the reminders exists")
        with freeze_time(when):
            for code in codes:
                self.env['procurement.job']._spm_run(code)
        self.env.flush_all()

    def _reach(self, partner):
        """The set of messages and e-mails that reached ``partner``."""
        self.env.flush_all()
        reached = set()
        messages = self.env['mail.message'].sudo().search([
            '|', ('partner_ids', 'in', partner.ids), ('notification_ids.res_partner_id', 'in', partner.ids)])
        reached |= {('m', m.id) for m in messages}
        mails = self.env['mail.mail'].sudo().search([])
        for mail in mails:
            if partner in mail.recipient_ids or (
                    partner.email and partner.email.lower() in (mail.email_to or '').lower()):
                reached.add(('m', mail.mail_message_id.id) if mail.mail_message_id else ('x', mail.id))
        return reached

    def _reach_text(self, partner, needle):
        """True when a message or e-mail holding ``needle`` reached ``partner``."""
        self.env.flush_all()
        for message in self.env['mail.message'].sudo().search([
                '|', ('partner_ids', 'in', partner.ids), ('notification_ids.res_partner_id', 'in', partner.ids)]):
            if needle in f'{message.subject or ""} {message.body or ""}':
                return True
        for mail in self.env['mail.mail'].sudo().search([]):
            reached = partner in mail.recipient_ids or (
                partner.email and partner.email.lower() in (mail.email_to or '').lower())
            if reached and needle in f'{mail.subject or ""} {mail.body_html or ""}':
                return True
        return False

    def _activity_domain(self, records):
        domain = []
        for index, rec in enumerate(records):
            if index < len(records) - 1:
                domain.insert(0, '|')
            domain += ['&', ('res_model', '=', rec._name), ('res_id', '=', rec.id)]
        return domain

    def _activity_count(self, user, *records):
        return self.env['mail.activity'].sudo().search_count(
            [('user_id', '=', user.id)] + self._activity_domain(records))


class TestDocumentExpiryReminders(SpmReminderMixin, SpmDeliveryCase):
    """Requirements: FR-SPM-0091, BR-SPM-0019"""

    def setUp(self):
        super().setUp()
        self.acme.spm_responsible_user_id = self.buyer
        self.insurance = self.env['procurement.document.type'].create({
            'name': 'Insurance T034', 'reminder_days': 30, 'needs_expiry': True})
        self.doc = self._doc('2026-12-15')

    def _doc(self, expiry, state='verified', doc_type=None):
        return self.env['procurement.supplier.document'].with_user(self.buyer).create({
            'partner_id': self.acme.id, 'document_type_id': (doc_type or self.insurance).id,
            'issue_date': '2026-01-01', 'expiry_date': expiry, 'verification_state': state})

    def _state(self):
        return (len(self._reach(self.acme_user.partner_id)),
                self._activity_count(self.buyer, self.doc, self.acme))

    def test_sc_0091_1_reminder_then_nothing_new(self):
        """SC-SPM-0091-1: insurance expires 2026-12-15, reminder period 30 days. The job of 2026-11-16 notifies
        Acme's portal users and gives the buyer an activity; the job of 2026-11-17 sends nothing new."""
        before = self._state()
        self._run_jobs('2026-11-16 08:00:00')
        after = self._state()
        self.assertGreater(after[0], before[0], "Acme's portal user is notified")
        self.assertGreater(after[1], before[1], "Acme's buyer has an activity")
        self._run_jobs('2026-11-17 08:00:00')
        self.assertEqual(self._state(), after, "nothing new on the next run")

    def test_sc_0091_1_the_same_day_twice_sends_once(self):
        """BR-SPM-0019: two runs on the same day send one notification."""
        before = self._state()
        self._run_jobs('2026-11-16 08:00:00')
        first = self._state()
        self.assertGreater(first[0], before[0], "the first run notifies")
        self._run_jobs('2026-11-16 09:00:00')
        self.assertEqual(self._state(), first)

    def test_sc_0091_1_the_key_row_is_kept_once(self):
        """BR-SPM-0019: the once-only key row of the document, the portal user's partner and the expiry
        date exists once after two runs."""
        self._run_jobs('2026-11-16 08:00:00')
        self._run_jobs('2026-11-17 08:00:00')
        rows = self.env['procurement.notification.log'].sudo().search([
            ('res_model', '=', 'procurement.supplier.document'), ('res_id', '=', self.doc.id),
            ('recipient_partner_id', '=', self.acme_user.partner_id.id)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows.due_date), '2026-12-15')

    def test_sc_0091_1_reminder_period_boundaries(self):
        """FR-SPM-0091 boundary: 31 days before expiry nothing; exactly 30 days before, the reminder."""
        before = self._state()
        self._run_jobs('2026-11-14 08:00:00')
        self.assertEqual(self._state(), before, "31 days before: not yet")
        self._run_jobs('2026-11-15 08:00:00')
        self.assertGreater(self._state()[0], before[0], "30 days before: reminded")

    def test_sc_0091_1_expired_document_is_reminded(self):
        """FR-SPM-0091: an expiry date that has passed is also reminded."""
        doc = self._doc('2026-11-10')
        self._run_jobs('2026-11-16 08:00:00')
        rows = self.env['procurement.notification.log'].sudo().search([
            ('res_model', '=', 'procurement.supplier.document'), ('res_id', '=', doc.id)])
        self.assertTrue(rows)

    def test_sc_0091_1_only_verified_documents(self):
        """FR-SPM-0091: a document still to verify is not reminded."""
        self.doc.write({'verification_state': 'to_verify'})
        other = self._doc('2026-12-15', doc_type=self.env['procurement.document.type'].create({
            'name': 'Tax T034', 'reminder_days': 30, 'needs_expiry': True}))
        self._run_jobs('2026-11-16 08:00:00')
        log = self.env['procurement.notification.log'].sudo()
        self.assertTrue(log.search([('res_model', '=', other._name), ('res_id', '=', other.id)]),
                        "the verified document is reminded")
        self.assertFalse(log.search([('res_model', '=', self.doc._name), ('res_id', '=', self.doc.id)]))

    def test_sc_0091_1_every_portal_user_is_notified(self):
        """FR-SPM-0091: each portal user of the supplier is notified."""
        second = self._portal_user(self.acme, 'Jim Acme', 'spm_acme2_t034')
        before = len(self._reach(second.partner_id))
        self._run_jobs('2026-11-16 08:00:00')
        self.assertGreater(len(self._reach(second.partner_id)), before)

    def test_br_0019_a_new_due_date_is_a_new_notification(self):
        """BR-SPM-0019: the expiry date moved to a new date, then due again, notifies again."""
        self._run_jobs('2026-11-16 08:00:00')
        first = len(self._reach(self.acme_user.partner_id))
        self.doc.write({'expiry_date': '2026-12-17'})
        self._run_jobs('2026-11-17 08:00:00')
        self.assertGreater(len(self._reach(self.acme_user.partner_id)), first)


class TestQuoteDeadlineReminders(SpmReminderMixin, SpmDeliveryCase):
    """Requirements: FR-SPM-0092, BR-SPM-0019"""

    def setUp(self):
        super().setUp()
        with freeze_time('2026-11-20 09:00:00'):
            self.event = self._event(
                self.acme | self.beta | self.gamma, title='Chairs T034', deadline='2026-11-30 17:00:00')
            # Acme answers
            response = self._response(self.event, self.acme)
            line = self.event.line_ids
            response.with_user(self.acme_user).spm_submit_version([{
                'event_line_id': line.id, 'price_unit': 12.5, 'discount': 0.0, 'lead_time_days': 10}])
        self.env.flush_all()

    def _count(self, user):
        return len(self._reach(user.partner_id))

    def test_sc_0092_1_silent_suppliers_are_reminded(self):
        """SC-SPM-0092-1: SE closes 2026-11-30 17:00, Acme answered, Beta and Gamma did not. The job of
        2026-11-28 18:00 reminds Beta and Gamma and not Acme."""
        before = {u: self._count(u) for u in (self.acme_user, self.beta_user, self.gamma_user)}
        self._run_jobs('2026-11-28 18:00:00')
        self.assertGreater(self._count(self.beta_user), before[self.beta_user])
        self.assertGreater(self._count(self.gamma_user), before[self.gamma_user])
        self.assertEqual(self._count(self.acme_user), before[self.acme_user], "Acme answered: not disturbed")

    def test_sc_0092_1_reminder_is_sent_once(self):
        """BR-SPM-0019: a second run in the window reminds no one again."""
        before = self._count(self.beta_user)
        self._run_jobs('2026-11-28 18:00:00')
        first = self._count(self.beta_user)
        self.assertGreater(first, before, "the first run reminds")
        self._run_jobs('2026-11-29 09:00:00')
        self.assertEqual(self._count(self.beta_user), first)

    def test_sc_0092_1_before_the_reminder_delay_nothing(self):
        """FR-SPM-0092 boundary: 3 days before the deadline (delay 2 days) nobody is reminded."""
        before = self._count(self.beta_user)
        self._run_jobs('2026-11-27 16:00:00')
        self.assertEqual(self._count(self.beta_user), before)
        self._run_jobs('2026-11-28 18:00:00')
        self.assertGreater(self._count(self.beta_user), before, "inside the delay the reminder goes")

    def test_sc_0092_1_buyer_told_who_did_not_answer(self):
        """SC-SPM-0092-1: after the deadline the event's buyer is notified that Beta and Gamma did not
        answer; Acme, who answered, is not named."""
        self._run_jobs('2026-12-01 09:00:00')
        buyer = self.buyer.partner_id
        self.assertTrue(self._reach_text(buyer, 'Beta'))
        self.assertTrue(self._reach_text(buyer, 'Gamma'))
        names = [m for m in self.env['mail.message'].sudo().search([('partner_ids', 'in', buyer.ids)])
                 if 'Beta' in (m.body or '') and 'Gamma' in (m.body or '')]
        self.assertTrue(names)
        self.assertFalse([m for m in names if 'Acme' in (m.body or '')])

    def test_sc_0092_1_buyer_notified_once(self):
        """BR-SPM-0019: a second run after the deadline tells the buyer nothing new."""
        before = self._count(self.buyer)
        self._run_jobs('2026-12-01 09:00:00')
        first = self._count(self.buyer)
        self.assertGreater(first, before, "the first run notifies the buyer")
        self._run_jobs('2026-12-02 09:00:00')
        self.assertEqual(self._count(self.buyer), first)

    def test_sc_0092_1_suppliers_not_reminded_after_the_deadline(self):
        """FR-SPM-0092: once the deadline has passed the silent suppliers get no further reminder."""
        before = self._count(self.beta_user)
        buyer_before = self._count(self.buyer)
        self._run_jobs('2026-12-01 09:00:00')
        self.assertGreater(self._count(self.buyer), buyer_before, "the buyer is told")
        self.assertEqual(self._count(self.beta_user), before)


class TestQuoteReminderTemplate(SpmReminderMixin, SpmDeliveryCase):
    """Requirements: FR-SPM-0095"""

    def setUp(self):
        super().setUp()
        self.env['res.lang']._activate_lang('fr_FR')
        with freeze_time('2026-11-20 09:00:00'):
            self.event = self._event(self.beta, title='Chairs FR', deadline='2026-11-30 17:00:00')

    def _template(self):
        template = self.env['mail.template'].search([
            ('model', 'in', ('procurement.sourcing.event', 'procurement.sourcing.response')),
            ('name', 'ilike', 'reminder')], limit=1)
        self.assertTrue(template, "an editable quote reminder template exists")
        return template

    def test_sc_0095_1_edited_french_text_is_sent(self):
        """SC-SPM-0095-1: an Administrator edits the French version of the quote reminder template; a reminder
        to a supplier contact whose language is French carries the edited French text. (The template
        form is not described by the specification: the edit is written with the French language in the
        context.)"""
        admin = self.env.ref('base.user_admin')
        self.beta_user.partner_id.lang = 'fr_FR'
        self.beta.lang = 'fr_FR'
        self._template().with_user(admin).with_context(lang='fr_FR').write({
            'body_html': '<p>TEXTE FRANCAIS EDITE T034</p>'})
        self._run_jobs('2026-11-28 18:00:00')
        self.assertTrue(self._reach_text(self.beta_user.partner_id, 'TEXTE FRANCAIS EDITE T034'))

    def test_sc_0095_1_other_language_keeps_its_own_text(self):
        """FR-SPM-0095: the French edit is not sent to a contact whose language is English."""
        admin = self.env.ref('base.user_admin')
        self._template().with_user(admin).with_context(lang='fr_FR').write({
            'body_html': '<p>TEXTE FRANCAIS EDITE T034</p>'})
        self._run_jobs('2026-11-28 18:00:00')
        self.assertFalse(self._reach_text(self.beta_user.partner_id, 'TEXTE FRANCAIS EDITE T034'))
        self.assertTrue(self._reach(self.beta_user.partner_id))


class TestDeliveryEscalation(SpmReminderMixin, SpmDeliveryCase):
    """Requirements: FR-SPM-0093, BR-SPM-0019"""

    def setUp(self):
        super().setUp()
        self.company.spm_delivery_escalation_days = 3
        with freeze_time('2026-12-01 09:00:00'):
            self.order = self._dorder(planned='2026-12-10 08:00:00', qty=100.0)
            self.line = self.order.order_line
            self.order.with_user(self.acme_user).spm_acknowledge({self.line.id: '2026-12-12 08:00:00'})
        self._receive(self.order, 60.0, '2026-12-05 10:00:00', backorder=True)
        self.commitment = self._commitment(self.order)

    def _state(self):
        return (len(self._reach(self.acme_user.partner_id)),
                self._activity_count(self.buyer, self.order, self.line, self.commitment))

    def test_sc_0093_1_overdue_line_is_escalated_once(self):
        """SC-SPM-0093-1: a line confirmed for 2026-12-12 with 40 remaining and a 3-day delay. The job of
        2026-12-16 gives the buyer an activity and notifies Acme's portal users; the job of 2026-12-17
        creates nothing new."""
        self.assertEqual(self.commitment.remaining_qty, 40.0)
        before = self._state()
        self._run_jobs('2026-12-16 08:00:00')
        after = self._state()
        self.assertGreater(after[1], before[1], "the buyer has an activity")
        self.assertGreater(after[0], before[0], "Acme's portal users are notified")
        self._run_jobs('2026-12-17 08:00:00')
        self.assertEqual(self._state(), after)

    def test_sc_0093_1_delay_boundary(self):
        """FR-SPM-0093 boundary: overdue by exactly 3 days (2026-12-15) is not more than the delay; 4 days is."""
        before = self._state()
        self._run_jobs('2026-12-15 08:00:00')
        self.assertEqual(self._state(), before)
        self._run_jobs('2026-12-16 08:00:00')
        self.assertNotEqual(self._state(), before)

    def test_sc_0093_1_delivered_line_is_not_escalated(self):
        """FR-SPM-0093: a line whose remaining quantity was received is not overdue."""
        order = self._dorder(planned='2026-12-12 08:00:00', qty=10.0)
        self._receive(order, 10.0, '2026-12-11 10:00:00')
        count = self.env['procurement.notification.log'].sudo().search_count([
            ('res_id', 'in', order.order_line.ids)])
        self._run_jobs('2026-12-16 08:00:00')
        self.assertEqual(self.env['procurement.notification.log'].sudo().search_count([
            ('res_id', 'in', order.order_line.ids)]), count)

    def test_sc_0093_1_company_delay_is_a_setting(self):
        """FR-SPM-0093: the delay is the company's: with 1 day the line is escalated on 2026-12-14."""
        self.company.spm_delivery_escalation_days = 1
        before = self._state()
        self._run_jobs('2026-12-14 08:00:00')
        self.assertNotEqual(self._state(), before)

    def test_sc_0093_1_new_agreed_date_gives_a_new_escalation(self):
        """FR-SPM-0093: after the buyer accepts a proposal for 2026-12-20 a new escalation can occur for
        the new date."""
        self._run_jobs('2026-12-16 08:00:00')
        first = self._state()
        with freeze_time('2026-12-17 09:00:00'):
            proposal = self.commitment.with_user(self.acme_user).spm_propose('late', date='2026-12-20 08:00:00')
            proposal.with_user(self.buyer).action_spm_accept()
        self.env.flush_all()
        self._run_jobs('2026-12-24 08:00:00')
        after = self._state()
        self.assertGreater(after[1], first[1])
        self.assertGreater(after[0], first[0])


class TestContractReminders(SpmReminderMixin, SpmContractCase):
    """Requirements: FR-SPM-0094, BR-SPM-0019"""

    def setUp(self):
        super().setUp()
        # end 2027-12-31 minus 60 days of notice: the notice date is 2027-11-01
        self.contract = self._active_contract(end='2027-12-31', notice=60)
        self.assertEqual(str(self.contract.notice_date), '2027-11-01')

    def _state(self):
        return (len(self._reach(self.ann.partner_id)), self._activity_count(self.ann, self.contract))

    def test_sc_0094_1_owner_notified_with_activity_on_the_notice_date(self):
        """SC-SPM-0094-1: active contract with notice date 2027-11-01, 30-day reminder period, owned by Ann.
        The job of 2027-10-02 notifies Ann and gives her an activity due 2027-11-01."""
        before = self._state()
        self._run_jobs('2027-10-02 08:00:00')
        after = self._state()
        self.assertGreater(after[0], before[0], "Ann is notified")
        self.assertGreater(after[1], before[1], "Ann has an activity")
        activities = self.env['mail.activity'].sudo().search([
            ('user_id', '=', self.ann.id), ('res_model', '=', 'procurement.contract'),
            ('res_id', '=', self.contract.id)])
        self.assertIn('2027-11-01', [str(a.date_deadline) for a in activities])

    def test_sc_0094_1_once_per_kind_and_date(self):
        """BR-SPM-0019: a second run sends nothing new."""
        before = self._state()
        self._run_jobs('2027-10-02 08:00:00')
        first = self._state()
        self.assertGreater(first[1], before[1], "the first run gives an activity")
        self._run_jobs('2027-10-03 08:00:00')
        self.assertEqual(self._state(), first)

    def test_sc_0094_1_period_boundary(self):
        """FR-SPM-0094 boundary: 31 days before the notice date nothing; 30 days before, the reminder."""
        before = self._state()
        self._run_jobs('2027-10-01 08:00:00')
        self.assertEqual(self._state(), before)
        self._run_jobs('2027-10-02 08:00:00')
        self.assertNotEqual(self._state(), before)

    def test_sc_0094_1_end_date_has_its_own_reminder(self):
        """FR-SPM-0094: the end date is a second kind of date, with its own reminder and activity."""
        self._run_jobs('2027-10-02 08:00:00')
        first = self._state()
        self._run_jobs('2027-12-01 08:00:00')
        after = self._state()
        self.assertGreater(after[1], first[1])
        deadlines = self.env['mail.activity'].sudo().search([
            ('user_id', '=', self.ann.id), ('res_model', '=', 'procurement.contract'),
            ('res_id', '=', self.contract.id)]).mapped(lambda a: str(a.date_deadline))
        self.assertIn('2027-12-31', deadlines)

    def test_sc_0094_1_inactive_contract_is_left_alone(self):
        """FR-SPM-0094: a draft contract is not reminded."""
        draft = self._contract(end='2027-12-31', notice=60)
        before = self._activity_count(self.ann, draft)
        active_before = self._activity_count(self.ann, self.contract)
        self._run_jobs('2027-10-02 08:00:00')
        self.assertGreater(self._activity_count(self.ann, self.contract), active_before, "the active one is")
        self.assertEqual(self._activity_count(self.ann, draft), before)
