import logging
from datetime import date

from psycopg2 import errors

from odoo import Command, _, api, fields, models
from odoo.addons.base.models.res_partner import _tz_get
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import safe_eval

_logger = logging.getLogger(__name__)

DATE_TYPES = ['date', 'datetime']


class RecordReminderRule(models.Model):
    """A reminder rule.

    Requirements: FR-SRR-0017 (changing a rule keeps the rule, so its history),
    FR-SRR-0018 (access only through the Reminder Manager group), NFR-SRR-0002.
    """

    _name = 'record.reminder.rule'
    _description = 'Reminder Rule'
    _order = 'name, id'

    name = fields.Char(required=True)
    active = fields.Boolean(
        default=True,
        help="Switch a rule off to pause it. Reminders that fall due while it is off are not sent when it is switched on again.")
    activated_at = fields.Datetime(
        string="Switched on", readonly=True, copy=False,
        help="The moment the rule was last switched on. Only reminders falling due after it are sent.")

    model_id = fields.Many2one(
        'ir.model', string="Record Type", required=True, ondelete='cascade',
        domain=[('transient', '=', False),
                ('field_id', 'any', [('ttype', 'in', DATE_TYPES), ('store', '=', True)])],
        help="The kind of record the rule reminds about, for example Contact or Sales Order.")
    model_name = fields.Char(related='model_id.model', store=True, index=True)
    domain = fields.Char(
        string="Conditions", default='[]',
        help="Only records matching these conditions are reminded about. Leave empty to remind about every record.")

    date_field_id = fields.Many2one(
        'ir.model.fields', string="Date Field", required=True, ondelete='cascade',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ['date', 'datetime']), ('store', '=', True)]",
        help="The date or date-and-time of the record that the reminder is counted from.")
    offset = fields.Integer(
        default=0, help="How many days or hours before or after the date the reminder goes out. 0 means on the date itself.")
    offset_unit = fields.Selection(
        [('days', "Days"), ('hours', "Hours")], required=True, default='days')
    offset_direction = fields.Selection(
        [('before', "Before"), ('after', "After")], required=True, default='before')
    tz = fields.Selection(
        _tz_get, string="Time Zone", required=True,
        default=lambda self: self.env.user.tz or 'UTC',
        help="The time zone in which dates without a time are read: such a date counts from midnight in this zone.")

    channel = fields.Selection(
        [('email', "Email"), ('activity', "Activity")], required=True, default='activity')
    mail_template_id = fields.Many2one(
        'mail.template', string="Email Template", ondelete='restrict',
        domain="[('model', '=', model_name)]",
        help="The email sent as the reminder. It must belong to the record type of the rule.")
    activity_type_id = fields.Many2one(
        'mail.activity.type', string="Activity Type", ondelete='restrict',
        help="The type of the activity created on the record as the reminder.")
    activity_summary = fields.Char(string="Activity Summary")

    recipient_mode = fields.Selection(
        [('field', "From a field of the record"), ('users', "Fixed users")],
        string="Recipients", required=True, default='field')
    recipient_field_id = fields.Many2one(
        'ir.model.fields', string="Recipient Field", ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ['many2one', 'many2many']),"
               " ('relation', 'in', ['res.users', 'res.partner'])]",
        help="The field of the record naming who is reminded: users, or contacts when the reminder is an email.")
    user_ids = fields.Many2many(
        'res.users', 'record_reminder_rule_res_users_rel', 'rule_id', 'user_id',
        string="Fixed Users", help="The users who are reminded about every record.")
    execution_ids = fields.One2many(
        'record.reminder.execution', 'rule_id', string="History")

    @api.onchange('model_id')
    def _onchange_model_id(self):
        """Forget the choices that belong to the previous record type."""
        self.date_field_id = False
        self.recipient_field_id = False
        self.mail_template_id = False

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SRR-0001, FR-SRR-0016. Record the switch-on moment of a rule created active."""
        now = fields.Datetime.now()
        for vals in vals_list:
            if vals.get('active', True) and not vals.get('activated_at'):
                vals['activated_at'] = now
        return super().create(vals_list)

    def write(self, vals):
        """Requirements: FR-SRR-0016. Record a new switch-on moment when a rule goes from off to on."""
        switched_on = self.browse()
        if vals.get('active'):
            switched_on = self.filtered(lambda rule: not rule.active)
        result = super().write(vals)
        if switched_on:
            switched_on.write({'activated_at': fields.Datetime.now()})
        return result

    @api.constrains('model_id', 'date_field_id', 'offset')
    def _check_timing(self):
        """Requirements: FR-SRR-0002. The date field is a stored date of the record type; the offset is not negative."""
        for rule in self:
            field = rule.date_field_id
            if field.model_id != rule.model_id or field.ttype not in DATE_TYPES or not field.store:
                raise ValidationError(_("The date field must be a stored date or date-and-time field of the record type."))
            if rule.offset < 0:
                raise ValidationError(_("The offset cannot be negative."))

    @api.constrains('domain', 'model_id')
    def _check_conditions(self):
        """Requirements: FR-SRR-0002. The conditions are a valid filter of the record type."""
        for rule in self:
            try:
                domain = Domain(safe_eval.safe_eval(rule.domain or '[]', rule._conditions_context()))
                # sudo: the manager saving the rule may lack read access on the watched type; only the filter is checked
                self.env[rule.model_name].sudo()._search(domain)
            except Exception as error:  # noqa: BLE001 - any failure to read or run the filter means it is not valid
                raise ValidationError(_(
                    "The conditions are not a valid filter of %(model)s: %(error)s",
                    model=rule.model_id.name, error=error)) from error

    @api.constrains('channel', 'model_id', 'mail_template_id', 'activity_type_id')
    def _check_channel(self):
        """Requirements: FR-SRR-0002. An email rule has a template of the record type; an activity rule can hold activities."""
        for rule in self:
            if rule.channel == 'email':
                if not rule.mail_template_id or rule.mail_template_id.model != rule.model_name:
                    raise ValidationError(_("An email rule needs an email template of the same record type."))
            elif not rule.model_id.is_mail_activity:
                raise ValidationError(_("Records of type %s cannot hold activities.", rule.model_id.name))
            elif not rule.activity_type_id:
                raise ValidationError(_("An activity rule needs an activity type."))

    @api.constrains('recipient_mode', 'recipient_field_id', 'user_ids', 'channel', 'model_id')
    def _check_recipients(self):
        """Requirements: FR-SRR-0002. The recipient choice names a user, or a field pointing to users (contacts for email)."""
        for rule in self:
            if rule.recipient_mode == 'users':
                if not rule.user_ids:
                    raise ValidationError(_("The rule has no recipient. Choose at least one user."))
                continue
            field = rule.recipient_field_id
            if not field:
                raise ValidationError(_("The rule has no recipient. Choose a recipient field or fixed users."))
            allowed = ['res.users', 'res.partner'] if rule.channel == 'email' else ['res.users']
            if field.model_id != rule.model_id or field.ttype not in ('many2one', 'many2many') or field.relation not in allowed:
                raise ValidationError(_(
                    "The recipient field must point to users, or to contacts for an email rule, on the record type."))

    def _conditions_context(self) -> dict:
        """Names a rule's conditions may use."""
        self.ensure_one()
        return {
            'uid': self.env.uid,
            'user': self.env.user,
            'time': safe_eval.time,
            'datetime': safe_eval.datetime,
            'context_today': lambda: fields.Date.context_today(self),
        }

    @api.ondelete(at_uninstall=False)
    def _unlink_except_with_history(self) -> None:
        """Forbid deleting a rule that already sent or tried a reminder: switching it off keeps its history.

        Removing the app of the watched records still removes the rule with its history.

        Requirements: FR-SRR-0017.
        """
        # sudo: the check counts history lines, which the deleting manager may read anyway
        if self.env['record.reminder.execution'].sudo().search_count([('rule_id', 'in', self.ids)], limit=1):
            raise UserError(_(
                "A rule that already sent or tried a reminder cannot be deleted. Switch it off instead."))

    def _recipients(self, record: models.BaseModel) -> tuple[models.BaseModel, models.BaseModel]:
        """The users and the contacts to remind about a record, read at sending time.

        The users are those to give an activity; the contacts those to email.
        A contact field gives contacts only.

        Requirements: FR-SRR-0006, FR-SRR-0007.
        """
        self.ensure_one()
        if self.recipient_mode == 'users':
            users = self.user_ids.filtered('active')
            if not users:
                raise UserError(_("No active user in the rule's fixed users."))
            return users, users.partner_id
        field = self.recipient_field_id
        value = record[field.name]
        if field.relation == 'res.users':
            users = value.filtered('active')
            partners = users.partner_id
        else:
            users, partners = self.env['res.users'], value
        if not partners:
            raise UserError(_("No recipient: the field %s is empty on this record.", field.field_description))
        return users, partners

    def _send_one(self, record: models.BaseModel, reminder_key: str, reminder_date: date) -> models.BaseModel:
        """Send the reminder of one record once and write its history line.

        The sent line is claimed first, inside a savepoint, so the database
        refuses a second send of the same record, rule and reminder date; any
        error undoes that reminder only and becomes a failed line, also when
        the record was deleted before sending.

        Requirements: FR-SRR-0006, FR-SRR-0007, FR-SRR-0008, FR-SRR-0009,
        FR-SRR-0010, FR-SRR-0011, FR-SRR-0012, BR-SRR-0001.

        :return: the history line written, empty when it was already sent
        """
        self.ensure_one()
        # sudo: history lines are written by the engine only, managers can read them
        executions = self.env['record.reminder.execution'].sudo()
        counts = dict(executions._read_group(
            [('rule_id', '=', self.id), ('res_model', '=', record._name),
             ('res_id', '=', record.id), ('reminder_key', '=', reminder_key)],
            ['state'], ['__count']))
        if counts.get('sent'):
            return executions
        values = {
            'rule_id': self.id, 'res_model': record._name, 'res_id': record.id,
            'reminder_key': reminder_key, 'reminder_date': reminder_date, 'channel': self.channel,
            'attempt': counts.get('failed', 0) + 1,
        }
        if not record.exists():
            return executions.create(dict(values, state='failed', error_message=_("The record no longer exists.")))
        values['record_name'] = record.display_name
        try:
            with self.env.cr.savepoint():
                users, partners = self._recipients(record)
                line = executions.create(dict(values, state='sent', partner_ids=[Command.set(partners.ids)]))
                self._deliver(record, users, partners, reminder_date)
            return line
        except errors.UniqueViolation:
            _logger.info("reminders: rule %s: %s %s already sent for %s", self.id, record._name, record.id, reminder_key)
            return executions
        except Exception as error:  # noqa: BLE001 - one failed reminder must not stop the others (FR-SRR-0012)
            _logger.warning("reminders: rule %s failed on %s %s", self.id, record._name, record.id, exc_info=True)
            return executions.create(dict(values, state='failed', error_message=str(error)))

    def _deliver(self, record: models.BaseModel, users: models.BaseModel, partners: models.BaseModel,
                 reminder_date: date) -> None:
        """Send the email to the contacts, or create one activity per user."""
        self.ensure_one()
        if self.channel == 'email':
            # sudo: the template is rendered and mailed as the engine, managers are trusted to send (FR-SRR-0018)
            self.mail_template_id.sudo().send_mail(
                record.id, force_send=True, raise_exception=True,
                email_values={
                    'recipient_ids': [Command.set(partners.ids)],
                    'email_to': False, 'email_cc': False,
                    'recipient_cc_ids': [Command.clear()],
                })
            return
        for user in users:
            # sudo: the recipient need not be the user who runs the rule
            record.sudo().activity_schedule(
                date_deadline=reminder_date, summary=self.activity_summary or '',
                user_id=user.id, activity_type_id=self.activity_type_id.id)
