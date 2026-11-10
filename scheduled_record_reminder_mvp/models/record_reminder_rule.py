from odoo import Command, _, api, fields, models
from odoo.addons.base.models.res_partner import _tz_get
from odoo.exceptions import ValidationError
from odoo.fields import Domain
from odoo.tools import safe_eval

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
