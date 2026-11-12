import copy
import logging

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..engine import apply

_logger = logging.getLogger(__name__)

# What a published layout still accepts: its life cycle, never its content.
LIFECYCLE_FIELDS = frozenset(['state', 'active', 'published_date', 'published_uid', 'origin_id'])


def _models_with_main_form(layouts) -> list:
    """Domain on ir.model: the models that have a main (primary) form view."""
    # sudo: only the names of models that have a form are read, so the choice never depends on view access
    views = layouts.env['ir.ui.view'].sudo()
    names = [
        model for (model,) in views._read_group(
            [('type', '=', 'form'), ('mode', '=', 'primary')], groupby=['model'])
        if model]
    return [('model', 'in', names)]


class FvfLayout(models.Model):
    _name = 'fvf.layout'
    _description = 'Form layout'
    _order = 'model, name, id'
    # the published map and the views built with it are cached: publishing, withdrawing, archiving, deleting clear them
    _clear_cache_name = 'templates'
    _clear_cache_on_fields = ('state', 'active', 'company_id', 'view_id')

    _published_unique = models.UniqueIndex(
        "(view_id, COALESCE(company_id, 0)) WHERE state = 'published' AND active")
    _one_draft_copy = models.UniqueIndex("(origin_id) WHERE origin_id IS NOT NULL AND active")

    name = fields.Char(
        compute='_compute_name', store=True, readonly=False, required=True, precompute=True)
    active = fields.Boolean(default=True)
    model_id = fields.Many2one(
        'ir.model', string='Model', required=True, ondelete='cascade',
        domain=_models_with_main_form)
    model = fields.Char(string='Model name', related='model_id.model', store=True, index=True)
    view_id = fields.Many2one(
        'ir.ui.view', string='Form', required=True, ondelete='cascade',
        domain="[('model', '=', model), ('type', '=', 'form'), ('mode', '=', 'primary')]")
    company_id = fields.Many2one('res.company', string='Company', ondelete='cascade')
    state = fields.Selection(
        [('draft', 'Draft'), ('published', 'Published')],
        required=True, default='draft', index=True, copy=False)
    origin_id = fields.Many2one('fvf.layout', string='Draft copy of', ondelete='set null', copy=False)
    published_date = fields.Datetime(copy=False)
    published_uid = fields.Many2one('res.users', copy=False)
    section_ids = fields.One2many('fvf.layout.section', 'layout_id', string='Sections', copy=True)
    line_ids = fields.One2many('fvf.layout.line', 'layout_id', string='Fields', copy=True)
    missing_count = fields.Integer(compute='_compute_missing_count')

    @api.depends('model_id', 'company_id')
    def _compute_name(self) -> None:
        for layout in self:
            scope = layout.company_id.name or _("Default")
            layout.name = "%s – %s" % (layout.model_id.name or _("Layout"), scope)

    @api.depends('line_ids.missing')
    def _compute_missing_count(self) -> None:
        for layout in self:
            layout.missing_count = len(layout.line_ids.filtered('missing'))

    @api.constrains('model_id', 'view_id')
    def _check_view_is_main_form_of_model(self) -> None:
        """Requirements: FR-FVF-0001"""
        for layout in self:
            view = layout.view_id
            if view.model != layout.model or view.type != 'form' or view.mode != 'primary':
                raise ValidationError(_("Choose a main form view of the chosen model."))

    @api.onchange('model_id')
    def _onchange_model_id(self) -> None:
        if self.view_id and self.view_id.model != self.model_id.model:
            self.view_id = False

    @api.model_create_multi
    def create(self, vals_list: list[dict]) -> 'FvfLayout':
        """Requirements: FR-FVF-0001 (a new layout lists the fields of its form)"""
        layouts = super().create(vals_list)
        layouts._fvf_sync_lines()
        return layouts

    def _fvf_form_field_requirements(self) -> dict:
        """Return ``{field_name: [required attribute of each occurrence]}`` for the form.

        Only fields of the form itself count, not those of the sub-views of
        its x2many fields; the names are in order of first appearance.
        """
        self.ensure_one()
        try:
            arch = self.view_id._get_combined_arch()
        except (ValidationError, ValueError) as error:
            raise UserError(_("This form cannot be read.")) from error
        occurrences = {}
        for node in arch.iter('field'):
            if any(parent.tag == 'field' for parent in node.iterancestors()):
                continue
            occurrences.setdefault(node.get('name'), []).append(node.get('required'))
        return occurrences

    def _fvf_readable_form_fields(self) -> dict:
        """Like ``_fvf_form_field_requirements`` but an unreadable form counts as having no field.

        Used by the computed marks, so one broken form never stops the layouts
        list from opening; the actions still raise the message.
        """
        self.ensure_one()
        if not self.view_id:
            return {}
        try:
            return self._fvf_form_field_requirements()
        except UserError:
            _logger.warning("fvf: layout %s cannot read form view %s", self.id, self.view_id.id)
            return {}

    def _fvf_sync_lines(self) -> None:
        """Add a line for each field of the form that has none yet.

        Requirements: FR-FVF-0001
        """
        Line = self.env['fvf.layout.line']
        for layout in self:
            known = set(layout.line_ids.mapped('field_name'))
            model_fields = self.env[layout.model]._fields
            new_names = [
                name for name in layout._fvf_form_field_requirements()
                if name in model_fields and name not in known]
            Line.create([
                {'layout_id': layout.id, 'field_name': name, 'sequence': 10 * index}
                for index, name in enumerate(new_names, start=len(known) + 1)])

    def action_refresh_fields(self) -> bool:
        """Requirements: FR-FVF-0001"""
        self._fvf_sync_lines()
        return True

    def action_add_section(self, name: str | None = None):
        """Add a section to the layout; the layout is already saved when a button calls this.

        Requirements: FR-FVF-0003
        """
        self.ensure_one()
        last = self.section_ids[-1:].sequence or 0
        return self.env['fvf.layout.section'].create({
            'layout_id': self.id,
            'name': name or _("New section"),
            'sequence': last + 10,
        })

    # -- locks ---------------------------------------------------------------

    def _fvf_check_unlocked(self) -> None:
        """Refuse a change of the content of a published layout.

        Requirements: FR-FVF-0014
        """
        if self.filtered(lambda layout: layout.state == 'published'):
            raise UserError(_("A published layout cannot be changed. Use Edit to make a draft copy."))

    def write(self, vals: dict) -> bool:
        """Requirements: FR-FVF-0013, FR-FVF-0014 (a published layout keeps its content; archiving withdraws it)"""
        published = self.filtered(lambda layout: layout.state == 'published')
        if not LIFECYCLE_FIELDS.issuperset(vals):
            published._fvf_check_unlocked()
        if vals.get('active') is False and published and 'state' not in vals:
            result = super(FvfLayout, published).write(dict(vals, state='draft'))
            result &= super(FvfLayout, self - published).write(vals)
        else:
            result = super().write(vals)
        return result

    # -- copy ----------------------------------------------------------------

    def copy(self, default: dict | None = None) -> 'FvfLayout':
        """Copy a layout as an unpublished draft whose lines point at the copied sections.

        Requirements: FR-FVF-0014
        """
        default = dict(default or {}, section_ids=[], line_ids=[])
        copies = self.browse()
        for layout in self:
            new = super(FvfLayout, layout).copy(default)
            new._fvf_copy_content_of(layout)
            copies |= new
        return copies

    def _fvf_copy_content_of(self, source: 'FvfLayout') -> None:
        """Give this fresh draft the sections, lines and conditions of ``source``."""
        self.ensure_one()
        sections = {
            old: self.env['fvf.layout.section'].create({
                'layout_id': self.id, 'name': old.name, 'sequence': old.sequence,
                'start_state': old.start_state})
            for old in source.section_ids}
        lines = {line.field_name: line for line in self.line_ids}
        for old in source.line_ids:
            vals = {
                'sequence': old.sequence,
                'section_id': sections[old.section_id].id if old.section_id else False,
                'hidden_group_ids': [Command.set(old.hidden_group_ids.ids)],
            }
            line = lines.get(old.field_name)
            if line:
                line.write(vals)
            else:
                line = self.env['fvf.layout.line'].create(
                    dict(vals, layout_id=self.id, field_name=old.field_name))
            line.condition_ids = [Command.create({
                'field_name': condition.field_name, 'operator': condition.operator,
                'value': condition.value, 'value_res_id': condition.value_res_id,
                'sequence': condition.sequence}) for condition in old.condition_ids]

    # -- publish, withdraw, edit -----------------------------------------------

    def _fvf_lock_live(self) -> 'FvfLayout':
        """Return the layouts published for this form and company, their rows locked."""
        self.ensure_one()
        self.flush_model(['state', 'active', 'view_id', 'company_id'])
        self.env.cr.execute(
            """SELECT id FROM fvf_layout
                WHERE view_id = %s AND COALESCE(company_id, 0) = %s
                  AND state = 'published' AND active
                  FOR UPDATE""",
            [self.view_id.id, self.company_id.id or 0])
        return self.browse(row[0] for row in self.env.cr.fetchall())

    def action_publish(self) -> bool:
        """Publish the draft for its form and company, replacing the layout live there.

        Requirements: FR-FVF-0012, BR-FVF-0003
        """
        for layout in self:
            if layout.state != 'draft':
                raise UserError(_("Only a draft layout can be published."))
            for live in layout._fvf_lock_live():
                if live == layout.origin_id:
                    live.write({'active': False})
                else:
                    live.write({'state': 'draft'})
                live.flush_recordset()
            layout.write({
                'state': 'published',
                'published_date': fields.Datetime.now(),
                'published_uid': self.env.uid,
                'origin_id': False,
            })
            _logger.info(
                "fvf: layout %s published for view %s company %s by uid %s",
                layout.id, layout.view_id.id, layout.company_id.id, self.env.uid)
        return True

    def action_withdraw(self) -> bool:
        """Take the published layout off the forms and keep it as a draft.

        Requirements: FR-FVF-0013
        """
        for layout in self:
            if layout.state != 'published':
                raise UserError(_("Only a published layout can be withdrawn."))
            layout.write({'state': 'draft'})
            _logger.info(
                "fvf: layout %s withdrawn for view %s company %s by uid %s",
                layout.id, layout.view_id.id, layout.company_id.id, self.env.uid)
        return True

    def action_edit(self) -> dict:
        """Open the single draft copy of a published layout, making it on first use.

        Requirements: FR-FVF-0014
        """
        self.ensure_one()
        draft = self.with_context(active_test=False).search(
            [('origin_id', '=', self.id), ('active', '=', True)], limit=1)
        if not draft:
            draft = self.copy({'origin_id': self.id})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'fvf.layout',
            'res_id': draft.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_discard_draft_copy(self) -> dict:
        """Delete a draft copy and go back to the published layout it edits.

        Requirements: FR-FVF-0014
        """
        self.ensure_one()
        origin = self.origin_id
        self.unlink()
        return origin.action_edit_target() if origin else {'type': 'ir.actions.act_window_close'}

    def action_edit_target(self) -> dict:
        """Open this layout's form."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'fvf.layout',
            'res_id': self.id, 'view_mode': 'form', 'target': 'current'}

    # -- what the forms read --------------------------------------------------

    def _fvf_spec(self) -> dict:
        """Return the plain layout the engine reads.

        Lines of conditions on a field that no longer exists are left out; a
        group is named by its external id, else by its id.

        Requirements: FR-FVF-0012, FR-FVF-0015
        """
        self.ensure_one()
        model_fields = self.env[self.model]._fields
        groups = self.line_ids.hidden_group_ids
        refs = groups._get_external_ids()
        group_ref = {group.id: (refs[group.id] or [str(group.id)])[0] for group in groups}
        return {
            'layout_id': self.id,
            'sections': [
                {'id': section.id, 'name': section.name, 'start_closed': section.start_state == 'closed'}
                for section in self.section_ids],
            'lines': [{
                'field': line.field_name,
                'section_id': line.section_id.id or False,
                'sequence': line.sequence,
                'hide_refs': sorted(group_ref[group.id] for group in line.hidden_group_ids),
                'condition': [
                    {'field': condition.field_name, 'operator': condition.operator, 'value': condition.value}
                    for condition in line.condition_ids if condition.field_name in model_fields],
            } for line in self.line_ids],
        }

    @api.model
    @api.ormcache(cache='templates')
    def _fvf_published_map(self) -> dict:
        """Return ``{model: {(view_id, company_id or 0): layout_id}}`` of the published layouts.

        Requirements: BR-FVF-0002, BR-FVF-0003
        """
        # sudo: every user builds forms, only administrators read layouts
        layouts = self.sudo().search([('state', '=', 'published')])
        published = {}
        for layout in layouts:
            published.setdefault(layout.model, {})[(layout.view_id.id, layout.company_id.id or 0)] = layout.id
        return published

    @api.model
    def _fvf_layout_for(self, model: str, view_id: int, company_id: int) -> 'FvfLayout':
        """Return the layout shown for this form in this company: its own, else the default, else none.

        Requirements: BR-FVF-0002, FR-FVF-0008, FR-FVF-0009
        """
        entries = self._fvf_published_map().get(model, {})
        layout_id = entries.get((view_id, company_id)) or entries.get((view_id, 0))
        # sudo: every user builds forms, only administrators read layouts
        return self.sudo().browse(layout_id)

    @api.model
    def _fvf_cache_token(self, model: str) -> tuple:
        """Return what tells the layouts of ``model`` for the current company apart; empty without layouts.

        Requirements: BR-FVF-0002, NFR-PERF-0001
        """
        entries = self._fvf_published_map().get(model)
        if not entries:
            return ()
        company_id = self.env.company.id
        return tuple(sorted(
            (view_id, self._fvf_layout_for(model, view_id, company_id).id)
            for view_id in {view for view, _company in entries}))

    def _fvf_apply_to(self, arch):
        """Return a copy of ``arch`` organized by this layout; the untouched ``arch`` when that fails.

        Requirements: FR-FVF-0012, FR-FVF-0015
        """
        self.ensure_one()
        model_fields = {name: {'type': field.type} for name, field in self.env[self.model]._fields.items()}
        try:
            # sudo: the spec reads groups and sections the user may not read
            return apply.apply_layout(copy.deepcopy(arch), self.sudo()._fvf_spec(), model_fields)
        except Exception:  # noqa: BLE001  a layout must never stop a form from opening (ADR-FVF-001)
            _logger.exception("fvf: layout %s not applied to view %s", self.id, self.view_id.id)
            return arch
