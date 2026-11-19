"""Unit tests of T-013: sourcing events (create, send, change deadline, cancel).

Requirements: FR-SPM-0031, FR-SPM-0032, FR-SPM-0033, FR-SPM-0042
"""
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError

from .portal_common import SpmPortalCase


class TestSourcingEvent(SpmPortalCase):
    """Requirements: FR-SPM-0031, FR-SPM-0032, FR-SPM-0033, FR-SPM-0042"""

    def _submitted_request(self, quantity=5.0):
        request = self.env['procurement.request'].with_user(self.requester).create({
            'needed_date': fields.Date.today() + timedelta(days=30), 'reason': 'Need',
            'line_ids': [(0, 0, {
                'name': 'Office chair', 'product_id': self.chair.id,
                'quantity': quantity, 'uom_id': self.unit.id})]})
        request.action_spm_submit()
        return request

    def test_deadline_history_keeps_old_and_new(self):
        """FR-SPM-0033: the tracking of the deadline holds the old and the new value."""
        old = fields.Datetime.now().replace(microsecond=0) + timedelta(days=5)
        new = old + timedelta(days=3)
        event = self._event(self.acme, deadline=old)
        self.env.cr.precommit.run()  # closes the creation: Odoo tracks only changes after it
        event.write({'deadline': new})
        self.env.flush_all()
        self.env.cr.precommit.run()  # the tracking messages are written at precommit
        values = event.sudo().message_ids.tracking_value_ids.filtered(
            lambda value: value.field_id.name == 'deadline')
        self.assertEqual(len(values), 1)
        self.assertEqual(values.old_value_datetime, old)
        self.assertEqual(values.new_value_datetime, new)

    def test_draft_deadline_change_notifies_nobody(self):
        """FR-SPM-0033: only an open event tells its suppliers of a new deadline."""
        event = self._event(self.acme, send=False)
        before = self.env['mail.mail'].sudo().search_count([])
        event.write({'deadline': fields.Datetime.now() + timedelta(days=20)})
        self.assertEqual(self.env['mail.mail'].sudo().search_count([]), before)

    def test_state_is_not_written_by_a_buyer(self):
        """FR-SPM-0042: the state and the reason change only through the buttons."""
        event = self._event(self.acme, send=False)
        with self.assertRaises(AccessError):
            event.with_user(self.buyer).write({'state': 'open'})
        with self.assertRaises(AccessError):
            event.with_user(self.buyer).write({'cancel_reason': 'x'})
        created = self.env['procurement.sourcing.event'].with_user(self.buyer).create({
            'title': 'Forced', 'state': 'open', 'cancel_reason': 'x'})
        self.assertEqual(created.state, 'draft')
        self.assertFalse(created.cancel_reason)

    def test_cancelled_event_deadline_is_frozen(self):
        """FR-SPM-0033: the deadline of a cancelled event cannot move."""
        event = self._event(self.acme)
        event.action_spm_cancel('Not needed')
        with self.assertRaises(UserError):
            event.write({'deadline': fields.Datetime.now() + timedelta(days=30)})

    def test_blocked_supplier_cannot_be_invited(self):
        """FR-SPM-0032: supplier control refuses a Blocked supplier at sending."""
        self.company.sudo().spm_supplier_control = True
        self.acme.sudo().spm_status_id = self.blocked
        event = self._event(self.acme | self.beta, send=False)
        with self.assertRaises(UserError):
            event.with_user(self.buyer).action_spm_send()
        self.assertEqual(event.state, 'draft')
        self.assertFalse(event.response_ids)

    def test_sending_twice_is_refused(self):
        """FR-SPM-0032: only a draft event is sent."""
        event = self._event(self.acme)
        with self.assertRaises(UserError):
            event.with_user(self.buyer).action_spm_send()

    def test_only_a_buyer_creates_an_event_from_requests(self):
        """FR-SPM-0031: a Requester cannot source; a draft request is refused."""
        request = self._submitted_request()
        with self.assertRaises(AccessError):
            request.with_user(self.requester).action_spm_create_sourcing_event()
        draft = self.env['procurement.request'].with_user(self.requester).create({
            'needed_date': fields.Date.today() + timedelta(days=30), 'reason': 'Need',
            'line_ids': [(0, 0, {'name': 'x', 'quantity': 1.0})]})
        with self.assertRaises(UserError):
            draft.with_user(self.buyer).action_spm_create_sourcing_event()

    def test_different_units_stay_separate_lines(self):
        """FR-SPM-0031: lines merge by product and unit, not by product alone."""
        dozen = self.env.ref('uom.product_uom_dozen')
        first = self._submitted_request(2.0)
        second = self._submitted_request(1.0)
        second.line_ids.sudo().write({'uom_id': dozen.id})
        (first | second).with_user(self.buyer).action_spm_create_sourcing_event()
        event = self.env['procurement.sourcing.event'].search([('request_ids', 'in', first.ids)])
        self.assertEqual(sorted(event.line_ids.mapped('quantity')), [1.0, 2.0])

    def test_cancel_keeps_a_request_covered_by_another_live_event(self):
        """FR-SPM-0042: a request in another live event stays In sourcing."""
        request = self._submitted_request()
        request.with_user(self.buyer).action_spm_create_sourcing_event()
        first = request.sourcing_event_ids
        second = self.env['procurement.sourcing.event'].with_user(self.buyer).create({
            'title': 'Again', 'request_ids': [(6, 0, request.ids)]})
        first.with_user(self.buyer).action_spm_cancel('Merged elsewhere')
        self.assertEqual(request.state, 'sourcing')
        second.with_user(self.buyer).action_spm_cancel('Not needed')
        self.assertEqual(request.state, 'submitted')

    def test_cancelling_twice_is_refused(self):
        """FR-SPM-0042: a cancelled event is not cancelled again."""
        event = self._event(self.acme)
        event.with_user(self.buyer).action_spm_cancel('Budget frozen')
        with self.assertRaises(UserError):
            event.with_user(self.buyer).action_spm_cancel('Again')

    def test_template_copies_criteria(self):
        """FR-SPM-0031: creating an event from a template copies its criteria."""
        template = self.env['procurement.sourcing.template'].create({
            'name': 'Standard', 'criterion_ids': [(0, 0, {'name': 'Price', 'kind': 'price', 'weight': 60.0})]})
        event = self.env['procurement.sourcing.event'].with_user(self.buyer).create({
            'title': 'From template', 'template_id': template.id})
        self.assertEqual(event.criterion_ids.mapped('name'), ['Price'])
        self.assertEqual(event.criterion_ids.weight, 60.0)

    def test_send_refuses_a_deadline_that_has_passed(self):
        """FR-SPM-0032: a draft whose deadline went by while it waited is not sent."""
        event = self._event(self.acme, send=False)
        self.env.cr.execute(
            "UPDATE procurement_sourcing_event SET deadline = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(hours=1), event.id))
        event.invalidate_recordset(['deadline'])
        with self.assertRaises(UserError):
            event.with_user(self.buyer).action_spm_send()
        self.assertEqual(event.state, 'draft')
        self.assertFalse(event.response_ids)

    def test_mail_shows_the_deadline_in_utc_whatever_the_buyer_zone(self):
        """FR-SPM-0033: the (UTC) hour of the mail does not follow the buyer's time zone."""
        self.buyer.tz = 'Asia/Tokyo'
        event = self._event(self.acme, deadline=fields.Datetime.from_string('2031-05-06 10:30:00'))
        mails = self.env['mail.mail'].sudo().search([('model', '=', event._name), ('res_id', '=', event.id)])
        self.assertTrue(mails)
        self.assertTrue(all('2031-05-06 10:30' in (mail.body_html or '') for mail in mails))

    def test_event_line_links_must_belong_to_the_company(self):
        """FR-SPM-0031: a product or request line of another company is refused."""
        other = self.env['res.company'].sudo().create({'name': 'Other company lines'})
        product = self.env['product.product'].sudo().create({'name': 'Foreign product', 'company_id': other.id})
        event = self._event(self.acme, send=False)
        line = event.line_ids[:1]
        with self.assertRaises(UserError):
            line.write({'product_id': product.id})
        request = self.env['procurement.request'].sudo().create({
            'company_id': other.id,
            'needed_date': fields.Date.today() + timedelta(days=5),
            'reason': 'Other company need',
            'line_ids': [Command.create({'name': 'x', 'quantity': 1})],
        })
        with self.assertRaises(UserError):
            line.write({'request_line_ids': [Command.link(request.line_ids.id)]})

    def test_event_links_must_belong_to_the_company(self):
        """FR-SPM-0031: a delivery location of another company is refused."""
        other = self.env['res.company'].sudo().create({'name': 'Other company'})
        picking_type = self.env['stock.picking.type'].sudo().search(
            [('code', '=', 'incoming'), ('company_id', '=', other.id)], limit=1)
        if not picking_type:
            self.skipTest("no receipt operation type for the second company")
        event = self._event(self.acme, send=False)
        with self.assertRaises(UserError):
            event.write({'picking_type_id': picking_type.id})
