from odoo import api, fields, models


class DuplicateGroupLine(models.Model):
    _name = 'duplicate.group.line'
    _description = 'Record of a Set of Possible Duplicates'

    group_id = fields.Many2one(
        'duplicate.group', required=True, index=True, ondelete='cascade')
    res_model = fields.Char(required=True)
    res_id = fields.Many2oneReference(model_field='res_model', required=True, index=True)
    name = fields.Char(help="Name of the record when it was found.")
    company_id = fields.Many2one('res.company', ondelete='set null')
    matched_values = fields.Char(compute='_compute_matched_values')
    record_create_date = fields.Datetime(compute='_compute_record_state')
    record_active = fields.Boolean(compute='_compute_record_state')

    _group_record_uniq = models.Constraint(
        'unique(group_id, res_id)', "A record can only appear once in a set.")

    @api.depends('group_id.rule_id.condition_ids', 'res_model', 'res_id')
    def _compute_matched_values(self) -> None:
        """Requirements: FR-DRD-0004 (show the values the rule compares, side by side)."""
        for line in self:
            # sudo: a manager may review sets of records of a company they cannot open
            record = line.env[line.res_model].sudo().with_context(active_test=False).browse(line.res_id)
            if not record.exists():
                line.matched_values = ''
                continue
            line.matched_values = ' · '.join(
                f"{c.field_id.field_description}: {self._display_value(record, c)}"
                for c in line.group_id.rule_id.condition_ids)

    @staticmethod
    def _display_value(record: models.BaseModel, condition: models.BaseModel) -> str:
        """Return the readable value of a rule's field on a record."""
        value = record[condition.field_id.name]
        return value.display_name if condition.field_type == 'many2one' else value or ''

    @api.depends('res_model', 'res_id')
    def _compute_record_state(self) -> None:
        """Requirements: FR-DRD-0004 (show whether the record is still active and when it was created)."""
        for line in self:
            # sudo: same reason as above
            record = line.env[line.res_model].sudo().with_context(active_test=False).browse(line.res_id)
            line.record_create_date = record.create_date if record.exists() else False
            line.record_active = record.active if record.exists() else False
