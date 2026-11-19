"""Acceptance tests of T-017 (weighted evaluation, award with rationale,
order generation), written from the specification.

Requirements: BR-SPM-0008, FR-SPM-0038, FR-SPM-0039, FR-SPM-0040, FR-SPM-0041

Names used (the design names none, fixed in tests/comparison_common.py):
procurement.sourcing.event.action_spm_evaluate() scores and ranks the responses;
the award dialog procurement.award.wizard is driven with Form (fields event_id,
mode whole/per_line, response_id, line_choice_ids with event_line_id and
response_id, rationale) and its Confirm button is action_confirm();
procurement.sourcing.event.action_spm_generate_orders() is the "Generate orders"
button. The event gets the criteria and the assumptions of BR-SPM-0007 stated,
since price is scored only on normalized values. The rationale refusal calls
action_confirm() on a dialog created with a blank rationale: Form cannot save an
empty required field, so the server check is what is tested.
"""
import json
from datetime import date

from freezegun import freeze_time

from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form

from .comparison_common import SpmComparisonCase

RATIONALE = 'Shortest lead time for the floor opening'
CRITERIA = [('Price', 'price', 'lower', 60.0), ('Lead time', 'lead_time', 'lower', 40.0)]


class TestAwardAcceptance(SpmComparisonCase):
    """Requirements: FR-SPM-0038, FR-SPM-0039, FR-SPM-0040, FR-SPM-0041, BR-SPM-0008"""

    def _scored_event(self, **extra):
        """SE: chairs only; Acme 11.90 / 10 days, Beta 12.40 / 5 days, Gamma 13.00 / 7 days."""
        event = self._sourcing(
            self.acme | self.beta | self.gamma, criteria=CRITERIA, **extra)
        self._assume(event)
        chairs = event.line_ids
        self.responses = {
            'acme': self._response(event, self.acme), 'beta': self._response(event, self.beta),
            'gamma': self._response(event, self.gamma)}
        self._quote(self.responses['acme'], [(chairs, 11.90, 10)])
        self._quote(self.responses['beta'], [(chairs, 12.40, 5)])
        self._quote(self.responses['gamma'], [(chairs, 13.00, 7)])
        return event

    def _two_line_event(self, **extra):
        """Chairs (60) and desks (20); Acme quoted both, Beta both, Gamma chairs only."""
        event = self._sourcing(
            self.acme | self.beta | self.gamma, criteria=CRITERIA,
            lines=[('Chair', self.chair, 60.0), ('Desk', self.desk, 20.0)], **extra)
        self._assume(event)
        chairs, desks = event.line_ids.sorted('id')
        self.responses = {
            'acme': self._response(event, self.acme), 'beta': self._response(event, self.beta),
            'gamma': self._response(event, self.gamma)}
        payment = self.env['account.payment.term'].create({'name': '30 days M3'})
        self._quote(self.responses['acme'], [(chairs, 11.90, 10), (desks, 200.0, 14)],
                    payment_term_id=payment.id)
        self._quote(self.responses['beta'], [(chairs, 12.40, 5), (desks, 180.0, 7)])
        self._quote(self.responses['gamma'], [(chairs, 13.00, 7)])
        self.payment = payment
        return event, chairs, desks

    def _award(self, event, mode='whole', response=None, choices=None, rationale=RATIONALE):
        """Fill the award dialog with Form and confirm it as the Buyer."""
        form = Form(self.env['procurement.award.wizard'].with_user(self.buyer).with_context(
            default_event_id=event.id, active_model=event._name, active_id=event.id))
        form.mode = mode
        if mode == 'whole':
            form.response_id = response
        else:
            for line, chosen in choices.items():
                rows = [i for i in range(len(form.line_choice_ids))]
                done = False
                for i in rows:
                    with form.line_choice_ids.edit(i) as row:
                        if row.event_line_id == line:
                            row.response_id = chosen
                            done = True
                if not done:
                    with form.line_choice_ids.new() as row:
                        row.event_line_id = line
                        row.response_id = chosen
        form.rationale = rationale
        wizard = form.save()
        return wizard.action_confirm()

    # FR-SPM-0038 -----------------------------------------------------------
    def test_sc_0038_1_weighted_scores_and_ranks(self):
        """SC-SPM-0038-1: price weight 60 and lead time weight 40; Acme has the
        best price (11.90) with 10 days, Beta's price is 11.90/12.40 of the best
        with the best lead time (5 days): Acme scores 80, Beta about 97.6 and
        Beta ranks first."""
        event = self._sourcing(self.acme | self.beta, criteria=CRITERIA)
        self._assume(event)
        chairs = event.line_ids
        acme, beta = self._response(event, self.acme), self._response(event, self.beta)
        self._quote(acme, [(chairs, 11.90, 10)])
        self._quote(beta, [(chairs, 12.40, 5)])
        event.with_user(self.buyer).action_spm_evaluate()
        self.env.flush_all()
        self.assertAlmostEqual(acme.score, 80.0, delta=0.05)
        self.assertAlmostEqual(beta.score, 97.6, delta=0.05)
        self.assertEqual(beta.rank, 1)
        self.assertEqual(acme.rank, 2)
        comparison = self.env['procurement.comparison'].search([('event_id', '=', event.id)])
        price = event.criterion_ids.filtered(lambda c: c.kind == 'price')
        self.assertAlmostEqual(
            self._cell(comparison, beta, 'criterion', criterion=price).score, 100 * 11.90 / 12.40, places=1)
        self.assertEqual(self._cell(comparison, acme, 'criterion', criterion=price).score, 100.0)

    def test_sc_0038_1_a_missing_value_scores_zero_and_is_flagged(self):
        """BR-SPM-0008: Gamma quoted the chairs only; its desk line is a missing
        value, counted in its missing lines, and its score is below the others'."""
        event, chairs, desks = self._two_line_event()
        event.with_user(self.buyer).action_spm_evaluate()
        self.env.flush_all()
        self.assertEqual(self.responses['gamma'].missing_line_count, 1)
        self.assertEqual(self.responses['acme'].missing_line_count, 0)
        self.assertLess(self.responses['gamma'].score, self.responses['acme'].score)
        self.assertEqual(self.responses['gamma'].rank, 3)

    def test_sc_0038_2_a_manual_score_needs_a_comment(self):
        """SC-SPM-0038-2: a manual criterion 'Service quality'; the Buyer gives
        Acme 70 without a comment: refused with a message that a comment is
        required; with a comment it is recorded."""
        event = self._sourcing(
            self.acme | self.beta, criteria=CRITERIA + [('Service quality', 'manual', 'higher', 20.0)])
        self._assume(event)
        chairs = event.line_ids
        acme = self._response(event, self.acme)
        self._quote(acme, [(chairs, 11.90, 10)])
        self._quote(self._response(event, self.beta), [(chairs, 12.40, 5)])
        comparison = self._compare(event)
        manual = event.criterion_ids.filtered(lambda c: c.kind == 'manual')
        cell = self._cell(comparison, acme, 'criterion', criterion=manual).with_user(self.buyer)
        with self.assertRaises((UserError, ValidationError)) as ctx:
            cell.write({'manual_score': 70.0})
        self.assertIn('comment', str(ctx.exception).lower())
        self.assertFalse(cell.manual_score)
        cell.write({'manual_score': 70.0, 'manual_comment': 'Fast on site'})
        self.assertEqual(cell.manual_score, 70.0)
        self.assertEqual(cell.manual_comment, 'Fast on site')

    def test_sc_0038_2_a_manual_score_survives_a_new_comparison(self):
        """FR-SPM-0038 and the design: scores and comments given by hand are kept
        when the comparison is rebuilt."""
        event = self._sourcing(
            self.acme, criteria=CRITERIA + [('Service quality', 'manual', 'higher', 20.0)])
        self._assume(event)
        acme = self._response(event, self.acme)
        self._quote(acme, [(event.line_ids, 11.90, 10)])
        comparison = self._compare(event)
        manual = event.criterion_ids.filtered(lambda c: c.kind == 'manual')
        self._cell(comparison, acme, 'criterion', criterion=manual).with_user(self.buyer).write(
            {'manual_score': 70.0, 'manual_comment': 'Fast on site'})
        comparison = self._compare(event)
        cell = self._cell(comparison, acme, 'criterion', criterion=manual)
        self.assertEqual(cell.manual_score, 70.0)
        self.assertEqual(cell.manual_comment, 'Fast on site')

    # FR-SPM-0039 -----------------------------------------------------------
    def test_sc_0039_1_award_to_one_response_marks_the_others(self):
        """SC-SPM-0039-1: the evaluated event is awarded to Beta with the rationale
        'Shortest lead time for the floor opening': Beta's response is Awarded,
        Acme's and Gamma's are Not awarded and the event is closed (Awarded)."""
        event = self._scored_event()
        event.with_user(self.buyer).action_spm_evaluate()
        self._award(event, response=self.responses['beta'])
        self.env.flush_all()
        self.assertEqual(self.responses['beta'].state, 'awarded')
        self.assertEqual(self.responses['acme'].state, 'not_awarded')
        self.assertEqual(self.responses['gamma'].state, 'not_awarded')
        self.assertEqual(event.state, 'awarded')
        self.assertEqual(event.award_rationale, RATIONALE)
        self.assertTrue(event.awarded_date)
        self.assertTrue(self.responses['beta'].current_version_id.line_ids.awarded)
        self.assertFalse(self.responses['acme'].current_version_id.line_ids.awarded)

    def test_sc_0039_2_award_without_rationale_is_refused(self):
        """SC-SPM-0039-2: awarding without a rationale is refused with a message
        that a rationale is required; nothing is marked and the event stays open."""
        event = self._scored_event()
        wizard = self.env['procurement.award.wizard'].with_user(self.buyer).create({
            'event_id': event.id, 'mode': 'whole', 'rationale': '   ',
            'response_id': self.responses['beta'].id})
        with self.assertRaises((UserError, ValidationError)) as ctx:
            wizard.action_confirm()
        self.assertIn('rationale', str(ctx.exception).lower())
        self.assertEqual(event.state, 'open')
        self.assertEqual(self.responses['beta'].state, 'submitted')

    def test_sc_0039_1_a_line_by_line_award_marks_responses_and_lines(self):
        """FR-SPM-0039: chairs to Acme and desks to Beta: both responses are
        awarded in part, Gamma is Not awarded and only the chosen lines are marked."""
        event, chairs, desks = self._two_line_event()
        self._award(event, mode='per_line', choices={chairs: self.responses['acme'],
                                                     desks: self.responses['beta']})
        self.env.flush_all()
        self.assertEqual(self.responses['acme'].state, 'partly_awarded')
        self.assertEqual(self.responses['beta'].state, 'partly_awarded')
        self.assertEqual(self.responses['gamma'].state, 'not_awarded')
        self.assertEqual(event.state, 'awarded')
        acme_lines = self.responses['acme'].current_version_id.line_ids
        beta_lines = self.responses['beta'].current_version_id.line_ids
        self.assertEqual(acme_lines.filtered('awarded').event_line_id, chairs)
        self.assertEqual(beta_lines.filtered('awarded').event_line_id, desks)

    # FR-SPM-0040 -----------------------------------------------------------
    def _awarded_per_line(self):
        requests = self.env['procurement.request'].sudo().create([{
            'requester_id': self.requester.id, 'needed_date': '2027-01-15', 'reason': 'New floor',
            'line_ids': [(0, 0, {'name': 'Office chair', 'product_id': self.chair.id,
                                 'quantity': 60.0, 'uom_id': self.unit.id})],
        }])
        event, chairs, desks = self._two_line_event()
        event.sudo().write({'request_ids': [(6, 0, requests.ids)]})
        with freeze_time('2026-11-25 10:00:00'):
            self._award(event, mode='per_line', choices={chairs: self.responses['acme'],
                                                         desks: self.responses['beta']})
        return event, requests, chairs, desks

    def _orders(self, event, partner):
        return self.env['purchase.order'].sudo().search([
            ('spm_sourcing_event_id', '=', event.id), ('partner_id', '=', partner.id)])

    def test_sc_0040_1_one_draft_order_per_awarded_supplier(self):
        """SC-SPM-0040-1: chairs awarded to Acme and desks to Beta; generating the
        orders gives one draft order for Acme with the chairs and one for Beta
        with the desks, both linked to the event and its requests."""
        event, requests, chairs, desks = self._awarded_per_line()
        with freeze_time('2026-11-25 10:00:00'):
            event.with_user(self.buyer).action_spm_generate_orders()
        self.env.flush_all()
        acme_order, beta_order = self._orders(event, self.acme), self._orders(event, self.beta)
        self.assertEqual(len(acme_order), 1)
        self.assertEqual(len(beta_order), 1)
        self.assertEqual(event.purchase_order_ids, acme_order | beta_order)
        self.assertEqual(acme_order.state, 'draft')
        self.assertEqual(beta_order.state, 'draft')
        self.assertEqual(acme_order.order_line.product_id, self.chair)
        self.assertEqual(acme_order.order_line.product_qty, 60.0)
        self.assertEqual(acme_order.order_line.price_unit, 11.90)
        self.assertEqual(beta_order.order_line.product_id, self.desk)
        self.assertEqual(beta_order.order_line.product_qty, 20.0)
        self.assertEqual(beta_order.order_line.price_unit, 180.0)
        self.assertEqual(acme_order.spm_request_ids, requests)
        self.assertEqual(beta_order.spm_request_ids, requests)
        self.assertEqual(acme_order.payment_term_id, self.payment)
        self.assertFalse(beta_order.payment_term_id)

    def test_sc_0040_1_expected_dates_follow_the_lead_times(self):
        """FR-SPM-0040: the expected date of each order line is the order date plus
        the lead time of the awarded offer (Acme 10 days, Beta 7 days)."""
        event, requests, chairs, desks = self._awarded_per_line()
        with freeze_time('2026-11-25 10:00:00'):
            event.with_user(self.buyer).action_spm_generate_orders()
        self.env.flush_all()
        self.assertEqual(self._orders(event, self.acme).order_line.date_planned.date(), date(2026, 12, 5))
        self.assertEqual(self._orders(event, self.beta).order_line.date_planned.date(), date(2026, 12, 2))

    def test_sc_0040_1_generating_again_updates_the_draft_and_never_confirms(self):
        """FR-SPM-0040: a second run updates the draft order linked to the event
        instead of creating another, and no order is ever confirmed."""
        event, requests, chairs, desks = self._awarded_per_line()
        with freeze_time('2026-11-25 10:00:00'):
            event.with_user(self.buyer).action_spm_generate_orders()
            acme_order = self._orders(event, self.acme)
            acme_order.order_line.price_unit = 1.0
            event.with_user(self.buyer).action_spm_generate_orders()
        self.env.flush_all()
        self.assertEqual(self._orders(event, self.acme), acme_order)
        self.assertEqual(len(self._orders(event, self.beta)), 1)
        self.assertEqual(acme_order.order_line.price_unit, 11.90)
        self.assertEqual(acme_order.state, 'draft')

    def test_sc_0040_2_a_confirmed_order_is_not_changed(self):
        """SC-SPM-0040-2: Acme's order is already confirmed; generating again
        leaves it unchanged and says that the existing order is confirmed."""
        event, requests, chairs, desks = self._awarded_per_line()
        with freeze_time('2026-11-25 10:00:00'):
            event.with_user(self.buyer).action_spm_generate_orders()
            acme_order = self._orders(event, self.acme)
            acme_order.with_user(self.buyer).button_confirm()
            self.assertEqual(acme_order.state, 'purchase')
            before = (acme_order.amount_total, acme_order.order_line.price_unit,
                      acme_order.order_line.product_qty, acme_order.write_date)
            try:
                with self.env.cr.savepoint():
                    result = event.with_user(self.buyer).action_spm_generate_orders()
                message = json.dumps(result, default=str)
            except UserError as error:
                message = str(error)
        self.assertIn('confirmed', message.lower())
        acme_order.invalidate_recordset()
        self.assertEqual(acme_order.state, 'purchase')
        self.assertEqual(
            (acme_order.amount_total, acme_order.order_line.price_unit,
             acme_order.order_line.product_qty, acme_order.write_date), before)
        self.assertEqual(len(self._orders(event, self.acme)), 1)

    # FR-SPM-0041 -----------------------------------------------------------
    def _messages_to(self, *partners):
        """Texts of every mail and message that reached one of the partners."""
        bodies = {}
        for mail in self.env['mail.mail'].sudo().search([]):
            if (partners and (set(partners) & set(mail.recipient_ids))) or any(
                    p.email and p.email.lower() in (mail.email_to or '').lower() for p in partners):
                bodies[('mail', mail.id)] = f'{mail.subject or ""} {mail.body_html or ""}'
        for message in self.env['mail.message'].sudo().search([]):
            reached = set(partners) & set(message.partner_ids | message.notification_ids.res_partner_id)
            if reached:
                bodies[('message', message.id)] = f'{message.subject or ""} {message.body or ""}'
        return bodies

    def test_sc_0041_1_unsuccessful_suppliers_are_told_without_names_or_prices(self):
        """SC-SPM-0041-1: awarded to Beta with 'inform unsuccessful suppliers' set:
        Acme and Gamma receive a message that the event was awarded to another
        offer, naming neither Beta nor a price; their responses stay readable."""
        event = self._scored_event(inform_unsuccessful=True)
        before = {p: set(self._messages_to(p)) for p in (self.acme, self.beta, self.gamma)}
        with freeze_time('2026-11-25 10:00:00'):
            self._award(event, response=self.responses['beta'])
        self.env.flush_all()
        for partner in (self.acme, self.gamma):
            new = {k: v for k, v in self._messages_to(partner).items() if k not in before[partner]}
            self.assertTrue(new, f"{partner.name} is told the result")
            for text in new.values():
                self.assertNotIn('Beta', text)
                for price in ('12.4', '12,4', '11.9', '11,9', '13.0', '13,0', '12.40', '11.90'):
                    self.assertNotIn(price, text)
        self.assertEqual(self.responses['acme'].with_user(self.buyer).current_version_id.line_ids.price_unit, 11.90)
        self.assertEqual(self.responses['gamma'].with_user(self.buyer).current_version_id.line_ids.price_unit, 13.00)
        self.assertEqual(self.responses['acme'].state, 'not_awarded')

    def test_sc_0041_1_nobody_is_told_when_the_event_does_not_say_so(self):
        """FR-SPM-0041: informing the unsuccessful suppliers is optional per event:
        without the setting Acme receives nothing about the award."""
        event = self._scored_event(inform_unsuccessful=False)
        before = set(self._messages_to(self.acme))
        with freeze_time('2026-11-25 10:00:00'):
            self._award(event, response=self.responses['beta'])
        self.env.flush_all()
        self.assertEqual(set(self._messages_to(self.acme)), before)
        self.assertEqual(self.responses['acme'].state, 'not_awarded')
        self.assertTrue(self.responses['acme'].version_ids)
