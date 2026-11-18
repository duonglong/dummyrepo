"""Acceptance tests of T-013 (sourcing events: create, send, change deadline,
cancel), written from the specification.

Requirements: FR-SPM-0031, FR-SPM-0032, FR-SPM-0033, FR-SPM-0042, FR-SPM-0016

Names used (the design names none, fixed in tests/portal_common.py):
procurement.request.action_spm_create_sourcing_event() (the "Create sourcing
event" button on the selected requests), procurement.sourcing.event.action_spm_send(),
action_spm_cancel(reason). The event form and the reason dialog are driven with
Form; a deadline change is a plain edit of the deadline field of an open event.
The quote-request part of SC-SPM-0016-1 is here because it needs events.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form

from .portal_common import SpmPortalCase


class TestSourcingEventAcceptance(SpmPortalCase):
    """Requirements: FR-SPM-0031, FR-SPM-0032, FR-SPM-0033, FR-SPM-0042, FR-SPM-0016"""

    def _request(self, quantity):
        request = self.env['procurement.request'].with_user(self.requester).create({
            'needed_date': fields.Date.today() + timedelta(days=60), 'reason': 'New floor',
            'line_ids': [(0, 0, {
                'name': 'Office chair', 'product_id': self.chair.id,
                'quantity': quantity, 'uom_id': self.unit.id})],
        })
        request.with_user(self.requester).action_spm_submit()
        return request

    # FR-SPM-0031 -----------------------------------------------------------
    def test_sc_0031_1_event_from_two_requests_merges_the_lines(self):
        """SC-SPM-0031-1: an event made from requests of 40 and 20 chairs has
        one line of 60 and both requests are In sourcing."""
        requests = self._request(40.0) | self._request(20.0)
        requests.with_user(self.buyer).action_spm_create_sourcing_event()
        self.env.flush_all()
        event = self.env['procurement.sourcing.event'].search([('request_ids', 'in', requests.ids)])
        self.assertEqual(len(event), 1)
        self.assertEqual(event.request_ids, requests)
        self.assertEqual(len(event.line_ids), 1)
        self.assertEqual(event.line_ids.quantity, 60.0)
        self.assertEqual(event.line_ids.product_id, self.chair)
        self.assertEqual(event.state, 'draft')
        self.assertEqual(set(requests.mapped('state')), {'sourcing'})
        self.assertEqual(requests.sourcing_event_ids, event)

    def test_sc_0031_2_a_past_deadline_is_refused(self):
        """SC-SPM-0031-2: setting the deadline a day before today and saving is
        refused with a message that the deadline is in the past."""
        form = Form(self.env['procurement.sourcing.event'].with_user(self.buyer))
        form.title = 'Chairs'
        form.deadline = fields.Datetime.now() - timedelta(days=1)
        with self.assertRaises((ValidationError, UserError)) as ctx:
            form.save()
        self.assertIn('past', str(ctx.exception).lower())

    def test_sc_0031_2_a_future_deadline_is_saved(self):
        """FR-SPM-0031 boundary: a deadline in the future is accepted."""
        form = Form(self.env['procurement.sourcing.event'].with_user(self.buyer))
        form.title = 'Chairs'
        form.deadline = fields.Datetime.now() + timedelta(days=1)
        event = form.save()
        self.assertEqual(event.state, 'draft')
        self.assertTrue(event.name)

    # FR-SPM-0032 -----------------------------------------------------------
    def test_sc_0032_1_sending_opens_the_event_with_one_response_per_supplier(self):
        """SC-SPM-0032-1: SE with Acme, Beta and Gamma invited is Open after
        sending, with three separate responses, and each supplier's portal user
        receives the invitation e-mail with a portal link."""
        event = self._event(self.acme | self.beta | self.gamma, send=False)
        event.with_user(self.buyer).action_spm_send()
        self.env.flush_all()
        self.assertEqual(event.state, 'open')
        self.assertEqual(len(event.response_ids), 3)
        self.assertEqual(event.response_ids.partner_id, self.acme | self.beta | self.gamma)
        self.assertEqual(set(event.response_ids.mapped('state')), {'invited'})
        for user in (self.acme_user, self.beta_user, self.gamma_user):
            self.assertTrue(
                self._sent_to(user.partner_id, '/my/procurement'),
                f"{user.name} receives the invitation")

    def test_sc_0032_2_sending_without_a_supplier_is_refused(self):
        """SC-SPM-0032-2: sending an event with no invited supplier is refused
        with a message that at least one supplier must be invited."""
        event = self._event(self.env['res.partner'], send=False)
        with self.assertRaises(UserError) as ctx:
            event.with_user(self.buyer).action_spm_send()
        self.assertIn('at least one supplier', str(ctx.exception).lower())
        self.assertEqual(event.state, 'draft')
        self.assertFalse(event.response_ids)

    def test_sc_0016_1_home_lists_only_invited_quote_requests(self):
        """SC-SPM-0016-1 (quote request part): Acme is invited to one event and
        Beta to another; an Acme portal user sees 1 quote request and nothing of Beta's."""
        own = self._event(self.acme, title='Chairs for Acme only')
        other = self._event(self.beta, title='Secret tender of Beta')
        draft = self._event(self.acme, title='Draft not yet sent', send=False)
        self._login(self.acme_user)
        text = self._get('/my/procurement').text
        self.assertIn(own.title, text)
        self.assertNotIn(other.title, text)
        self.assertNotIn(draft.title, text)
        self.assertIn(own.name, text)
        self.assertNotIn(other.name, text)

    # FR-SPM-0033 -----------------------------------------------------------
    def test_sc_0033_1_deadline_change_notifies_and_is_recorded(self):
        """SC-SPM-0033-1: moving the deadline of an open event notifies Acme,
        Beta and Gamma of the new date and the history shows old and new."""
        noon = {'hour': 12, 'minute': 0, 'second': 0, 'microsecond': 0}
        old = (fields.Datetime.now() + timedelta(days=10)).replace(**noon)
        new = (fields.Datetime.now() + timedelta(days=24)).replace(**noon)
        event = self._event(self.acme | self.beta | self.gamma, deadline=old)
        self.env.flush_all()
        event.with_user(self.buyer).write({'deadline': new})
        self.env.flush_all()
        tokens = [new.strftime(fmt) for fmt in (
            '%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%d.%m.%Y', '%d-%m-%Y', '%b %d, %Y', '%B %d, %Y',
            '%d %B %Y', '%d %b %Y')]
        tokens += [new.strftime('%b %d, %Y').replace(' 0', ' '), new.strftime('%B %d, %Y').replace(' 0', ' ')]
        for user in (self.acme_user, self.beta_user, self.gamma_user):
            self.assertTrue(
                any(self._sent_to(user.partner_id, event.name, token) for token in tokens),
                f"{user.name} is told of the new deadline")
        values = event.message_ids.tracking_value_ids.filtered(
            lambda tracking: tracking.field_id.name == 'deadline')
        self.assertEqual(len(values), 1)
        self.assertEqual(values.old_value_datetime, old.replace(microsecond=0))
        self.assertEqual(values.new_value_datetime, new.replace(microsecond=0))

    def test_sc_0033_1_a_past_new_deadline_is_refused(self):
        """FR-SPM-0033 / FR-SPM-0031: the deadline of an open event cannot move into the past."""
        event = self._event(self.acme)
        with self.assertRaises((ValidationError, UserError)):
            event.with_user(self.buyer).write({'deadline': fields.Datetime.now() - timedelta(hours=1)})

    # FR-SPM-0042 -----------------------------------------------------------
    def test_sc_0042_1_cancelling_notifies_suppliers_and_frees_the_requests(self):
        """SC-SPM-0042-1: cancelling open SE/0010 (covering PR/0003) with the
        reason 'Budget frozen' closes it, keeps the responses, notifies the
        suppliers and sets the request back to Submitted."""
        request = self._request(40.0)
        request.with_user(self.buyer).action_spm_create_sourcing_event()
        event = self.env['procurement.sourcing.event'].search([('request_ids', 'in', request.ids)])
        event.write({'invited_partner_ids': [(6, 0, (self.acme | self.beta).ids)]})
        event.with_user(self.buyer).action_spm_send()
        self.assertEqual(request.state, 'sourcing')
        wizard = Form(self.env['procurement.reason.wizard'].with_user(self.buyer).with_context(
            default_res_model=event._name, default_res_ids=event.ids, default_action='cancel'))
        wizard.reason = 'Budget frozen'
        wizard.save().action_confirm()
        self.env.flush_all()
        self.assertEqual(event.state, 'cancelled')
        self.assertEqual(event.cancel_reason, 'Budget frozen')
        self.assertEqual(len(event.response_ids), 2)
        self.assertEqual(request.state, 'submitted')
        for user in (self.acme_user, self.beta_user):
            self.assertTrue(self._sent_to(user.partner_id, event.name),
                            f"{user.name} is told of the cancellation")

    def test_sc_0042_1_cancelling_needs_a_reason(self):
        """FR-SPM-0042: a cancellation without a reason is refused."""
        event = self._event(self.acme)
        with self.assertRaises((UserError, ValidationError)):
            event.with_user(self.buyer).action_spm_cancel('')
        self.assertEqual(event.state, 'open')

    def test_sc_0042_2_an_awarded_event_cannot_be_cancelled(self):
        """SC-SPM-0042-2: cancelling an awarded event is refused with a message
        that the event is already awarded."""
        event = self._event(self.acme)
        event.sudo().write({'state': 'awarded'})
        with self.assertRaises(UserError) as ctx:
            event.with_user(self.buyer).action_spm_cancel('Changed our mind')
        self.assertIn('awarded', str(ctx.exception).lower())
        self.assertEqual(event.state, 'awarded')
