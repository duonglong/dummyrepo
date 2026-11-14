import logging

from psycopg2 import IntegrityError

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..const import MERGE_MAX_RECORDS, MERGE_MIN_RECORDS, PROTECTED_MODELS
from ..services import merge

_logger = logging.getLogger(__name__)

ADMIN_GROUP = 'data_governance_quality_center_app.group_dgq_admin'


class DataQualityMergeWizard(models.TransientModel):
    """The preview of a merge: the record kept, the value of each field, the links that move.

    Nothing is changed by the preview; it only reads. Confirming the dialog does the merge.

    Requirements: FR-DGQ-0042, FR-DGQ-0043, FR-DGQ-0044
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
    links_to_move = fields.Text(
        string='Linked Documents To Move', compute='_compute_summary',
        help="One line per kind of linked document with the number that moves to the record kept.")
    archive_names = fields.Char(
        string='To Be Archived', compute='_compute_summary',
        help="The records that are archived once the merge is confirmed.")
    reversible = fields.Boolean(
        string='Can Be Rolled Back', compute='_compute_summary',
        help="Whether the merge can be undone while the merged records still exist.")
    irreversible_reason = fields.Char(string='Why Not', compute='_compute_summary')
    preview_signature = fields.Char(
        help="The state of the records when the preview was saved; a record changed since refuses the merge.")
    protected_authorized = fields.Boolean(
        string='Authorize Protected Records',
        help="An administrator's explicit authorization to merge records of a protected model.")
    is_protected = fields.Boolean(compute='_compute_is_protected')

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

    def _trusted_ids(self):
        """Return the ids among ``res_ids`` that the preview may read, in the order given.

        With a group the model and the records are the group's own, read as the user; without one
        the model must be a real model and every record readable by the user. Anything else is
        left out, so a forged model or id is never read.
        """
        self.ensure_one()
        ids = list(self.res_ids or [])
        if self.group_id:
            if self.res_model != self.group_id.res_model:
                return []
            allowed = set(self.group_id.line_ids.mapped('res_id'))
            return [res_id for res_id in ids if res_id in allowed]
        if self.res_model not in self.env:
            return []
        records = self.env[self.res_model].browse(ids)
        try:
            records.check_access('read')
        except AccessError:
            return []
        return ids

    @api.constrains('res_model', 'res_ids', 'group_id')
    def _check_records_are_readable(self):
        """The records to merge are those of the group, or records the user may read.

        Records of another company than the readable ones are named as such, which is what a
        manager needs to know; the others are refused without a hint of what they are.

        Requirements: FR-DGQ-0042, FR-DGQ-0044, BR-DGQ-0010
        """
        for wizard in self:
            wizard._check_all_trusted()

    def _check_all_trusted(self):
        """Refuse a preview whose records the user may not merge: the other company or not readable."""
        self.ensure_one()
        if len(self._trusted_ids()) == len(self.res_ids or []):
            return
        if not self.group_id and self.res_model in self.env:
            # sudo: only the company of the records is read, to tell why they are refused
            records = self.env[self.res_model].sudo().browse(self.res_ids or []).exists()
            if 'company_id' in records._fields and len(set(records.company_id.ids)) > 1:
                raise ValidationError(self.env._("Records of different companies cannot be merged."))
        raise ValidationError(self.env._("These records cannot be merged from this group."))

    @api.constrains('kept_res_id', 'value_line_ids')
    def _check_choices(self):
        """The record kept and the source of every value are among the records to merge; a value
        line names a field the comparison may show.

        Requirements: FR-DGQ-0042, BR-DGQ-0010
        """
        for wizard in self:
            ids = set(wizard.res_ids or [])
            chosen = {wizard.kept_res_id, *wizard.value_line_ids.mapped('source_res_id')} - {0}
            if not chosen <= ids:
                raise ValidationError(self.env._("The record kept and the sources must be among the records to merge."))
            if wizard.value_line_ids and wizard.res_model:
                allowed = set(merge.comparable_fields(self.env, wizard.res_model))
                if not set(wizard.value_line_ids.mapped('field_name')) <= allowed:
                    raise ValidationError(self.env._("A value line names a field that cannot be compared."))

    def _links_per_record(self):
        """Return the number of linked documents of each record: ``{id: count}``."""
        self.ensure_one()
        counts = merge.linked_counts(self.env, self.res_model, self._trusted_ids())
        return {res_id: sum(per_model.values()) for res_id, per_model in counts.items()}

    @api.depends('res_ids', 'res_model')
    def _compute_kept_res_id(self):
        """Keep the record with the most linked documents, the oldest on a tie, unless one is chosen.

        Requirements: FR-DGQ-0042
        """
        for wizard in self:
            ids = wizard._trusted_ids()
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
            ids = wizard._trusted_ids()
            if not ids or wizard.kept_res_id not in ids:
                wizard.value_line_ids = [Command.clear()]
                continue
            # sudo: managers read the rule of their group; the preview only needs its fields
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

        A merge strategy may declare its model irreversible.

        Requirements: FR-DGQ-0042, BR-DGQ-0008
        """
        for wizard in self:
            ids = wizard._trusted_ids()
            others = [res_id for res_id in ids if res_id != wizard.kept_res_id]
            wizard.link_summary = []
            wizard.links_to_move = False
            wizard.archive_names = False
            wizard.reversible = True
            wizard.irreversible_reason = False
            if not ids or not wizard.res_model:
                continue
            records = self.env[wizard.res_model].browse(others).exists()
            wizard.archive_names = ', '.join(records.mapped('display_name'))
            moving = {}
            for per_model in merge.linked_counts(self.env, wizard.res_model, others).values():
                for model_name, count in per_model.items():
                    moving[model_name] = moving.get(model_name, 0) + count
            # sudo: the manager may not read ir.model, the label is the model's own name
            labels = {name: self.env['ir.model'].sudo()._get(name).name for name in moving}
            summary = [
                {'model': model_name, 'label': labels[model_name], 'count': count}
                for model_name, count in sorted(moving.items())]
            wizard.link_summary = summary
            wizard.links_to_move = '\n'.join('%s: %s' % (entry['label'], entry['count']) for entry in summary)
            reason = merge.irreversible_reason(self.env, wizard.res_model, wizard.kept_res_id, others)
            strategy = merge.strategy_of(self.env, wizard.res_model)
            if not reason and strategy and strategy.get('reversible') is False:
                reason = self.env._("Merges of this model cannot be undone.")
            wizard.reversible = not reason
            wizard.irreversible_reason = reason

    @api.model_create_multi
    def create(self, vals_list):
        """Take the state of the records when the preview is saved, unless the preview already took it.

        Requirements: FR-DGQ-0044
        """
        wizards = super().create(vals_list)
        for wizard in wizards.filtered(lambda wizard: not wizard.preview_signature):
            ids = wizard._trusted_ids()
            wizard.preview_signature = merge.preview_signature(self.env, wizard.res_model, ids) if ids else False
        return wizards

    def _protected(self):
        """Whether the model of the records is protected (BR-DGQ-0005)."""
        self.ensure_one()
        return self.res_model in PROTECTED_MODELS or (
            self.env['data.quality.model.config']._get_status(self.res_model) == 'restricted')

    @api.depends('res_model')
    def _compute_is_protected(self):
        """Tell the dialog whether the authorization is asked for.

        Requirements: FR-DGQ-0044
        """
        for wizard in self:
            wizard.is_protected = bool(wizard.res_model) and wizard._protected()

    def _refusal(self, records):
        """Return why the merge of the records is refused, or None (FR-DGQ-0044)."""
        self.ensure_one()
        if 'active' not in records._fields:
            return self.env._("Records of this model cannot be archived, so they cannot be merged.")
        if 'company_id' in records._fields and len({record.company_id.id for record in records}) > 1:
            return self.env._("Records of different companies cannot be merged.")
        if self._protected() and not (self.protected_authorized and self.env.user.has_group(ADMIN_GROUP)):
            return self.env._(
                "%s is a protected model: an administrator must authorize the merge.", self.res_model)
        strategy = merge.strategy_of(self.env, self.res_model)
        return strategy['handler'](self.env, records) if strategy else None

    def _check_unchanged(self, ids):
        """Refuse when another transaction holds a record or a record changed since the preview."""
        self.ensure_one()
        message = self.env._("These records changed since the preview was opened. Open the preview again.")
        if not merge.lock_records(self.env, self.res_model, ids):
            raise UserError(message)
        if not self.preview_signature or merge.preview_signature(
                self.env, self.res_model, ids) != self.preview_signature:
            raise UserError(message)

    def action_confirm(self):
        """Merge the records: values written on the kept one, every link moved, the others archived.

        All in one transaction: when a step fails nothing is changed and the reason is shown.

        Requirements: FR-DGQ-0043, FR-DGQ-0044, BR-DGQ-0008
        """
        self.ensure_one()
        self.env['data.quality.duplicate.group']._check_manager()
        if self.group_id:
            self.group_id._check_open()
        self._check_all_trusted()
        ids = self._trusted_ids()
        if self.kept_res_id not in ids:
            raise UserError(self.env._("The record kept must be among the records to merge."))
        self._check_unchanged(ids)
        records = self.env[self.res_model].browse(ids)
        reason = self._refusal(records)
        if reason:
            raise UserError(reason)
        others = [res_id for res_id in ids if res_id != self.kept_res_id]
        reason = merge.irreversible_reason(self.env, self.res_model, self.kept_res_id, others)
        if reason:
            raise UserError(self.env._("The links cannot be moved. %s", reason))
        try:
            with self.env.cr.savepoint():
                history = self._merge(records, others)
        except IntegrityError as error:
            raise UserError(self.env._(
                "A linked document could not be moved to the record kept: %s", error.diag.table_name or error)
            ) from error
        except merge.UnrecordableLinks as error:
            raise UserError(self.env._(
                "The links in %s cannot be recorded, so they cannot be moved.", error)) from error
        return {
            'type': 'ir.actions.act_window', 'res_model': history._name, 'res_id': history.id,
            'view_mode': 'form', 'views': [(False, 'form')],
        }

    def _merge(self, records, others):
        """Do the merge and return its history entry."""
        self.ensure_one()
        kept = records.browse(self.kept_res_id)
        sources = {record.id: record for record in records}
        before, written = {}, {}
        for line in self.value_line_ids.filtered(lambda line: line.source_res_id != kept.id):
            name = line.field_name
            new = merge.field_values(sources[line.source_res_id], [name])[name]
            old = merge.field_values(kept, [name])[name]
            if new != old:
                before[name], written[name] = old, new
        merged_records = records.browse(others)
        names = ', '.join(merged_records.mapped('display_name'))
        moved = merge.move_links(self.env, self.res_model, kept.id, others)
        kept.sudo().write(written)  # sudo: the app writes; the manager decided and may not edit
        merged_records.sudo().write({'active': False})  # sudo: the app archives; the manager decided
        strategy = merge.strategy_of(self.env, self.res_model)
        reversible = not (strategy and strategy.get('reversible') is False)
        history = self.env['data.quality.merge'].sudo().create({  # sudo: the app writes its own history
            'name': '%s → %s' % (names, kept.display_name), 'res_model': self.res_model,
            'company_id': kept.company_id.id if 'company_id' in kept._fields else False,
            'group_id': self.group_id.id, 'kept_res_id': kept.id, 'merged_res_ids': others,
            'merged_names': names, 'values_before': before, 'values_written': written,
            'moved_links': moved, 'user_id': self.env.user.id, 'date': fields.Datetime.now(),
            'reversible': reversible,
            'irreversible_reason': False if reversible else self.env._("Merges of this model cannot be undone."),
        })
        if self.group_id:
            self.group_id.sudo().write({'state': 'merged', 'merge_id': history.id})  # sudo: the app closes its own group
        _logger.info("dgq merge %s: %s into %s by uid %s", self.res_model, others, kept.id, self.env.uid)
        return history


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
            wizard = line.wizard_id
            trusted = line.res_model and line.source_res_id in wizard._trusted_ids() and (
                line.field_name in merge.comparable_fields(line.env, line.res_model))
            source = line.env[line.res_model].browse(line.source_res_id).exists() if trusted else False
            line.value_display = merge.display_value(source, line.field_name) if source else False
