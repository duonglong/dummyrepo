from odoo import Command, _, api, fields, models, tools
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL

from ..models.duplicate_rule import WATCHED_MODELS

MERGE_MIN_RECORDS = 2
MERGE_MAX_RECORDS = 10

PREVIEW_FIELD_TYPES = (
    'char', 'text', 'html', 'selection', 'many2one', 'date', 'datetime', 'integer', 'float', 'monetary')
BOOKKEEPING_FIELDS = ('id', 'display_name', 'active', 'create_uid', 'create_date', 'write_uid', 'write_date')
MANAGER_GROUPS = ('new_duplicate_record_detector.group_duplicate_manager', 'base.group_system')


class DuplicateMergeWizard(models.TransientModel):
    _name = 'duplicate.merge.wizard'
    _description = 'Merge Preview'

    group_id = fields.Many2one(
        'duplicate.group', string='Set', ondelete='cascade',
        help="The set of duplicates the merge was started from. Empty when started from a list selection.")
    res_model = fields.Char(string='Record type', required=True)
    record_line_ids = fields.One2many(
        'duplicate.merge.record.line', 'wizard_id', string='Records')
    kept_res_id = fields.Many2oneReference(
        model_field='res_model', string='Kept record', required=True,
        help="The record that stays. The others are archived and their links move to it.")
    value_line_ids = fields.One2many(
        'duplicate.merge.value.line', 'wizard_id', string='Values after the merge',
        compute='_compute_preview', store=True)
    link_line_ids = fields.One2many(
        'duplicate.merge.link.line', 'wizard_id', string='Linked documents that move',
        compute='_compute_preview', store=True)
    archive_names = fields.Char(string='Archived', compute='_compute_archive_names')

    # ------------------------------------------------------------------
    # access and start
    # ------------------------------------------------------------------

    def _check_merge_rights(self) -> None:
        """Requirements: FR-DRD-0017.

        Refuse anyone who is neither a duplicate manager nor an administrator.
        """
        user = self.env.user
        if not any(user.has_group(group) for group in MANAGER_GROUPS):
            raise AccessError(_("Only duplicate managers and administrators can merge records."))

    @api.model
    def default_get(self, fields_list: list[str]) -> dict:
        """Requirements: FR-DRD-0009, FR-DRD-0017, BR-DRD-0006.

        Load the records of the set (``active_model='duplicate.group'``) or of the list selection
        with the user's own access, and choose the oldest as the kept record.
        """
        self._check_merge_rights()
        values = super().default_get(fields_list)
        context = self.env.context
        active_model = context.get('active_model')
        if active_model == 'duplicate.group':
            group = self.env['duplicate.group'].browse(context.get('active_id')).exists()
            res_model, res_ids = group.model_name, self._group_res_ids(group)
        else:
            res_model, res_ids = active_model, context.get('active_ids') or []
        if res_model not in WATCHED_MODELS:
            raise UserError(_("A merge works on contacts or product templates only."))
        records = self._ordered(self.env[res_model].browse(res_ids).exists()._filtered_access('read'))
        self._check_size(len(records))
        values.update({
            'group_id': group.id if active_model == 'duplicate.group' else False,
            'res_model': res_model,
            'kept_res_id': records[0].id,
            'record_line_ids': [Command.create({
                'res_id': record.id, 'name': record.display_name,
                'record_create_date': record.create_date, 'include': True,
            }) for record in records],
        })
        return values

    @api.model
    def _group_res_ids(self, group: models.BaseModel) -> list[int]:
        """Return the records of a set; a set that mixes record types is refused."""
        if set(group.line_ids.mapped('res_model')) - {group.model_name}:
            raise UserError(_("A merge works on one record type only."))
        return group.line_ids.mapped('res_id')

    @api.model
    def _ordered(self, records: models.BaseModel) -> models.BaseModel:
        """Return the records oldest first (creation date, then id)."""
        return records.sorted(lambda record: (record.create_date, record.id))

    @api.model
    def _check_size(self, count: int) -> None:
        """Requirements: BR-DRD-0006."""
        if not MERGE_MIN_RECORDS <= count <= MERGE_MAX_RECORDS:
            raise UserError(_(
                "A merge takes %(min)s to %(max)s records, not %(count)s.",
                min=MERGE_MIN_RECORDS, max=MERGE_MAX_RECORDS, count=count))

    @api.model_create_multi
    def create(self, vals_list: list[dict]) -> models.BaseModel:
        """Requirements: FR-DRD-0017 (a user outside the manager groups cannot start a merge)."""
        self._check_merge_rights()
        return super().create(vals_list)

    def write(self, vals: dict) -> bool:
        """Requirements: FR-DRD-0017 (every change of the dialog is checked)."""
        self._check_merge_rights()
        return super().write(vals)

    @api.constrains('record_line_ids', 'kept_res_id', 'res_model')
    def _constrain_selection(self) -> None:
        """Requirements: BR-DRD-0006, FR-DRD-0009."""
        for wizard in self:
            wizard._check_selection()

    def _check_selection(self) -> None:
        """Requirements: BR-DRD-0006.

        The included records number 2 to 10, are of one allowed type and hold the kept record.
        """
        self.ensure_one()
        if self.res_model not in WATCHED_MODELS:
            raise ValidationError(_("A merge works on contacts or product templates only."))
        included = self.record_line_ids.filtered('include')
        self._check_size(len(included))
        if self.kept_res_id not in included.mapped('res_id'):
            raise UserError(_("The kept record must be one of the records that take part in the merge."))

    # ------------------------------------------------------------------
    # preview
    # ------------------------------------------------------------------

    def _included_records(self) -> models.BaseModel:
        """Return the records that take part in the merge, oldest first."""
        self.ensure_one()
        ids = self.record_line_ids.filtered('include').mapped('res_id')
        return self._ordered(self.env[self.res_model].browse(ids).exists())

    @api.depends('res_model', 'kept_res_id', 'record_line_ids.include', 'record_line_ids.res_id')
    def _compute_preview(self) -> None:
        """Requirements: FR-DRD-0009 (the preview follows the kept record and the included records)."""
        for wizard in self:
            kept, others = wizard._kept_and_others()
            wizard.value_line_ids = [Command.clear()] + [
                Command.create(vals) for vals in wizard._value_vals(kept, others)]
            wizard.link_line_ids = [Command.clear()] + [
                Command.create(vals) for vals in wizard._link_vals(others)]

    def _kept_and_others(self) -> tuple[models.BaseModel, models.BaseModel]:
        """Return the kept record and the other included records (both empty without a valid type)."""
        self.ensure_one()
        if self.res_model not in WATCHED_MODELS:
            return self.env['res.partner'], self.env['res.partner']
        records = self._included_records()
        kept = records.filtered(lambda record: record.id == self.kept_res_id)
        return kept, records - kept

    @api.depends('res_model', 'kept_res_id', 'record_line_ids.include', 'record_line_ids.res_id')
    def _compute_archive_names(self) -> None:
        """Requirements: FR-DRD-0009 (the records that will be archived)."""
        for wizard in self:
            wizard.archive_names = ', '.join(wizard._kept_and_others()[1].mapped('display_name'))

    def _value_vals(self, kept: models.BaseModel, others: models.BaseModel) -> list[dict]:
        """One line per field that has a value: what the kept record will hold and where it comes from."""
        vals_list = []
        if not kept:
            return vals_list
        for field in self._preview_fields():
            source = kept if kept[field.name] else next(
                (other for other in others if other[field.name]), None)
            if source is None:
                continue
            vals_list.append({
                'field_name': field.name,
                'field_label': field.get_description(self.env)['string'],
                'kept_value': self._readable_value(field, source),
                'source_name': source.display_name,
                'is_filled': source is not kept,
                'write_value': self._raw_value(field, source),
            })
        return vals_list

    def _preview_fields(self) -> list:
        """Return the stored, editable fields of the record type that the preview compares."""
        return [
            field for field in self.env[self.res_model]._fields.values()
            if field.store and not field.compute and not field.readonly
            and field.type in PREVIEW_FIELD_TYPES and field.name not in BOOKKEEPING_FIELDS]

    @api.model
    def _readable_value(self, field, record: models.BaseModel) -> str:
        """Return the value of the field as a person reads it."""
        value = record[field.name]
        if field.type == 'selection':
            return dict(field._description_selection(self.env)).get(value, value)
        if field.type in ('many2one', 'date', 'datetime'):
            return field.convert_to_export(value, record) or str(value)
        return str(value)

    @api.model
    def _raw_value(self, field, record: models.BaseModel):
        """Return the value of the field as it is written on the kept record."""
        value = record[field.name]
        return value.id if field.type == 'many2one' else value

    def _link_vals(self, others: models.BaseModel) -> list[dict]:
        """One line per kind of linked document: how many move off the records to be archived."""
        linked = self._linked_ids(self.res_model, others.ids)
        labels = {name: self.env['ir.model']._get(name).name for name in linked}
        return [{'model_label': labels[name], 'count': len(ids)}
                for name, ids in sorted(linked.items(), key=lambda item: labels[item[0]]) if ids]

    @api.model
    def _linked_ids(self, res_model: str, res_ids: list[int]) -> dict[str, set[int]]:
        """Requirements: FR-DRD-0009.

        Return, per model, the ids of the documents that point to one of the records through a
        many2one, many2many, reference or model/id field: the links a merge moves.
        """
        linked: dict[str, set[int]] = {}
        if not res_ids:
            return linked
        for model_name, query in self._link_queries(res_model):
            self.env.cr.execute(query(tuple(res_ids)))
            ids = {row[0] for row in self.env.cr.fetchall()}
            if ids:
                linked.setdefault(model_name, set()).update(ids)
        return linked

    @api.model
    @tools.ormcache('res_model')
    def _link_queries(self, res_model: str) -> list:
        """Return (model, query builder) pairs, one per field that can point to the record type."""
        queries = []
        for model_name in self.env.registry:
            model = self.env[model_name]
            if model._abstract or model._transient or not model._auto or model_name.startswith('duplicate.'):
                continue
            for field in model._fields.values():
                query = self._link_query(model, field, res_model)
                if query:
                    queries.append((model_name, query))
        return queries

    @api.model
    def _link_query(self, model: models.BaseModel, field, res_model: str):
        """Return a function that builds the query of the documents linked through the field, or None."""
        if not field.store or (field.compute and not field.inverse):
            return None
        table = SQL.identifier(model._table)
        if field.type == 'many2one' and field.comodel_name == res_model:
            return lambda ids: SQL("SELECT id FROM %s WHERE %s IN %s", table, SQL.identifier(field.name), ids)
        if field.type == 'many2many' and field.comodel_name == res_model:
            return lambda ids: SQL(
                "SELECT %s FROM %s WHERE %s IN %s", SQL.identifier(field.column1),
                SQL.identifier(field.relation), SQL.identifier(field.column2), ids)
        if field.type == 'reference':
            return lambda ids: SQL(
                "SELECT id FROM %s WHERE %s IN %s", table, SQL.identifier(field.name),
                tuple(f'{res_model},{res_id}' for res_id in ids))
        if field.type == 'many2one_reference' and model._fields[field.model_field].store:
            return lambda ids: SQL(
                "SELECT id FROM %s WHERE %s = %s AND %s IN %s", table,
                SQL.identifier(field.model_field), res_model, SQL.identifier(field.name), ids)
        return None
