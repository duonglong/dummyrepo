from odoo import api, fields, models
from odoo.exceptions import AccessError

from ..services import portal_access, sourcing

# Fields only the submission service writes (it writes with elevated rights after its checks).
WORKFLOW_FIELDS = ('state', 'current_version_id', 'version_ids')


class ProcurementSourcingResponse(models.Model):
    """One invited supplier's answer to a sourcing event: its versions and its own deadline.

    Requirements: FR-SPM-0020, FR-SPM-0021, FR-SPM-0022, FR-SPM-0032, FR-SPM-0034
    The header was built with the sending of the event (T-013); T-014 adds the versions,
    the submission and the deadline check.
    """
    _name = 'procurement.sourcing.response'
    _description = 'Sourcing Response'
    _order = 'id'

    event_id = fields.Many2one('procurement.sourcing.event', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', string="Supplier", required=True, ondelete='restrict', index=True)
    state = fields.Selection([
        ('invited', 'Invited'), ('submitted', 'Submitted'), ('reopened', 'Reopened'),
    ], required=True, default='invited')
    version_ids = fields.One2many('procurement.sourcing.response.version', 'response_id', string="Versions")
    current_version_id = fields.Many2one(
        'procurement.sourcing.response.version', string="Current version", readonly=True,
        copy=False, index=True)
    reopened_deadline = fields.Datetime(string="Own deadline")
    effective_deadline = fields.Datetime(
        compute='_compute_effective_deadline', store=True, string="Deadline for this supplier")
    company_id = fields.Many2one(related='event_id.company_id', store=True, index=True)

    _event_partner_unique = models.Constraint(
        'UNIQUE(event_id, partner_id)', 'A supplier answers an event once.')

    @api.depends('event_id.deadline', 'reopened_deadline')
    def _compute_effective_deadline(self):
        """The supplier's own deadline when it is later than the event's, else the event's.

        Requirements: FR-SPM-0022
        """
        for response in self:
            deadline = response.event_id.deadline
            if response.reopened_deadline and (not deadline or response.reopened_deadline > deadline):
                deadline = response.reopened_deadline
            response.effective_deadline = deadline

    @api.model_create_multi
    def create(self, vals_list):
        """A user creates an Invited response without versions; the service writes the rest.

        Requirements: FR-SPM-0021
        """
        if not self.env.su:
            for vals in vals_list:
                if vals.get('current_version_id') or vals.get('version_ids') \
                        or vals.get('state', 'invited') != 'invited':
                    raise AccessError(self.env._("The versions of a response change with its submissions only."))
        return super().create(vals_list)

    def write(self, vals):
        """The state, the versions and the current version change with the submissions only.

        Requirements: FR-SPM-0021
        """
        if not self.env.su and any(field in vals for field in WORKFLOW_FIELDS):
            raise AccessError(self.env._("The versions of a response change with its submissions only."))
        return super().write(vals)

    def spm_submit_version(self, lines, attachment_ids=None, **terms):
        """The supplier's portal user submits a new version of the quotation of its own response.

        Requirements: FR-SPM-0020, FR-SPM-0021, FR-SPM-0022

        ``lines`` are dicts with event_line_id, price_unit, discount, lead_time_days (and tax_ids);
        ``terms`` are payment_term_id, payment_days, validity_date, warranty_months and notes.
        The response must be the session user's own, the event open and the deadline not passed.
        """
        self.ensure_one()
        portal_access.check_own_response(self.env, self)
        return sourcing.add_version(self, lines, attachment_ids, terms, by_buyer=False)

    def spm_enter_quotation(self, lines, attachment_ids=None, **terms):
        """The Buyer enters a quotation received outside the portal as a new version.

        Requirements: FR-SPM-0034

        The buyer needs the right to change the response; the deadline does not apply.
        """
        self.ensure_one()
        self.check_access('write')
        return sourcing.add_version(self, lines, attachment_ids, terms, by_buyer=True)

    def action_spm_open_entry_wizard(self):
        """Open the dialog where the Buyer types a quotation received outside the portal.

        Requirements: FR-SPM-0034
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Enter quotation"),
            'res_model': 'procurement.quotation.entry',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_response_id': self.id},
        }
