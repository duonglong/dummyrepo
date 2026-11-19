from markupsafe import Markup

from odoo import fields, models
from odoo.exceptions import AccessError, UserError

from ..services import portal_access, sourcing

# The longest question a supplier asks from the portal.
QUESTION_MAX = 2000


class ProcurementSourcingQuestion(models.Model):
    """A supplier's question on a sourcing event and the buyer's answer, private or shared.

    Requirements: FR-SPM-0023, BR-SPM-0003
    The asker is readable by internal users and by the asker itself (check_field_access), so a
    supplier who gets a shared answer never learns who asked; the portal record rule shows a supplier its own questions and
    the shared ones of the events it is invited to.
    """
    _name = 'procurement.sourcing.question'
    _description = 'Sourcing Question'
    _order = 'id desc'

    event_id = fields.Many2one(
        'procurement.sourcing.event', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one(
        'res.partner', string="Asked by", required=True, ondelete='restrict', index=True)
    question = fields.Text(required=True)
    answer = fields.Text()
    shared = fields.Boolean(string="Shared with all suppliers", copy=False)
    answered_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    asked_date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    answered_date = fields.Datetime(readonly=True, copy=False)
    company_id = fields.Many2one(related='event_id.company_id', store=True, index=True)

    def check_field_access(self, field, operation):
        """A supplier portal user reads the asker of its own questions only, never of a shared one.

        Requirements: FR-SPM-0023, BR-SPM-0003
        Decided per record and never memorised (the native memo is per field); a search or a
        grouping by the asker is refused too, as it would tell who asked.
        """
        user = self.env.user
        if field.name == 'partner_id' and not self.env.su and user.share:
            supplier = user.sudo().partner_id.commercial_partner_id  # sudo: the company contact of the session user
            if not self or any(asker != supplier for asker in self.sudo().partner_id):  # sudo: the asker is compared, not shown
                raise AccessError(self.env._("You cannot see who asked a question."))
            return
        return super().check_field_access(field, operation)

    def spm_ask(self, event_id, text):
        """Record a question of the session user's supplier on an open event it is invited to.

        Requirements: FR-SPM-0023, BR-SPM-0003
        The event id comes from the client: it must be an open event the supplier has a
        response for (the portal rule decides what the user reads), then the question is
        created with elevated rights, as a portal user has no create right.
        """
        text = (text or '').strip()
        if not text:
            raise UserError(self.env._("A question cannot be empty."))
        if len(text) > QUESTION_MAX:
            raise UserError(self.env._("A question holds at most %s characters.", QUESTION_MAX))
        supplier = portal_access.current_supplier(self.env)
        response = self.env['procurement.sourcing.response'].search(
            [('event_id', '=', event_id), ('partner_id', '=', supplier.id)], limit=1)
        # sudo: the portal user has no right on the event; its state is read after the invitation check
        event = response.event_id.sudo()
        if not response or event.state != 'open':
            raise AccessError(self.env._("You cannot ask a question on this quote request."))
        # sudo: a portal user cannot create; the values are set here after the checks above
        question = self.sudo().create({
            'event_id': event.id, 'partner_id': supplier.id, 'question': text})
        # sudo (event): the event thread is internal; the note records who asked, for the buyer
        event.message_post(
            body=Markup("%s: %s") % (supplier.name, text), subtype_xmlid='mail.mt_note',
            partner_ids=event.buyer_id.partner_id.ids, author_id=self.env.user.partner_id.id)
        return question.with_env(self.env)

    def action_spm_answer(self, answer, shared=False):
        """Answer the question: to the asker, or to every invited supplier without the asker.

        Requirements: FR-SPM-0023, BR-SPM-0003
        Each supplier gets its own message, so no recipient list names another supplier.
        """
        answer = (answer or '').strip()
        if not answer:
            raise UserError(self.env._("An answer cannot be empty."))
        for question in self:
            question.check_access('write')
            question.write({
                'answer': answer, 'shared': bool(shared), 'answered_by_id': self.env.user.id,
                'answered_date': fields.Datetime.now()})
            event = question.event_id
            suppliers = event.response_ids.partner_id if shared else question.partner_id
            body = Markup("<p>%s</p><p>%s</p>") % (question.question, answer)
            for supplier in suppliers:
                recipients = sourcing.portal_users(self.env, supplier).partner_id
                if recipients:
                    event.message_post(
                        body=body, subject=self.env._("Answer on %s", event.name),
                        message_type='comment', subtype_xmlid='mail.mt_comment',
                        partner_ids=recipients.ids)
        return True

    def action_spm_send_answer(self):
        """Send the answer typed on the form with its Shared choice.

        Requirements: FR-SPM-0023
        """
        for question in self:
            question.action_spm_answer(question.answer, question.shared)
        return True
