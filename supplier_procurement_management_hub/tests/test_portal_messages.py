"""Own tests of T-015: who asked stays hidden, the ask route, what a supplier may post and see.

Requirements: FR-SPM-0023, FR-SPM-0026, BR-SPM-0003
"""
from odoo import SUPERUSER_ID
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

    def test_the_creator_of_a_question_is_not_the_asking_user(self):
        """Requirements: BR-SPM-0003 - create_uid would name the asker to another supplier."""
        question = self.Question.with_user(self.acme_user).spm_ask(self.event.id, 'Colour?')
        question.with_user(self.buyer).action_spm_answer('Black', shared=True)
        self.assertEqual(question.create_uid.id, SUPERUSER_ID)
        self.assertNotIn(self.acme_user.id, question.sudo().write_uid.ids)

    def test_a_supplier_reads_shared_questions_by_attribute(self):
        """Requirements: FR-SPM-0023 - own and another supplier's shared questions in one recordset."""
        mine = self.Question.with_user(self.acme_user).spm_ask(self.event.id, 'Colour?')
        theirs = self.Question.with_user(self.beta_user).spm_ask(self.event.id, 'Size?')
        mine.with_user(self.buyer).action_spm_answer('Black', shared=True)
        theirs.with_user(self.buyer).action_spm_answer('Large', shared=True)
        for user in (self.acme_user, self.beta_user):
            questions = self.Question.with_user(user).search([('event_id', '=', self.event.id)])
            self.assertEqual(len(questions), 2)
            self.assertEqual(set(questions.mapped('answer')), {'Black', 'Large'})
            self.assertEqual(set(questions.mapped('question')), {'Colour?', 'Size?'})

    def test_a_draft_answer_and_the_shared_tick_stay_hidden_until_sent(self):
        """Requirements: FR-SPM-0023 - the form saves a draft; nothing reaches a supplier before Send."""
        question = self.Question.with_user(self.acme_user).spm_ask(self.event.id, 'Colour?')
        question.with_user(self.buyer).write({'draft_answer': 'Maybe black', 'shared': True})
        self.assertFalse(self.Question.with_user(self.beta_user).search([('event_id', '=', self.event.id)]))
        acme_view = self.Question.with_user(self.acme_user).search([('event_id', '=', self.event.id)])
        self.assertFalse(acme_view.answer)
        with self.assertRaises(AccessError):
            acme_view.read(['draft_answer'])
        question.with_user(self.buyer).action_spm_send_answer()
        self.assertEqual(self.Question.with_user(self.beta_user).search([('event_id', '=', self.event.id)]), question)
        self.assertEqual(question.answer, 'Maybe black')
        self.assertFalse(question.draft_answer)
        with self.assertRaises(UserError):
            question.with_user(self.buyer).action_spm_answer('Again')

    def test_blocking_is_judged_on_the_reader_not_the_asker(self):
        """Requirements: FR-SPM-0023, FR-SPM-0028 - a blocked asker's shared answer stays; a blocked reader sees none."""
        question = self.Question.with_user(self.acme_user).spm_ask(self.event.id, 'Colour?')
        question.with_user(self.buyer).action_spm_answer('Black', shared=True)
        self.acme.with_user(self.manager).action_spm_set_status(self.blocked, 'Fraud suspected')
        self.assertEqual(self.Question.with_user(self.beta_user).search([('event_id', '=', self.event.id)]), question)
        self.beta.with_user(self.manager).action_spm_set_status(self.blocked, 'Fraud suspected')
        for user in (self.acme_user, self.beta_user):
            try:
                found = self.Question.with_user(user).search([('event_id', '=', self.event.id)])
            except AccessError:  # an empty rule reads as no access at all
                found = self.Question
            self.assertFalse(found, user.name)

    def test_the_ask_route_refuses_a_user_who_is_not_a_supplier(self):
        """Requirements: FR-SPM-0023 - a page of refusal, not a server error."""
        self._login(self.buyer)
        response = self._post(f'/my/procurement/rfq/{self.event.id}/question', {'question': 'Hi?'})
        self.assertIn(response.status_code, (403, 404))
        self.assertFalse(self.Question.sudo().search([('event_id', '=', self.event.id)]))


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

    def test_the_portal_chatter_domain_and_the_supplier_contact_hide_colleague_messages(self):
        """Requirements: FR-SPM-0026 - the chatter route reads as superuser, so its domain filters too."""
        order = self._order(self.acme)
        order.with_user(self.buyer).message_post(
            body='Colleagues only: renegotiate', message_type='comment',
            subtype_xmlid='mail.mt_comment', partner_ids=[self.manager.partner_id.id])
        order.with_user(self.buyer).message_post(
            body='Delivery planned', message_type='comment',
            subtype_xmlid='mail.mt_comment', partner_ids=[self.acme_user.partner_id.id])
        self.acme.with_user(self.buyer).message_post(
            body='Contact colleagues only', message_type='comment',
            subtype_xmlid='mail.mt_comment', partner_ids=[self.manager.partner_id.id])
        self.env.flush_all()
        chatter = self.env['mail.message'].sudo().search(
            order.with_user(self.acme_user)._get_share_message_domain())
        bodies = ' '.join(chatter.filtered(lambda m: m.res_id == order.id and m.model == order._name).mapped('body'))
        self.assertIn('Delivery planned', bodies)
        self.assertNotIn('Colleagues only', bodies)
        contact_bodies = ' '.join(self.env['mail.message'].with_user(self.acme_user).search([
            ('model', '=', 'res.partner'), ('res_id', '=', self.acme.id)]).mapped('body'))
        self.assertNotIn('Contact colleagues only', contact_bodies)

    def test_the_chatter_domain_of_a_bill_and_of_a_contact_is_their_own(self):
        """Requirements: FR-SPM-0026 - the portal chatter calls the method of the thread's own model."""
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.acme.id, 'invoice_date': '2026-01-05',
            'invoice_line_ids': [(0, 0, {'name': 'Line', 'quantity': 1, 'price_unit': 10})]})
        for record in (bill, self.acme):
            record.with_user(self.buyer).message_post(
                body='Internal only', message_type='comment',
                subtype_xmlid='mail.mt_comment', partner_ids=[self.manager.partner_id.id])
            record.with_user(self.buyer).message_post(
                body='For the supplier', message_type='comment',
                subtype_xmlid='mail.mt_comment', partner_ids=[self.acme_user.partner_id.id])
            self.env.flush_all()
            chatter = self.env['mail.message'].sudo().search(
                record.with_user(self.acme_user)._get_share_message_domain())
            bodies = ' '.join(chatter.filtered(
                lambda m: m.res_id == record.id and m.model == record._name).mapped('body'))
            self.assertIn('For the supplier', bodies)
            self.assertNotIn('Internal only', bodies)
