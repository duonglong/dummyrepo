"""Acceptance tests of T-015 (event questions and answers, portal messages
without internal notes), written from the specification.

Requirements: FR-SPM-0023, FR-SPM-0026, BR-SPM-0003

Names used (the design names none, fixed in tests/portal_common.py):
procurement.sourcing.question.spm_ask(event_id, text), called as the supplier's
portal user (the call behind POST /my/procurement/rfq/<id>/question), and
action_spm_answer(answer, shared=False) for the Buyer. The portal answers are
checked at the record layer (the design: "filter at message level"), as the portal user.
SC-SPM-0023-2 says neither supplier sees that Acme asked: the asker field is
unreadable to Beta; Acme sees the shared entry like Beta does.
"""
from odoo.exceptions import AccessError, UserError, ValidationError

from .portal_common import SpmPortalCase

ASK = 'Is the colour black accepted?'


class TestEventQuestionsAcceptance(SpmPortalCase):
    """Requirements: FR-SPM-0023, BR-SPM-0003"""

    def setUp(self):
        super().setUp()
        self.event = self._event(self.acme | self.beta)
        self.Question = self.env['procurement.sourcing.question']

    def _ask(self):
        return self.Question.with_user(self.acme_user).spm_ask(self.event.id, ASK)

    def _visible(self, user):
        return self.Question.with_user(user).search([('event_id', '=', self.event.id)])

    def test_sc_0023_1_question_is_seen_by_buyer_and_asker_only(self):
        """SC-SPM-0023-1: Acme asks 'Is the colour black accepted?'; the buyer
        sees the question and Beta does not."""
        question = self._ask()
        self.env.flush_all()
        self.assertEqual(question.question, ASK)
        self.assertEqual(question.event_id, self.event)
        self.assertEqual(question.partner_id, self.acme)
        self.assertTrue(question.asked_date)
        self.assertEqual(self._visible(self.buyer), question)
        self.assertEqual(self._visible(self.acme_user), question)
        self.assertFalse(self._visible(self.beta_user))
        with self.assertRaises(AccessError):
            question.with_user(self.beta_user).read(['question'])

    def test_sc_0023_1_a_supplier_not_invited_cannot_ask(self):
        """FR-SPM-0023: only an invited supplier may ask on the event."""
        event = self._event(self.beta, title='Beta only')
        with self.assertRaises((AccessError, UserError, ValidationError)):
            self.Question.with_user(self.acme_user).spm_ask(event.id, ASK)
        self.assertFalse(self.Question.sudo().search([('event_id', '=', event.id)]))

    def test_sc_0023_2_shared_answer_reaches_all_without_the_asker(self):
        """SC-SPM-0023-2: the Buyer answers 'Yes' and shares it; Acme and Beta
        both see the question and answer, and Beta cannot see who asked."""
        question = self._ask()
        question.with_user(self.buyer).action_spm_answer('Yes', shared=True)
        self.env.flush_all()
        self.assertEqual(question.answer, 'Yes')
        self.assertTrue(question.shared)
        self.assertEqual(question.answered_by_id, self.buyer)
        for user in (self.acme_user, self.beta_user):
            visible = self._visible(user)
            self.assertEqual(visible, question, user.name)
            data = visible.read(['question', 'answer'])[0]
            self.assertEqual(data['question'], ASK)
            self.assertEqual(data['answer'], 'Yes')
        with self.assertRaises(AccessError):
            question.with_user(self.beta_user).read(['partner_id'])
        self.assertNotIn('Acme', str(question.with_user(self.beta_user).read(
            ['question', 'answer', 'shared', 'event_id'])))
        for user in (self.acme_user, self.beta_user):
            self.assertTrue(self._sent_to(user.partner_id, 'Yes'), f"{user.name} receives the answer")
        self.assertFalse(self._sent_to(self.beta_user.partner_id, 'Yes', 'Acme'))

    def test_sc_0023_2_a_private_answer_reaches_the_asker_only(self):
        """FR-SPM-0023: an answer not shared goes to the asking supplier only."""
        question = self._ask()
        question.with_user(self.buyer).action_spm_answer('Yes, black is fine', shared=False)
        self.env.flush_all()
        self.assertEqual(self._visible(self.acme_user).answer, 'Yes, black is fine')
        self.assertFalse(self._visible(self.beta_user))
        self.assertTrue(self._sent_to(self.acme_user.partner_id, 'black is fine'))
        self.assertFalse(self._sent_to(self.beta_user.partner_id, 'black is fine'))


class TestPortalMessagesAcceptance(SpmPortalCase):
    """Requirements: FR-SPM-0026, BR-SPM-0003, NFR-SPM-0001"""

    def _thread(self, order):
        return self.env['mail.message'].with_user(self.acme_user).search([
            ('model', '=', 'purchase.order'), ('res_id', '=', order.id)])

    def test_sc_0026_1_internal_notes_are_not_shown_to_the_supplier(self):
        """SC-SPM-0026-1: order P00050 of Acme has a message to Acme, an
        internal note 'Acme is expensive' and an internal discussion; the Acme
        portal user sees the message and neither of the others."""
        order = self._order(self.acme)
        order.with_user(self.buyer).message_post(
            body='Delivery planned for Monday', message_type='comment',
            subtype_xmlid='mail.mt_comment', partner_ids=[self.acme_user.partner_id.id])
        order.with_user(self.buyer).message_post(
            body='Acme is expensive', message_type='comment', subtype_xmlid='mail.mt_note')
        order.with_user(self.buyer).message_post(
            body='Colleagues only: renegotiate', message_type='comment',
            subtype_xmlid='mail.mt_comment', partner_ids=[self.manager.partner_id.id])
        self.env.flush_all()
        bodies = ' '.join(self._thread(order).mapped('body'))
        self.assertIn('Delivery planned for Monday', bodies)
        self.assertNotIn('Acme is expensive', bodies)
        self.assertNotIn('Colleagues only', bodies)
