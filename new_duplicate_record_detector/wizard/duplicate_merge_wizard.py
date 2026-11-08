import logging

from markupsafe import escape
from psycopg2 import IntegrityError, OperationalError

from odoo import Command, _, api, fields, models, tools
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL, mute_logger
from odoo.tools.sql import table_columns

from ..models.duplicate_rule import WATCHED_MODELS

_logger = logging.getLogger(__name__)

MERGE_MIN_RECORDS = 2
MERGE_MAX_RECORDS = 10

PREVIEW_FIELD_TYPES = (
    'char', 'text', 'html', 'selection', 'many2one', 'date', 'datetime', 'integer', 'float', 'monetary')
BOOKKEEPING_FIELDS = ('id', 'display_name', 'active', 'create_uid', 'create_date', 'write_uid', 'write_date')
# Fields of a record type that are computed from its variant (so skipped by the generic rule) but
# that a person fills in on the form: they take part in the preview and the fill.
PREVIEW_EXTRA_FIELDS = {'product.template': ('barcode', 'default_code')}
# Tables that describe the variants of a template: they stay with the template they belong to.
VARIANT_STRUCTURE_TABLES = (
    'product_product', 'product_template_attribute_line', 'product_template_attribute_value',
    'product_template_attribute_exclusion')
# Tables that describe the combination of a variant: they stay with the variant.
VARIANT_COMBINATION_TABLES = ('product_variant_combination',)
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
        wizards = super().create(vals_list)
        wizards.flush_recordset(['value_line_ids', 'link_line_ids'])  # compute the preview now
        return wizards

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
        model = self.env[self.res_model]
        extra = PREVIEW_EXTRA_FIELDS.get(self.res_model, ())
        return [
            field for field in model._fields.values()
            if field.name in extra or (
                field.store and not field.compute and not field.readonly
                and field.type in PREVIEW_FIELD_TYPES and field.name not in BOOKKEEPING_FIELDS)]

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
        self.env.flush_all()  # the queries below read the database, not the cache
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

    # ------------------------------------------------------------------
    # confirm
    # ------------------------------------------------------------------

    def action_merge(self) -> dict:
        """Requirements: FR-DRD-0010, FR-DRD-0011, FR-DRD-0012, FR-DRD-0013, FR-DRD-0014, BR-DRD-0007.

        Merge the included records into the kept one in a single savepoint: any failure undoes
        every step and the reason is shown to the manager.
        """
        self.ensure_one()
        self._check_merge_rights()
        self._check_selection()
        filled_values = self._checked_filled_values()
        try:
            with self.env.cr.savepoint():
                # sudo: the manager group and the read access to every record are checked above;
                # the merge edits records the manager may not edit
                self.sudo()._merge_records(filled_values)
        except (UserError, OperationalError):
            raise
        except Exception as error:
            _logger.exception("Merge of %s %s failed", self.res_model, self.record_line_ids.mapped('res_id'))
            raise UserError(_("The merge failed and nothing was changed: %(reason)s", reason=error)) from error
        return {'type': 'ir.actions.act_window_close'}

    def _checked_filled_values(self) -> dict:
        """Requirements: FR-DRD-0012, FR-DRD-0017.

        With the user's own access, refuse records the user cannot read and rebuild the values that
        fill empty fields from the records themselves, never from the stored preview lines (a
        manager can write those lines directly).
        """
        ids = self.record_line_ids.filtered('include').mapped('res_id')
        records = self.env[self.res_model].browse(ids).exists()
        if records._filtered_access('read') != records:
            raise UserError(_("You cannot merge records you are not allowed to read."))
        wizard = self.sudo()  # sudo: only reads records the user was just checked to read, with every field
        kept, others = wizard._kept_and_others()
        return {vals['field_name']: vals['write_value']
                for vals in wizard._value_vals(kept, others) if vals['is_filled']}

    def _merge_records(self, filled_values: dict) -> None:
        """Run the steps of a merge of contacts or product templates; the caller holds the savepoint and the checks."""
        included_ids = self.record_line_ids.filtered('include').mapped('res_id')
        self._lock_and_recheck(included_ids)
        records = self.env[self.res_model].with_context(active_test=False).browse(included_ids)
        kept = records.filtered(lambda record: record.id == self.kept_res_id)
        merged_away = records - kept
        if self.res_model == 'res.partner':
            self._check_contact_safety(records)
        else:
            self._check_product_safety(records)
        merged_names = merged_away.mapped('display_name')
        if self.res_model == 'res.partner':
            self._move_contact_links(merged_away, kept)
        else:
            self._move_product_links(merged_away, kept)
        merged_away.write({'active': False})
        if 'barcode' in filled_values:
            self._release_barcode(merged_away, filled_values['barcode'])
        self._fill_empty_fields(kept, filled_values)
        self._mark_group_merged(kept, set(included_ids))
        self._post_merge_note(kept, merged_names)
        self.env.flush_all()

    def _lock_and_recheck(self, res_ids: list[int]) -> None:
        """Requirements: FR-DRD-0010.

        Lock the records and refuse the merge when one is gone or archived since the preview.
        """
        self.env.flush_all()
        self.env.cr.execute(SQL(
            "SELECT id FROM %s WHERE id IN %s AND active FOR UPDATE",
            SQL.identifier(self.env[self.res_model]._table), tuple(res_ids)))
        if len(self.env.cr.fetchall()) != len(res_ids):
            raise UserError(_("A record of this merge was archived, deleted or merged by someone else: "
                              "open the preview again."))

    def _check_contact_safety(self, partners: models.BaseModel) -> None:
        """Requirements: FR-DRD-0015.

        Refuse a company merged with its own contact and contacts of more than one user.
        """
        for partner in partners:
            descendants = partner.search([('id', 'child_of', partner.id)]) - partner
            if partners & descendants:
                raise UserError(_("A company cannot be merged with its own contact."))
        if len(partners.user_ids) > 1:
            raise UserError(_("Contacts of more than one user cannot be merged, even if only one is active."))

    def _move_contact_links(self, merged_away: models.BaseModel, kept: models.BaseModel) -> None:
        """Requirements: FR-DRD-0011.

        Move every link to the kept contact with the native contact merge steps; a link the kept
        contact already has (a shared tag) stays once.
        """
        native = self.env['base.partner.merge.automatic.wizard']
        native._merge_bank_accounts(merged_away, kept)
        native._update_foreign_keys(merged_away, kept)
        native._update_reference_fields(merged_away, kept)

    def _check_product_safety(self, templates: models.BaseModel) -> None:
        """Requirements: FR-DRD-0016.

        Refuse a product with several variants and products sold in different units of measure.
        """
        if any(len(template.product_variant_ids.filtered('active')) > 1 for template in templates):
            raise UserError(_("A product with several variants cannot be merged."))
        if len(templates.uom_id) > 1:
            raise UserError(_("Products with different units of measure cannot be merged."))

    def _move_product_links(self, merged_away: models.BaseModel, kept: models.BaseModel) -> None:
        """Requirements: FR-DRD-0011.

        Move the links of the merged-away templates to the kept template and the links of their
        variant to the variant of the kept template; the variants themselves stay where they are.
        The links of archived merged-away variants move too, so the kept product holds the whole
        history (old orders, stock moves) of the products merged into it.
        """
        kept_variant = kept.product_variant_ids.filtered('active')
        if len(kept_variant) != 1:
            raise UserError(_("The kept product must have exactly one variant."))
        gone_variants = merged_away.product_variant_ids
        native = self.env['base.partner.merge.automatic.wizard']
        self.env.flush_all()  # the moves below run on the database
        for src_records, dst_record, skip in (
                (merged_away, kept, VARIANT_STRUCTURE_TABLES), (gone_variants, kept_variant, VARIANT_COMBINATION_TABLES)):
            self._repoint_foreign_keys(src_records, dst_record, skip)
            native._update_reference_fields_generic(src_records._name, src_records, dst_record)
        self.env.invalidate_all()

    def _release_barcode(self, merged_away: models.BaseModel, barcode: str) -> None:
        """Requirements: FR-DRD-0012.

        Free the barcode that moves to the kept product (a barcode is unique); the barcodes of
        the other merged-away products stay as they are.
        """
        merged_away.product_variant_ids.filtered(lambda variant: variant.barcode == barcode).write(
            {'barcode': False})

    def _repoint_foreign_keys(self, src_records: models.BaseModel, dst_record: models.BaseModel,
                              skip_tables: tuple) -> None:
        """Requirements: FR-DRD-0011.

        Point every link of the source records to the destination record, but in the skipped tables.

        One batched update per table; a link that cannot move because the destination already has
        it stays on the archived record.
        """
        native = self.env['base.partner.merge.automatic.wizard']
        for table, column in native._get_fk_on(src_records._table):
            if table in skip_tables or table.startswith('base_partner_merge_'):
                continue
            other_columns = [name for name in table_columns(self.env.cr, table) if name != column]
            if len(other_columns) == 1:
                self._repoint_relation_table(table, column, other_columns[0], src_records, dst_record)
            else:
                self._repoint_table(table, column, src_records, dst_record, native)

    def _repoint_relation_table(self, table: str, column: str, other_column: str,
                                src_records: models.BaseModel, dst_record: models.BaseModel) -> None:
        """Requirements: FR-DRD-0011.

        Move the rows of a two-column relation the destination does not hold yet.

        One update per source record (at most MERGE_MAX_RECORDS - 1): a single update for all of
        them would not see the rows it moves itself, so two sources sharing a value the destination
        lacks would both move it and break the relation's primary key.
        """
        for src_id in src_records.ids:
            self.env.cr.execute(SQL(
                """UPDATE %(table)s AS moved SET %(column)s = %(dst)s
                    WHERE %(column)s = %(src)s AND NOT EXISTS (
                        SELECT 1 FROM %(table)s AS held
                        WHERE held.%(column)s = %(dst)s AND held.%(other)s = moved.%(other)s)""",
                table=SQL.identifier(table), column=SQL.identifier(column), other=SQL.identifier(other_column),
                dst=dst_record.id, src=src_id))

    def _repoint_table(self, table: str, column: str, src_records: models.BaseModel,
                       dst_record: models.BaseModel, native: models.BaseModel) -> None:
        """Requirements: FR-DRD-0011.

        Move the rows of a table in one update; only when a unique or check constraint refuses it,
        move the rows one by one and leave the refused ones on the archived record.
        """
        update = SQL(
            "UPDATE %s SET %s = %s WHERE %s = ANY(%s)",
            SQL.identifier(table), SQL.identifier(column), dst_record.id, SQL.identifier(column), src_records.ids)
        if not native._has_check_or_unique_constraint(table, column):
            self.env.cr.execute(update)
            return
        try:
            with mute_logger('odoo.sql_db'), self.env.cr.savepoint():
                self.env.cr.execute(update)
        except IntegrityError:
            self._repoint_rows_one_by_one(table, column, src_records, dst_record)

    def _repoint_rows_one_by_one(self, table: str, column: str, src_records: models.BaseModel,
                                 dst_record: models.BaseModel) -> None:
        """Requirements: FR-DRD-0011. Fallback of the batched update: keep the rows the destination already has."""
        self.env.cr.execute(SQL(
            "SELECT ctid FROM %s WHERE %s = ANY(%s)", SQL.identifier(table), SQL.identifier(column),
            src_records.ids))
        for (row,) in self.env.cr.fetchall():
            update = SQL(
                "UPDATE %s SET %s = %s WHERE ctid = %s::tid",
                SQL.identifier(table), SQL.identifier(column), dst_record.id, row)
            try:
                with mute_logger('odoo.sql_db'), self.env.cr.savepoint():
                    self.env.cr.execute(update)
            except IntegrityError:
                continue  # the destination already has this link

    def _fill_empty_fields(self, kept: models.BaseModel, filled_values: dict) -> None:
        """Requirements: FR-DRD-0012.

        Write the preview's values on the fields of the kept record that are still empty.
        """
        kept.invalidate_recordset()
        values = {name: value for name, value in filled_values.items() if not kept[name]}
        if values:
            kept.write(values)

    def _mark_group_merged(self, kept: models.BaseModel, included_ids: set[int]) -> None:
        """Requirements: FR-DRD-0013, BR-DRD-0004.

        Mark the set merged; the records left out of the merge stay listed, with the kept record,
        in a new open set. Every other open set, of any rule, that holds a merged-away record
        gets the kept record in its place.
        """
        group = self.group_id
        left_out = group.line_ids.filtered(lambda line: line.res_id not in included_ids)
        if group:
            self._close_group(group, kept)
        self._replace_in_open_sets(kept, included_ids - {kept.id})
        if not group:
            return
        left_out_records = self.env[self.res_model].browse(left_out.mapped('res_id')).exists()
        if left_out_records:
            records = kept | left_out_records
            self.env['duplicate.group'].create({
                'rule_id': group.rule_id.id, 'model_name': group.model_name,
                'company_id': group.company_id.id,
                'line_ids': [Command.create({
                    'res_model': record._name, 'res_id': record.id,
                    'name': record.display_name, 'company_id': record.company_id.id,
                }) for record in records],
            })

    def _close_group(self, group: models.BaseModel, kept: models.BaseModel) -> None:
        """Mark a set merged into the kept record."""
        group.write({
            'state': 'merged', 'kept_res_id': kept.id, 'kept_name': kept.display_name,
            'merge_date': fields.Datetime.now(), 'merge_user_id': self.env.uid})

    def _replace_in_open_sets(self, kept: models.BaseModel, merged_away_ids: set[int]) -> None:
        """Requirements: FR-DRD-0013, BR-DRD-0004.

        Put the kept record in the place of the merged-away records in every open set, drop the
        duplicate lines, and close as merged a set left with fewer than 2 records.
        """
        lines = self.env['duplicate.group.line'].search([
            ('group_id.state', '=', 'open'), ('res_model', '=', kept._name),
            ('res_id', 'in', list(merged_away_ids))])
        groups = lines.group_id
        for group in groups:
            group_lines = lines.filtered(lambda line: line.group_id == group)
            if kept.id in group.line_ids.mapped('res_id'):
                group_lines.unlink()
            else:
                group_lines[1:].unlink()
                group_lines[:1].write({
                    'res_id': kept.id, 'name': kept.display_name, 'company_id': kept.company_id.id})
        for group in groups.filtered(lambda g: len(g.line_ids) < 2):
            self._close_group(group, kept)

    def _post_merge_note(self, kept: models.BaseModel, merged_names: list[str]) -> None:
        """Requirements: FR-DRD-0013 (a note on the kept record names the merged-away records)."""
        kept.message_post(body=escape(_(
            "Merged with %(names)s by %(user)s on %(date)s. The merged-away records are archived.",
            names=', '.join(merged_names), user=self.env.user.name,
            date=fields.Date.context_today(self))))
