import re

from odoo import api, fields, models

SENSITIVE_NAME_WORDS = frozenset({'password', 'passwd', 'token', 'secret', 'key', 'signature'})
SENSITIVE_FIELD_TYPES = frozenset({'binary'})
BANK_ACCOUNT_FIELDS = {'res.partner.bank': {'account_number', 'sanitized_account_number'}}


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

    @api.model
    def _is_sensitive_by_default(self, field):
        """Tell whether an ir.model.fields record is sensitive without any mark.

        Secrets (password, token, secret, key, signature names), binary fields, bank account
        numbers and fields restricted to user groups.

        Requirements: BR-DGQ-0010
        """
        words = set(re.split(r'[^a-z0-9]+', field.name.lower()))
        return bool(
            words & SENSITIVE_NAME_WORDS
            or field.ttype in SENSITIVE_FIELD_TYPES
            or field.name in BANK_ACCOUNT_FIELDS.get(field.model, ())
            or field.groups
        )

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
