from odoo import fields, models


class RecordReminderExecution(models.Model):
    """One try to send one reminder: the history and the ledger against duplicates.

    Requirements: FR-SRR-0010, FR-SRR-0011, FR-SRR-0012, BR-SRR-0001.
    """

    _name = 'record.reminder.execution'
    _description = 'Reminder Execution'
    _order = 'executed_at desc, id desc'

    _sent_key_uniq = models.UniqueIndex(
        "(rule_id, res_model, res_id, reminder_key) WHERE state = 'sent'")
    _key_idx = models.Index("(rule_id, res_id, reminder_key)")

    rule_id = fields.Many2one(
        'record.reminder.rule', string="Rule", required=True, index=True, ondelete='cascade')
    res_model = fields.Char(string="Record Type", required=True, index=True)
    res_id = fields.Many2oneReference(
        string="Record", model_field='res_model', required=True, index=True)
    record_name = fields.Char(
        string="Record Name", help="The name of the record when the reminder was sent.")
    reminder_key = fields.Char(
        required=True, help="The value of the rule's date field at sending time, as text.")
    reminder_date = fields.Date(help="The day of the reminder date in the rule's time zone.")
    channel = fields.Selection(
        [('email', "Email"), ('activity', "Activity")], required=True)
    partner_ids = fields.Many2many(
        'res.partner', 'record_reminder_execution_res_partner_rel', 'execution_id', 'partner_id',
        string="Recipients")
    state = fields.Selection([('sent', "Sent"), ('failed', "Failed")], required=True)
    error_message = fields.Text(string="Error")
    executed_at = fields.Datetime(
        string="Time", required=True, default=fields.Datetime.now)
    attempt = fields.Integer(help="Which try of this reminder this line is.")
