from odoo import models


class ValidateAccountMove(models.TransientModel):
    _inherit = 'validate.account.move'

    def validate_move(self):
        """Check the Not checked vendor bills of the selection, then post.

        Requirements: FR-IPR-0002, FR-IPR-0016
        """
        self.move_ids._ipr_prepare_post()
        return super().validate_move()
