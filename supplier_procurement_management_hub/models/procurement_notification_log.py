from odoo import fields, models

from ..services import notification


class ProcurementNotificationLog(models.Model):
    """The key of one notification sent: its kind, record, recipient and due date.

    Requirements: BR-SPM-0019
    A key exists once; the notification service inserts it before it sends, so a repeated run finds it
    and sends nothing. Rows are written by the service only.
    """
    _name = 'procurement.notification.log'
    _description = 'Notification Sent'
    _order = 'id desc'

    kind = fields.Char(required=True, readonly=True, index=True)
    res_model = fields.Char(string="Model", required=True, readonly=True)
    res_id = fields.Integer(string="Record", required=True, readonly=True)
    recipient_partner_id = fields.Many2one(
        'res.partner', string="Recipient", required=True, readonly=True, ondelete='cascade', index=True)
    due_date = fields.Date(required=True, readonly=True)
    company_id = fields.Many2one(
        'res.company', string="Company", readonly=True, index=True, ondelete='cascade',
        help="The company of the record the notification is about; empty for a shared record.")
    sent_date = fields.Datetime(string="Sent", readonly=True)

    _key_unique = models.Constraint(
        'UNIQUE(kind, res_model, res_id, recipient_partner_id, due_date)',
        'The same notification is sent once per record, recipient and due date.')


class ProcurementJob(models.Model):  # sdd-allow: model-split the job runner is extended per code by the task that owns the job (procurement_job.py, not a file of this task)
    """The daily reminders and escalations run as jobs of the app (ADR-SPM-007).

    Requirements: FR-SPM-0091, FR-SPM-0092, FR-SPM-0093, FR-SPM-0094, FR-SPM-0096
    """
    _inherit = 'procurement.job'

    code = fields.Selection(selection_add=[
        ('document_expiry', 'Supplier document expiry reminders'),
        ('quote_reminder', 'Quote deadline reminders'),
        ('delivery_escalation', 'Delivery escalation'),
        ('contract_reminder', 'Contract notice and renewal reminders'),
    ], ondelete={
        'document_expiry': 'cascade', 'quote_reminder': 'cascade',
        'delivery_escalation': 'cascade', 'contract_reminder': 'cascade',
    })

    def _spm_collect_document_expiry(self):
        """Return the documents to remind today.

        Requirements: FR-SPM-0091
        """
        return notification.due_documents(self.env)

    def _spm_process_document_expiry(self, document):
        """Remind one document and return the notifications sent.

        Requirements: FR-SPM-0091, BR-SPM-0019
        """
        return notification.remind_document(self.env, document)

    def _spm_collect_quote_reminder(self):
        """Return the open events that still wait for an answer.

        Requirements: FR-SPM-0092
        """
        return notification.open_events_waiting(self.env)

    def _spm_process_quote_reminder(self, event):
        """Remind the silent suppliers of one event, or tell its buyer after the deadline.

        Requirements: FR-SPM-0092, BR-SPM-0019
        """
        return notification.remind_event(self.env, event)

    def _spm_collect_delivery_escalation(self):
        """Return the late delivery commitments.

        Requirements: FR-SPM-0093
        """
        return notification.overdue_commitments(self.env)

    def _spm_process_delivery_escalation(self, commitment):
        """Escalate one commitment and return the notifications sent.

        Requirements: FR-SPM-0093, BR-SPM-0019
        """
        return notification.escalate_commitment(self.env, commitment)

    def _spm_collect_contract_reminder(self):
        """Return the active contracts that have not ended.

        Requirements: FR-SPM-0094
        """
        return notification.contracts_with_dates_ahead(self.env)

    def _spm_process_contract_reminder(self, contract):
        """Remind the owner of one contract and return the notifications sent.

        Requirements: FR-SPM-0094, BR-SPM-0019
        """
        return notification.remind_contract(self.env, contract)


class ProcurementContract(models.Model):  # sdd-allow: model-split the reminder period belongs to the reminder jobs of this task, not to the contract file of T-021
    """The reminder period of a contract.

    Requirements: FR-SPM-0094
    """
    _inherit = 'procurement.contract'

    reminder_days = fields.Integer(
        string="Reminder period (days)", default=30,
        help="Days before the notice date and the end date when the owner is reminded.")
