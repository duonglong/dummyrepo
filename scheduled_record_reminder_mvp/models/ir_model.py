from odoo import _, api, models
from odoo.exceptions import UserError


class IrModel(models.Model):
    """Refuse removing a record type that a reminder rule with history watches.

    Requirements: FR-SRR-0017, FR-SRR-0010, FR-SRR-0011.
    """

    _inherit = 'ir.model'

    @api.ondelete(at_uninstall=True)
    def _unlink_except_reminder_history(self) -> None:
        """The database cascade would delete the rule and its history without a word; uninstalling an app still may."""
        if self.pool.uninstalling_modules:
            return
        self.env['record.reminder.rule']._check_history_kept('model_id', self.ids)


class IrModelFields(models.Model):
    """Refuse removing a date field that a reminder rule with history counts from.

    Requirements: FR-SRR-0017, FR-SRR-0010, FR-SRR-0011.
    """

    _inherit = 'ir.model.fields'

    @api.ondelete(at_uninstall=True)
    def _unlink_except_reminder_history(self) -> None:
        """The database cascade would delete the rule and its history without a word; uninstalling an app still may."""
        if self.pool.uninstalling_modules:
            return
        self.env['record.reminder.rule']._check_history_kept('date_field_id', self.ids)
