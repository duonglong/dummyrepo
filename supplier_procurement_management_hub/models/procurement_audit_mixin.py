from odoo import api, models

from ..services import audit


class ProcurementAuditMixin(models.AbstractModel):
    """Writes changes of configuration records to the audit trail.

    Requirements: BR-SPM-0018
    A model inherits it and lists its audited fields in ``_spm_audited_fields``;
    ``_spm_audit_event`` is the kind of the entries it writes.
    """
    _name = 'procurement.audit.mixin'
    _description = 'Procurement Audit Mixin'

    _spm_audited_fields = ()
    _spm_audit_event = 'configuration'

    def _spm_audit_values(self):
        """Return the audited values of one record as plain, JSON-ready data."""
        self.ensure_one()
        values = {}
        for name in self._spm_audited_fields:
            value = self[name]
            field = self._fields[name]
            if field.type == 'many2one':
                value = value.id
            elif field.type in ('one2many', 'many2many'):
                value = value.ids
            elif field.type in ('date', 'datetime'):
                value = field.convert_to_export(value, self) or False
            values[name] = value
        return values

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: BR-SPM-0018"""
        records = super().create(vals_list)
        for record in records:
            audit.log(self.env, self._spm_audit_event, record,
                      {}, record._spm_audit_values(), 'created')
        return records

    def write(self, vals):
        """Requirements: BR-SPM-0018"""
        if not set(vals) & set(self._spm_audited_fields):
            return super().write(vals)
        before = {record: record._spm_audit_values() for record in self}
        result = super().write(vals)
        for record in self:
            after = record._spm_audit_values()
            changed = [name for name in after if after[name] != before[record][name]]
            if changed:
                audit.log(self.env, self._spm_audit_event, record,
                          {name: before[record][name] for name in changed},
                          {name: after[name] for name in changed}, 'changed')
        return result

    def unlink(self):
        """Requirements: BR-SPM-0018"""
        for record in self:
            audit.log(self.env, self._spm_audit_event, record,
                      record._spm_audit_values(), {}, 'deleted')
        return super().unlink()
