from odoo import fields, models


class PerfAuditApplyWizard(models.TransientModel):
    """The confirmation shown before one fix runs: the finding and the exact statement.

    Requirements: FR-OPA-0029, BR-OPA-0004
    """

    _name = 'perf.audit.apply.wizard'
    _description = 'Confirm the fix of a finding'

    finding_id = fields.Many2one('perf.audit.finding', required=True, ondelete='cascade')
    statement = fields.Text(related='finding_id.fix_statement', readonly=True)

    def action_confirm(self):
        """Run the statement of the finding, the only way an apply happens.

        Requirements: FR-OPA-0029
        """
        self.ensure_one()
        self.finding_id._apply_fix()
        return {'type': 'ir.actions.client', 'tag': 'reload'}
