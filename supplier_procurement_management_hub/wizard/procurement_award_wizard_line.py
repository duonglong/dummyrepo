from odoo import fields, models


class ProcurementAwardWizardLine(models.TransientModel):
    """The winner chosen for one line of the event in the award dialog.

    Requirements: FR-SPM-0039
    """
    _name = 'procurement.award.wizard.line'
    _description = 'Award Dialog Line'

    wizard_id = fields.Many2one('procurement.award.wizard', required=True, ondelete='cascade')
    event_id = fields.Many2one(related='wizard_id.event_id')
    event_line_id = fields.Many2one('procurement.sourcing.event.line', required=True, readonly=True)
    name = fields.Char(related='event_line_id.name')
    response_id = fields.Many2one(
        'procurement.sourcing.response', string="Winning response",
        domain="[('event_id', '=', event_id), ('current_version_id', '!=', False)]")
