import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


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
