import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

SENSITIVE_NAME_WORDS = frozenset({'password', 'passwd', 'token', 'secret', 'key', 'signature'})
SENSITIVE_FIELD_TYPES = frozenset({'binary'})
BANK_ACCOUNT_MODEL = 'res.partner.bank'
BANK_ACCOUNT_WORDS = frozenset({'account', 'acc'})


class DataQualityFieldConfig(models.Model):
    _name = 'data.quality.field.config'
    _description = 'Data Quality Field Mark'
    _rec_name = 'field_id'

    field_id = fields.Many2one(
        'ir.model.fields', string='Field', required=True, ondelete='cascade', index=True)
    model_id = fields.Many2one(
        related='field_id.model_id', string='Model', store=True, index=True)
    status = fields.Selection(
        [('excluded', 'Excluded'), ('sensitive', 'Sensitive'), ('allowed', 'Allowed')],
        string='Status', required=True, default='excluded',
        help="Excluded and sensitive fields are left out of rules, scans, profiles, exports and "
             "audit values. Allowed puts back a field that is sensitive by default.")

    _field_unique = models.Constraint(
        'UNIQUE(field_id)', 'A field can only carry one mark.')

    @api.constrains('field_id')
    def _check_field_is_stored(self):
        """Refuse a field that scans cannot read: of a model without a table, or not stored.

        Requirements: FR-DGQ-0075
        """
        for config in self:
            field = config.field_id
            model = self.env.registry.get(field.model)
            if model is None or not model._auto or model._transient or not field.store:
                raise ValidationError(_(
                    "%(field)s is not a stored field of a model with a table and cannot be marked.",
                    field=field.display_name))

    @api.model
    def _is_sensitive_by_default(self, field):
        """Tell whether an ir.model.fields record is sensitive without any mark.

        Secrets (password, token, secret, key, signature names), binary fields, bank account
        numbers (iban, account number names on any model) and links to bank accounts, and fields restricted to user groups (also those
        hidden from everyone).

        Requirements: BR-DGQ-0010
        """
        words = set(re.split(r'[^a-z0-9]+', field.name.lower()))
        is_bank_account_number = 'iban' in words or (words & BANK_ACCOUNT_WORDS and 'number' in words)
        return bool(
            words & SENSITIVE_NAME_WORDS
            or is_bank_account_number
            or field.ttype in SENSITIVE_FIELD_TYPES
            or field.relation == BANK_ACCOUNT_MODEL
            or field.groups
            or self._is_hidden_from_everyone(field)
        )

    @api.model
    def _is_hidden_from_everyone(self, field):
        """Tell whether the field is declared with no group allowed, which Odoo does not list.

        Requirements: BR-DGQ-0010
        """
        model = self.env.registry.get(field.model)
        python_field = model._fields.get(field.name) if model is not None else None
        return python_field is not None and python_field.groups == fields.NO_ACCESS

    @api.model
    def _get_usable_field_names(self, model_name):
        """Return the names of the stored fields of a model that scans and profiles may read.

        A field is left out when marked excluded or sensitive, or when it is sensitive by default
        and not marked allowed; a mark overrides the default list.

        Requirements: BR-DGQ-0010, FR-DGQ-0075
        """
        # sudo: scans read the marks and field list of any model whatever the caller may read
        fields_of_model = self.env['ir.model.fields'].sudo().search(
            [('model', '=', model_name), ('store', '=', True)])
        # sudo: same reason, the marks are an administrator setting
        mark_records = self.sudo().search([('model_id.model', '=', model_name)])
        marks = {mark.field_id.id: mark.status for mark in mark_records}
        usable = set()
        for field in fields_of_model:
            mark = marks.get(field.id)
            if mark == 'allowed' or (mark is None and not self._is_sensitive_by_default(field)):
                usable.add(field.name)
        return usable
