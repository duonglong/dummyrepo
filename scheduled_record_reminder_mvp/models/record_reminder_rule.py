import logging
import time
from datetime import date, datetime, timedelta

import pytz
from psycopg2 import errors

from odoo import Command, _, api, fields, modules, models
from odoo.addons.base.models.res_partner import _tz_get
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import safe_eval

_logger = logging.getLogger(__name__)

DATE_TYPES = ['date', 'datetime']
MAX_TRIES = 3  # a failed reminder is tried again by the scheduled run until it has failed this many times (BR-SRR-0004)
COMMIT_EVERY = 100  # the scheduled run commits its progress after this many reminders


class RecordReminderRule(models.Model):
    """A reminder rule.

    Requirements: FR-SRR-0017 (changing a rule keeps the rule, so its history),
    FR-SRR-0018 (access only through the Reminder Manager group), NFR-SRR-0002.
    The due-window engine and the scheduled run are in the methods below.
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

    def _compute_due_moment(self, value: date | datetime) -> datetime:
        """The UTC moment a reminder is due for a record whose date field holds ``value``.

        A date without time counts from 00:00 in the rule's time zone; a date
        with time is taken as it is. The offset is then taken off (before) or
        added (after). Days of a date-only value are calendar days of the
        rule's time zone; hours are elapsed hours.

        Requirements: FR-SRR-0004, BR-SRR-0002.
        """
        self.ensure_one()
        sign = -1 if self.offset_direction == 'before' else 1
        delta = timedelta(**{self.offset_unit: sign * self.offset})
        if isinstance(value, datetime):
            return value + delta
        zone = pytz.timezone(self.tz)
        local = datetime.combine(value, datetime.min.time())
        if self.offset_unit == 'days':
            return zone.localize(local + delta).astimezone(pytz.utc).replace(tzinfo=None)
        return zone.localize(local).astimezone(pytz.utc).replace(tzinfo=None) + delta

    def _value_bounds(self, start: datetime, end: datetime) -> tuple[date | datetime, date | datetime]:
        """Bounds on the date field that surely hold every value whose due moment is in [start, end].

        A date-only field is widened by one calendar day on each side, so the
        time zone and the day-length changes cannot cut a value off; the exact
        check is made on the candidates (ADR-SRR-003).

        Requirements: FR-SRR-0004, BR-SRR-0003.
        """
        self.ensure_one()
        sign = 1 if self.offset_direction == 'before' else -1
        delta = timedelta(**{self.offset_unit: sign * self.offset})
        low, high = start + delta, end + delta
        if self.date_field_id.ttype == 'datetime':
            return low, high
        zone = pytz.timezone(self.tz)

        def local_day(moment: datetime) -> date:
            return pytz.utc.localize(moment).astimezone(zone).date()

        return local_day(low) - timedelta(days=1), local_day(high) + timedelta(days=1)

    def _collect_due(self, now: datetime, include_exhausted: bool = False) -> list[tuple[models.BaseModel, str, date]]:
        """The reminders due at ``now`` that were not sent yet, oldest first.

        A reminder is due when its moment is at or after the moment the rule
        was switched on and at or before ``now``. Reminders that already failed
        ``MAX_TRIES`` times are left out unless ``include_exhausted``.

        :return: list of ``(record, reminder_key, reminder_date)``
        Requirements: FR-SRR-0003, FR-SRR-0004, FR-SRR-0005, FR-SRR-0013,
        BR-SRR-0002, BR-SRR-0003, BR-SRR-0004.
        """
        self.ensure_one()
        start = self.activated_at or self.create_date
        field = self.date_field_id
        low, high = self._value_bounds(start, now)
        domain = Domain(safe_eval.safe_eval(self.domain or '[]', self._conditions_context())) & Domain([
            (field.name, '!=', False), (field.name, '>=', low), (field.name, '<=', high)])
        # sudo: the manager running the rule may lack read access on the watched type; the conditions are the filter
        records = self.env[self.model_name].sudo().search(domain)
        if not records:
            return []
        zone = pytz.timezone(self.tz)
        in_window = {}
        for record in records:
            value = record[field.name]
            due = self._compute_due_moment(value)
            if start <= due <= now:
                in_window[record.id] = (due, record, self._reminder_key(value))
        done = {}
        # sudo: the history is read by the engine to know what was sent; managers may not read every line
        for res_id, reminder_key, state, count in self.env['record.reminder.execution'].sudo()._read_group(
                [('rule_id', '=', self.id), ('res_model', '=', self.model_name), ('res_id', 'in', list(in_window))],
                ['res_id', 'reminder_key', 'state'], ['__count']):
            done.setdefault((res_id, reminder_key), {})[state] = count
        due_reminders = []
        for res_id, (due, record, reminder_key) in in_window.items():
            tries = done.get((res_id, reminder_key), {})
            if tries.get('sent') or (tries.get('failed', 0) >= MAX_TRIES and not include_exhausted):
                continue
            reminder_date = pytz.utc.localize(due).astimezone(zone).date()
            due_reminders.append((due, record, reminder_key, reminder_date))
        due_reminders.sort(key=lambda item: (item[0], item[1].id))
        return [(record, reminder_key, reminder_date) for _due, record, reminder_key, reminder_date in due_reminders]

    def _reminder_key(self, value: date | datetime) -> str:
        """The date field's value as text: the identity of a reminder with its record and rule.

        Requirements: BR-SRR-0001.
        """
        return fields.Datetime.to_string(value) if isinstance(value, datetime) else fields.Date.to_string(value)

    def _lock_row(self) -> bool:
        """Lock the rule's row for this run; False when another run holds it (ADR-SRR-002).

        Requirements: FR-SRR-0005.
        """
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM record_reminder_rule WHERE id = %s FOR UPDATE SKIP LOCKED", [self.id])
        return bool(self.env.cr.fetchone())

    @api.model
    def _cron_send_reminders(self) -> None:
        """The scheduled action: send the due reminders of every active rule.

        A rule another run is processing is left to it. Progress is committed
        every ``COMMIT_EVERY`` reminders with the number of reminders left, so
        when the time of the job is used up the run stops and the scheduler
        runs the job again at once; a complete run reports nothing left.

        Requirements: FR-SRR-0005, FR-SRR-0013, FR-SRR-0016, NFR-SRR-0001.
        """
        now = fields.Datetime.now()
        for rule in self.search([]):
            if not rule._send_due(now):
                return
        if not modules.module.current_test:  # a test runs in one transaction that may not be committed
            self.env['ir.cron']._commit_progress(remaining=0)

    def _send_due(self, now: datetime) -> bool:
        """Send the due reminders of one rule; False when the job must stop (its time is used up).

        Requirements: FR-SRR-0005, FR-SRR-0013, NFR-SRR-0001.
        """
        self.ensure_one()
        started = time.monotonic()
        if not self._lock_row():
            return True
        try:
            with self.env.cr.savepoint():
                due_reminders = self._collect_due(now)
        except Exception:  # noqa: BLE001 - a rule that cannot be read must not stop the other rules (design, section 5)
            _logger.error("reminders: rule %s: collecting the due reminders failed", self.id, exc_info=True)
            return True
        sent = failed = 0
        for index, (record, reminder_key, reminder_date) in enumerate(due_reminders, 1):
            line = self._send_one(record, reminder_key, reminder_date)
            sent += line.state == 'sent'
            failed += line.state == 'failed'
            if index % COMMIT_EVERY or index == len(due_reminders) or modules.module.current_test:
                continue  # a test runs in one transaction that may not be committed
            time_left = self.env['ir.cron']._commit_progress(COMMIT_EVERY, remaining=len(due_reminders) - index)
            if not time_left:
                self._log_counts(sent, failed, started)
                _logger.info("reminders: rule %s: time is up after %s reminders, the job runs again for the rest",
                             self.id, index)
                return False
            if not self._lock_row():
                self._log_counts(sent, failed, started)
                return True
        self._log_counts(sent, failed, started)
        return True

    def _log_counts(self, sent: int, failed: int, started: float) -> None:
        """Log what one rule's run sent and failed (design, section 5)."""
        _logger.info("reminders: rule %s: %s sent, %s failed in %.1fs", self.id, sent, failed, time.monotonic() - started)
