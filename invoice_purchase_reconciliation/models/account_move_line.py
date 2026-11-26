from odoo import api, models

from ..services import reconciliation


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _ipr_watched_lines(self):
        """Return the product lines of checked draft vendor bills.

        Requirements: FR-IPR-0018
        """
        if self.env.context.get('ipr_skip_reset'):
            return self.browse()
        return self.filtered(
            lambda line: line.display_type == 'product' and line.move_id._ipr_resettable())

    @api.model_create_multi
    def create(self, vals_list):
        """Reset the checked draft bill a product line is added to.

        Requirements: FR-IPR-0018
        """
        lines = super().create(vals_list)
        lines._ipr_watched_lines().move_id._ipr_reset()
        return lines

    def write(self, vals):
        """Reset a checked draft bill when a product line really changes a watched value.

        Requirements: FR-IPR-0018
        """
        if not reconciliation.WATCHED_LINE_FIELDS_SET.intersection(vals):
            return super().write(vals)
        names = reconciliation.WATCHED_LINE_FIELDS
        before = {line: [line[name] for name in names] for line in self._ipr_watched_lines()}
        result = super().write(vals)
        changed = self.browse(
            line.id for line, old in before.items() if old != [line[name] for name in names])
        changed.move_id._ipr_reset()
        return result

    def unlink(self):
        """Reset the checked draft bill a product line is removed from.

        Requirements: FR-IPR-0018
        """
        moves = self._ipr_watched_lines().move_id
        result = super().unlink()
        moves._ipr_reset()
        return result
