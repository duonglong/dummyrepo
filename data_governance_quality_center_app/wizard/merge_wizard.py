from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError

from ..const import MERGE_MAX_RECORDS, MERGE_MIN_RECORDS
from ..services import merge


class DataQualityMergeWizard(models.TransientModel):
    """The preview of a merge: the record kept, the value of each field, the links that move.

    Nothing is changed by the preview; it only reads. The merge itself is confirmed from the
    same dialog by the merge task.

    Requirements: FR-DGQ-0042
    """
    _name = 'data.quality.merge.wizard'
    _description = 'Data Quality Merge Preview'

    group_id = fields.Many2one(
        'data.quality.duplicate.group', string='Group', ondelete='cascade',
        help="The duplicate group the records come from.")
    res_model = fields.Char(string='Model', required=True)
    res_ids = fields.Json(string='Records', help="The records to merge, 2 to 10.")
    kept_res_id = fields.Many2oneReference(
        string='Record Kept', model_field='res_model', compute='_compute_kept_res_id', store=True,
        readonly=False,
        help="The record that stays. By default the one with the most linked documents.")
    value_line_ids = fields.One2many(
        'data.quality.merge.wizard.value', 'wizard_id', string='Values',
        compute='_compute_value_lines', store=True, readonly=False)
    link_summary = fields.Json(
        compute='_compute_summary',
        help="The linked documents of each kind that move to the record kept.")
    archive_names = fields.Char(
        string='To Be Archived', compute='_compute_summary',
        help="The records that are archived once the merge is confirmed.")
    reversible = fields.Boolean(
        string='Can Be Rolled Back', compute='_compute_summary',
        help="Whether the merge can be undone while the merged records still exist.")
    irreversible_reason = fields.Char(string='Why Not', compute='_compute_summary')

    @api.constrains('res_ids')
    def _check_record_count(self):
        """A merge takes 2 to 10 records.

        Requirements: FR-DGQ-0042
        """
        for wizard in self:
            count = len(wizard.res_ids or [])
            if not MERGE_MIN_RECORDS <= count <= MERGE_MAX_RECORDS:
                raise ValidationError(self.env._(
                    "A merge takes %(min)s to %(max)s records, not %(count)s. "
                    "Set aside the records that are not duplicates first.",
                    min=MERGE_MIN_RECORDS, max=MERGE_MAX_RECORDS, count=count))

    @api.constrains('kept_res_id', 'value_line_ids')
    def _check_choices(self):
        """The record kept and the source of every value are among the records to merge."""
        for wizard in self:
            ids = set(wizard.res_ids or [])
            chosen = {wizard.kept_res_id, *wizard.value_line_ids.mapped('source_res_id')} - {0}
            if not chosen <= ids:
                raise ValidationError(self.env._("The record kept and the sources must be among the records to merge."))

    def _links_per_record(self):
        """Return the number of linked documents of each record: ``{id: count}``."""
        self.ensure_one()
        counts = merge.linked_counts(self.env, self.res_model, self.res_ids or [])
        return {res_id: sum(per_model.values()) for res_id, per_model in counts.items()}

    @api.depends('res_ids', 'res_model')
    def _compute_kept_res_id(self):
        """Keep the record with the most linked documents, the oldest on a tie, unless one is chosen.

        Requirements: FR-DGQ-0042
        """
        for wizard in self:
            ids = wizard.res_ids or []
            if not ids or wizard.kept_res_id in ids:
                continue
            links = wizard._links_per_record()
            wizard.kept_res_id = min(ids, key=lambda res_id: (-links.get(res_id, 0), res_id))

    @api.depends('kept_res_id', 'res_ids', 'group_id')
    def _compute_value_lines(self):
        """List the filled fields with the record each value comes from.

        The record kept keeps its value; a field it leaves empty is filled from the first other
        record that has one, and the manager may pick another source.

        Requirements: FR-DGQ-0042
        """
        for wizard in self:
            ids = wizard.res_ids or []
            if not ids or not wizard.kept_res_id:
                wizard.value_line_ids = [Command.clear()]
                continue
            rule_lines = wizard.group_id.sudo().rule_id._get_match_lines() if wizard.group_id else []
            others = [res_id for res_id in ids if res_id != wizard.kept_res_id]
            rows = merge.field_rows(self.env, wizard.res_model, [wizard.kept_res_id, *others], rule_lines)
            rows.sort(key=lambda row: (not row['is_match'], not row['differs']))
            lines = []
            for row in rows:
                if not row['writable']:
                    continue
                source = next(
                    (res_id for res_id in [wizard.kept_res_id, *others] if row['values'].get(res_id)),
                    wizard.kept_res_id)
                lines.append(Command.create({
                    'field_name': row['name'], 'field_label': row['label'], 'differs': row['differs'],
                    'source_res_id': source, 'kept_value': row['values'].get(wizard.kept_res_id, ''),
                }))
            wizard.value_line_ids = [Command.clear(), *lines]

    @api.depends('kept_res_id', 'res_ids', 'res_model')
    def _compute_summary(self):
        """Count the links that move, name the archived records and tell if the merge can be undone.

        Requirements: FR-DGQ-0042, BR-DGQ-0008
        """
        for wizard in self:
            ids = wizard.res_ids or []
            others = [res_id for res_id in ids if res_id != wizard.kept_res_id]
            wizard.link_summary = []
            wizard.archive_names = False
            wizard.reversible = True
            wizard.irreversible_reason = False
            if not ids or not wizard.res_model:
                continue
            # sudo: the manager may not read every merged model, the names are the records' own
            records = self.env[wizard.res_model].sudo().browse(others).exists()
            wizard.archive_names = ', '.join(records.mapped('display_name'))
            moving = {}
            for per_model in merge.linked_counts(self.env, wizard.res_model, others).values():
                for model_name, count in per_model.items():
                    moving[model_name] = moving.get(model_name, 0) + count
            wizard.link_summary = [
                {'model': model_name, 'label': self.env['ir.model'].sudo()._get(model_name).name, 'count': count}
                for model_name, count in sorted(moving.items())]
            reason = merge.irreversible_reason(self.env, wizard.res_model, wizard.kept_res_id, others)
            wizard.reversible = not reason
            wizard.irreversible_reason = reason


class DataQualityMergeWizardValue(models.TransientModel):
    """One field of the merge preview: the value it will have and the record it comes from.

    Requirements: FR-DGQ-0042
    """
    _name = 'data.quality.merge.wizard.value'
    _description = 'Data Quality Merge Preview Value'

    wizard_id = fields.Many2one(
        'data.quality.merge.wizard', string='Preview', required=True, ondelete='cascade')
    res_model = fields.Char(related='wizard_id.res_model')
    field_name = fields.Char(string='Technical Name', required=True)
    field_label = fields.Char(string='Field')
    kept_value = fields.Char(string='Value Now', help="The value the record kept has today.")
    source_res_id = fields.Many2oneReference(
        string='Value From', model_field='res_model',
        help="The record whose value the kept record will have.")
    value_display = fields.Char(string='Value After', compute='_compute_value_display')
    differs = fields.Boolean(string='Differs', help="The records do not all have the same value.")

    @api.depends('source_res_id', 'field_name', 'res_model')
    def _compute_value_display(self):
        """Show the value of the chosen source record.

        Requirements: FR-DGQ-0042
        """
        for line in self:
            # sudo: the value was read for the preview by the same rules as the comparison
            source = line.env[line.res_model].sudo().browse(line.source_res_id).exists() if line.res_model else False
            line.value_display = merge.display_value(source, line.field_name) if source and line.field_name else False
