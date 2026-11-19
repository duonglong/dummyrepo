"""Own tests of T-015: who asked stays hidden, the ask route, what a supplier may post and see.

Requirements: FR-SPM-0023, FR-SPM-0026, BR-SPM-0003
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .portal_common import SpmPortalCase


@tagged('post_install', '-at_install')
class TestSourcingQuestion(SpmPortalCase):
    """Requirements: FR-SPM-0023, BR-SPM-0003"""

    def setUp(self):
        super().setUp()
        self.event = self._event(self.acme | self.beta)
        self.Question = self.env['procurement.sourcing.question']

    def test_search_by_asker_is_refused_to_another_supplier(self):
        """Requirements: BR-SPM-0003 - a search on the asker would tell who asked."""
        question = self.Question.with_user(self.acme_user).spm_ask(self.event.id, 'Colour?')
        question.with_user(self.buyer).action_spm_answer('Black', shared=True)
        with self.assertRaises(AccessError):
            self.Question.with_user(self.beta_user).search([('partner_id', '=', self.acme.id)])

    def test_the_asker_of_a_shared_question_stays_readable_to_the_buyer(self):
        """Requirements: FR-SPM-0023"""
        question = self.Question.with_user(self.acme_user).spm_ask(self.event.id, 'Colour?')
        self.assertEqual(question.with_user(self.buyer).partner_id, self.acme)

    def test_an_empty_question_and_a_draft_event_are_refused(self):
        """Requirements: FR-SPM-0023"""
        with self.assertRaises(UserError):
            self.Question.with_user(self.acme_user).spm_ask(self.event.id, '  ')
        draft = self._event(self.acme, title='Draft', send=False)
        with self.assertRaises((AccessError, UserError)):
            self.Question.with_user(self.acme_user).spm_ask(draft.id, 'Colour?')

    def test_the_ask_route_records_the_question_of_the_session_user(self):
        """Requirements: FR-SPM-0023 - the route form posts the text; Beta cannot ask on Acme's event."""
        self._login(self.acme_user)
        self._post(f'/my/procurement/rfq/{self.event.id}/question', {'question': 'Delivery date?'})
        question = self.Question.sudo().search([('event_id', '=', self.event.id)])
        self.assertEqual(question.question, 'Delivery date?')
        self.assertEqual(question.partner_id, self.acme)
        other = self._event(self.beta, title='Beta only')
        response = self._post(f'/my/procurement/rfq/{other.id}/question', {'question': 'Hidden?'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.Question.sudo().search([('event_id', '=', other.id)]))


@tagged('post_install', '-at_install')
class TestSupplierMessages(SpmPortalCase):
    """Requirements: FR-SPM-0026, BR-SPM-0003"""

    def test_a_message_written_by_the_supplier_stays_visible_and_another_suppliers_does_not(self):
        """Requirements: FR-SPM-0026"""
        order = self._order(self.acme)
        order.sudo().message_post(  # the portal chatter posts as the portal user's partner with elevated rights
            body='Question from Acme', message_type='comment', subtype_xmlid='mail.mt_comment',
            author_id=self.acme_user.partner_id.id)
        order.with_user(self.buyer).message_post(
            body='Beta secret', message_type='comment', subtype_xmlid='mail.mt_comment',
            partner_ids=[self.beta_user.partner_id.id])
        self.env.flush_all()
        bodies = ' '.join(self.env['mail.message'].with_user(self.acme_user).search([
            ('model', '=', 'purchase.order'), ('res_id', '=', order.id)]).mapped('body'))
        self.assertIn('Question from Acme', bodies)
        self.assertNotIn('Beta secret', bodies)
