from odoo import models

from ..engine import apply


class IrUiView(models.Model):
    _inherit = 'ir.ui.view'

    def _postprocess_access_rights(self, tree):
        """Drop the sections left without a field once the user's groups are applied.

        Requirements: FR-FVF-0003
        """
        return apply.prune_empty_sections(super()._postprocess_access_rights(tree))
