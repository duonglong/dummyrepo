from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import sourcing


class ProcurementAwardWizard(models.TransientModel):
    """The dialog that awards a sourcing event, to one response or line by line.

    Requirements: FR-SPM-0039, FR-SPM-0041
    """
    _name = 'procurement.award.wizard'
    _description = 'Award a Sourcing Event'

    event_id = fields.Many2one('procurement.sourcing.event', required=True, readonly=True)
    mode = fields.Selection([
        ('whole', 'The whole event to one response'), ('per_line', 'Line by line'),
    ], required=True, default='whole')
    response_id = fields.Many2one(
        'procurement.sourcing.response', string="Winning response",
        domain="[('event_id', '=', event_id), ('current_version_id', '!=', False)]")
    line_choice_ids = fields.One2many('procurement.award.wizard.line', 'wizard_id', string="Winners by line")
    rationale = fields.Text(required=True)

    @api.model
    def default_get(self, fields_list):
        """One choice row per line of the event."""
        values = super().default_get(fields_list)
        event = self.env['procurement.sourcing.event'].browse(values.get('event_id'))
        if 'line_choice_ids' in fields_list and event:
            values['line_choice_ids'] = [(0, 0, {'event_line_id': line.id}) for line in event.line_ids]
        return values

    def action_confirm(self):
        """Award the event with the chosen responses and the rationale, then close the dialog.

        Requirements: FR-SPM-0039, FR-SPM-0041
        """
        self.ensure_one()
        event = self.event_id
        if self.mode == 'whole':
            if not self.response_id:
                raise UserError(self.env._("Choose the winning response."))
            choices = {line: self.response_id for line in event.line_ids}
            choices = {
                line: response for line, response in choices.items()
                if response.current_version_id.line_ids.event_line_id & line}
        else:
            choices = {
                choice.event_line_id: choice.response_id for choice in self.line_choice_ids
                if choice.response_id}
        sourcing.award(event, choices, self.rationale)
        return {'type': 'ir.actions.act_window_close'}
