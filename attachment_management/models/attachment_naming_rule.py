import mimetypes
import os
import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Date

PLACEHOLDERS = ('record_name', 'record_ref', 'date', 'uploader', 'original_name', 'seq')
PLACEHOLDER_PATTERN = re.compile(r'\{(\w*)\}')
FORBIDDEN_NAME_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
NAME_MAX_LENGTH = 200


class AttachmentNamingRule(models.Model):
    _name = 'attachment.naming.rule'
    _description = "Attachment Naming Pattern"
    _order = 'name'

    name = fields.Char(compute='_compute_name', store=True)
    model_id = fields.Many2one(
        'ir.model', string="Kind of Record", required=True, ondelete='cascade', index=True,
        domain=[('transient', '=', False), ('abstract', '=', False)])
    active = fields.Boolean(default=True)
    pattern = fields.Char(
        required=True,
        help="Fixed text and placeholders: {record_name}, {record_ref}, {date}, {uploader}, "
             "{original_name}, {seq}. Example: {record_ref}_{date} gives S00042_2026-11-10.pdf.")
    example_name = fields.Char(compute='_compute_example_name')
    sequence_id = fields.Many2one('ir.sequence', string="Running Number", copy=False, ondelete='restrict')

    @api.depends('model_id.name')
    def _compute_name(self):
        for rule in self:
            rule.name = rule.model_id.name or False

    @api.depends('pattern')
    def _compute_example_name(self):
        for rule in self:
            rule.example_name = rule._render(
                original_name='scan.pdf', record_name='Azure Interior', record_ref='AZ-001',
                date=fields.Date.context_today(rule), uploader=rule.env.user.name, seq='1')

    @api.constrains('model_id', 'active')
    def _check_one_active_rule_per_model(self):
        """Requirements: FR-AM-0011. One active pattern per kind of record, with a message."""
        for rule in self.filtered('active'):
            if self.search_count([('model_id', '=', rule.model_id.id), ('id', '!=', rule.id)]):
                raise ValidationError(self.env._("This kind of record already has an active naming pattern."))

    @api.constrains('pattern')
    def _check_pattern(self):
        """Requirements: FR-AM-0011. Only known placeholders, and a pattern that renders a name."""
        for rule in self:
            unknown = [name for name in PLACEHOLDER_PATTERN.findall(rule.pattern or '') if name not in PLACEHOLDERS]
            if unknown:
                raise ValidationError(self.env._(
                    "Unknown placeholder {%(name)s}. Use one of: %(allowed)s.",
                    name=unknown[0], allowed=', '.join('{%s}' % name for name in PLACEHOLDERS)))
            if not rule._render(
                    original_name='x', record_name='x', record_ref='x', date='x', uploader='x', seq='x'):
                raise ValidationError(self.env._("The pattern must produce a file name."))

    @api.constrains('model_id')
    def _check_model_kind(self):
        """Requirements: FR-AM-0011. Only kinds of record that store records can carry a pattern."""
        for rule in self:
            if rule.model_id.transient or rule.model_id.abstract:
                raise ValidationError(self.env._("Choose a kind of record that stores records."))

    @api.model_create_multi
    def create(self, vals_list):
        rules = super().create(vals_list)
        for rule in rules:
            rule.sequence_id = self.env['ir.sequence'].create({
                'name': self.env._("Attachment naming: %s", rule.model_id.name),
                'implementation': 'standard',
            })
        return rules

    def unlink(self):
        sequences = self.sequence_id
        result = super().unlink()
        sequences.unlink()
        return result

    def copy_data(self, default=None):
        # An archived copy keeps the unique active index satisfied.
        return super().copy_data(dict(default or {}, active=False))

    def _render(self, *, original_name, record_name, record_ref, date, uploader, seq):
        """Return the file name the pattern gives for the given values, with the original extension.

        Characters a file name cannot hold become an underscore; the result is
        cut at NAME_MAX_LENGTH characters. An empty result is returned as ''.
        """
        self.ensure_one()
        stem, extension = os.path.splitext(original_name)
        values = {
            'record_name': record_name, 'record_ref': record_ref, 'date': str(date),
            'uploader': uploader, 'original_name': stem, 'seq': str(seq),
        }
        rendered = PLACEHOLDER_PATTERN.sub(lambda match: values.get(match.group(1), ''), self.pattern or '')
        rendered = FORBIDDEN_NAME_CHARS.sub('_', rendered).strip()[:NAME_MAX_LENGTH].strip()
        return rendered + extension if rendered else ''

    @api.model
    def _apply_to(self, vals_list):
        """Requirements: FR-AM-0012, FR-AM-0013. Return the pattern name of each create values dict.

        The list answers each element in order: the rendered name, or None when
        the file is kept as uploaded (no record, field file, no active pattern or
        a pattern that renders nothing).
        """
        # sudo: ordinary users cannot read naming rules; only the rendered name leaves.
        rules = self.sudo().search([
            ('model_id.model', 'in', list({vals.get('res_model') for vals in vals_list if vals.get('res_model')}))])
        rule_of_model = {rule.model_id.model: rule for rule in rules}
        return [self._name_of(vals, rule_of_model.get(vals.get('res_model'))) for vals in vals_list]

    def _name_of(self, vals, rule):
        if not rule or vals.get('res_field') or not vals.get('res_id') or not vals.get('name'):
            return None
        # sudo: the upload route already checked the uploader's access to the record; the name only needs its display name.
        record = self.env[vals['res_model']].sudo().browse(vals['res_id']).exists()
        if not record:
            return None
        # sudo: ordinary users cannot read the rule's sequence; a number is only drawn when the pattern uses one.
        sequence = rule.sequence_id.sudo()
        name = rule._render(
            original_name=self._file_name(vals), record_name=record.display_name,
            record_ref=self._record_reference(record), date=Date.context_today(self),
            uploader=self.env.user.name, seq=sequence._next() if '{seq}' in rule.pattern else '')
        return name or None

    @api.model
    def _file_name(self, vals):
        """The uploaded name, with an extension taken from the mimetype when it has none."""
        name = vals['name']
        if os.path.splitext(name)[1] or not vals.get('mimetype'):
            return name
        return name + (mimetypes.guess_extension(vals['mimetype']) or '')

    @api.model
    def _record_reference(self, record):
        field = record._fields.get(record._rec_name)
        if field and field.type == 'char' and record[record._rec_name]:
            return record[record._rec_name]
        return self.env._("%(model)s %(id)s", model=record._description, id=record.id)
