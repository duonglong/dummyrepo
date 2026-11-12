from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_READ_ONLY_FORM_ATTRIBUTES = ('edit', 'create', 'delete', 'duplicate')


def _remove(node) -> None:
    """Remove ``node`` and hand the text that follows it to the node before it or to its parent."""
    parent = node.getparent()
    if node.tail:
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or '') + node.tail
        else:
            parent.text = (parent.text or '') + node.tail
    parent.remove(node)


def _unwrap(node) -> None:
    """Replace ``node`` by its children, which keeps a group's fields side by side."""
    for child in reversed(node):
        node.addnext(child)
    _remove(node)


class FvfLayoutPreview(models.TransientModel):
    _name = 'fvf.layout.preview'
    _description = 'Form layout preview'

    layout_id = fields.Many2one('fvf.layout', string="Layout", required=True, ondelete='cascade')
    group_id = fields.Many2one('res.groups', string="As a user of group", required=True, ondelete='cascade')
    company_id = fields.Many2one(
        'res.company', string="In company", required=True, ondelete='cascade',
        default=lambda self: self._default_company())
    res_model = fields.Char(related='layout_id.model', string="Model name")
    res_id = fields.Many2oneReference(string="Record", model_field='res_model', required=True)

    @api.model
    def _default_company(self):
        layout = self.env['fvf.layout'].browse(self.env.context.get('default_layout_id'))
        if layout.company_id in self.env.companies:
            return layout.company_id
        return self.env.company

    def action_show_preview(self) -> dict:
        """Open the client action that draws the preview chosen in the dialog.

        Requirements: FR-FVF-0010
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'fvf_layout_preview',
            'name': _("Preview"),
            'params': {'preview_id': self.id},
        }

    def get_preview_view(self) -> dict:
        """Build the read-only form of the chosen record as the chosen group and company would see it.

        The draft takes the place the published layout would have. The form is
        built here, never through the view cache, and nothing is written.

        Requirements: FR-FVF-0010, FR-FVF-0011, BR-FVF-0006, NFR-SEC-0001
        """
        self.ensure_one()
        if not self.env.is_system():
            raise AccessError(_("Only Settings administrators can preview a layout."))
        layout = self.layout_id
        company = self.company_id
        model = self._in_company_only(layout.model, company)
        record = self._readable_record(model)
        model = model.with_context(fvf_preview_layout_id=layout.id)
        arch, view = model._get_view(layout.view_id.id, 'form')
        arch, field_names_by_model = model._get_view_postprocessed(view, arch)
        field_names_by_model = model._get_view_fields('form', field_names_by_model)
        tree = etree.fromstring(arch)
        self._resolve_groups(tree, self._simulated_groups())
        tree = self.env['ir.ui.view']._postprocess_access_rights(tree)
        self._make_read_only(tree)
        related_models = {name: {'fields': self._describe_fields(names, name)} for name, names in field_names_by_model.items()}
        return {
            'arch': etree.tostring(tree, encoding='unicode'),
            'fields': related_models[layout.model]['fields'],
            'relatedModels': related_models,
            'res_model': layout.model,
            'res_id': record.id,
            'layout_id': layout.id,
            'company_id': company.id,
            'banner': _(
                "Preview (read-only) as %(group)s in %(company)s - %(record)s",
                group=self.group_id.full_name, company=company.name, record=record.display_name),
        }

    def _in_company_only(self, model_name: str, company):
        """Return ``model_name`` working in ``company`` alone, the company set the browser reads the record with."""
        return self.env[model_name].with_context(allowed_company_ids=[company.id])

    def _readable_record(self, model):
        """Return the chosen record of ``model``, or refuse when it is missing or cannot be read."""
        record = model.browse(self.res_id).exists()
        try:
            record.check_access('read')
        except AccessError:
            record = record.browse()
        if not record:
            raise UserError(_("The chosen record cannot be shown."))
        return record

    def _describe_fields(self, names, model_name: str) -> dict:
        """Describe ``names`` the way ``get_views`` does for the web client."""
        model = self.env[model_name]
        return model.fields_get(allfields=list(names), attributes=model._get_view_field_attributes())

    def _simulated_groups(self):
        """Return the groups of a user of the chosen group: itself, what it implies, and everyone."""
        return self.group_id | self.group_id.all_implied_ids | self.env.ref('base.group_everyone')

    def _resolve_groups(self, tree, group_ids) -> None:
        """Drop what the simulated group set cannot see, as the native view build does for the real user."""
        definitions = self.env['res.groups']._get_group_definitions()
        for node in tree.xpath('//*[@__groups_key__]'):
            if definitions.from_key(node.attrib.pop('__groups_key__')).matches(group_ids._ids):
                if node.tag == 't' and not node.attrib:
                    _unwrap(node)
            else:
                _remove(node)

    def _make_read_only(self, tree) -> None:
        """Remove every action a person could press and forbid editing the form."""
        for node in tree.xpath('//button | //chatter'):
            _remove(node)
        for name in _READ_ONLY_FORM_ATTRIBUTES:
            tree.set(name, '0')
