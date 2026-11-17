from odoo import api, fields, models

from ..services import audit, export


class DataQualityAuditPurge(models.TransientModel):
    _name = 'data.quality.audit.purge'
    _description = 'Audit Purge Dialog'

    date_before = fields.Date(
        string='Delete entries before', required=True,
        help="Entries older than this day are deleted for good. The purge itself is written to "
             "the audit trail.")
    entry_count = fields.Integer(
        string='Entries to delete', compute='_compute_entry_count',
        help="How many entries the purge would delete now.")

    @api.depends('date_before')
    def _compute_entry_count(self):
        """Count the entries the purge would delete.

        Requirements: FR-DGQ-0073
        """
        for wizard in self:
            if not wizard.date_before:
                wizard.entry_count = 0
                continue
            wizard.entry_count = export.count_entries_before(self.env, wizard.date_before)

    def action_purge(self):
        """Delete the old entries and write the purge entry (administrators only).

        Requirements: FR-DGQ-0073
        """
        self.ensure_one()
        audit.purge(self.env, self.date_before)
        return {'type': 'ir.actions.act_window_close'}


class DataQualityAuditExport(models.TransientModel):
    _name = 'data.quality.audit.export'
    _description = 'Audit Export Dialog'

    date_from = fields.Date(string='From', help="First day to export. Empty: from the first entry.")
    date_to = fields.Date(string='To', help="Last day to export, included. Empty: up to the last entry.")
    event = fields.Selection(
        selection=lambda self: self.env['data.quality.audit']._fields['event'].selection,
        string='Event', help="Export only this event. Empty: every event.")
    user_ids = fields.Many2many('res.users', string='Users', help="Empty: every user.")
    model_ids = fields.Many2many('ir.model', string='Models', help="Empty: every model.")
    company_ids = fields.Many2many('res.company', string='Companies', help="Empty: all your companies.")
    file_format = fields.Selection(
        [('xlsx', 'XLSX'), ('pdf', 'PDF')], string='Format', required=True, default='xlsx')

    def _get_entries(self):
        """Return the entries that match the filters, in the user's companies.

        Requirements: FR-DGQ-0074
        """
        self.ensure_one()
        return self.env['data.quality.audit'].search(export.audit_domain(self))

    def action_export(self):
        """Give the matching entries as an XLSX file or a PDF report (administrators only).

        Requirements: FR-DGQ-0074
        """
        self.ensure_one()
        if self.file_format == 'pdf':
            return self.env.ref(
                'data_governance_quality_center_app.action_report_audit').report_action(
                    self._get_entries())
        return {
            'type': 'ir.actions.act_url',
            'url': '/data_quality/xlsx/audit/%s' % self.id,
            'target': 'self',
        }
