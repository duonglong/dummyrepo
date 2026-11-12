from odoo import api, models


class Base(models.AbstractModel):
    _inherit = 'base'

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        """Organize a form with the layout published for the current company.

        Nothing is written to ``ir.ui.view``: the layout is applied on top of
        the combined arch each time the form is built. A draft takes the
        place of the published layout while ``fvf_preview_layout_id`` is set.

        Requirements: FR-FVF-0008, FR-FVF-0009, FR-FVF-0012, BR-FVF-0002
        """
        arch, view = super()._get_view(view_id, view_type, **options)
        if view.type != 'form' or 'fvf.layout' not in self.env.registry:
            return arch, view
        layout = self._fvf_layout_of_form(view)
        if layout:
            arch = layout._fvf_apply_to(arch)
        return arch, view

    @api.model
    def _fvf_preview_layout_id(self) -> int:
        """Return the draft id asked for in the context, or 0 unless a Settings administrator asked with an integer.

        Requirements: FR-FVF-0014, NFR-SEC-0001
        """
        preview_id = self.env.context.get('fvf_preview_layout_id')
        if not self.env.is_system() or type(preview_id) is not int:
            return 0
        return preview_id

    @api.model
    def _fvf_layout_of_form(self, view):
        """Return the layout that organizes ``view`` for the current company, else an empty recordset."""
        Layout = self.env['fvf.layout']
        company_id = self.env.company.id
        published = Layout._fvf_layout_for(self._name, view.id, company_id)
        # sudo: the preview is checked against the form and company, and only an administrator can start one
        preview = Layout.sudo().browse(self._fvf_preview_layout_id()).exists()
        if preview.view_id != view or preview.model != self._name:
            return published
        own_published = Layout._fvf_published_map().get(self._name, {}).get((view.id, company_id))
        if preview.company_id.id == company_id or (not preview.company_id and not own_published):
            return preview
        return published

    @api.model
    def _get_view_cache_key(self, view_id=None, view_type='form', **options):
        """Tell the cached forms of one company's layouts from another's.

        Requirements: BR-FVF-0002, FR-FVF-0014, NFR-PERF-0001
        """
        key = super()._get_view_cache_key(view_id, view_type, **options)
        if 'fvf.layout' not in self.env.registry:
            return key
        key += (self.env['fvf.layout']._fvf_cache_token(self._name),)
        preview_id = self._fvf_preview_layout_id()
        return key + (('fvf_preview', preview_id),) if preview_id else key
