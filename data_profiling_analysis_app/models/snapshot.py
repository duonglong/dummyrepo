from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from ..services import xlsx
from ..services.const import PDF_MAX_LINES, PDF_TOP_VALUES


class DpaSnapshotFrozen(models.AbstractModel):
    _name = 'dpa.snapshot.frozen'
    _description = 'Lines that never change once their snapshot is saved'

    def _frozen_snapshots(self):
        """The snapshots these records belong to."""
        return self.env['dpa.snapshot']

    def _check_not_saved(self):
        """Refuse the change when one of the records belongs to a saved snapshot.

        Requirements: FR-DPA-0018
        """
        if self._frozen_snapshots().filtered('is_saved'):
            raise UserError(self.env._("A saved snapshot cannot be changed."))

    def write(self, vals):
        """Judge the records before and after the write, so a line cannot be moved into a saved snapshot."""
        self._check_not_saved()
        result = super().write(vals)
        self._check_not_saved()
        return result

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_not_saved()
        return records

    def unlink(self):
        self._check_not_saved()
        return super().unlink()


class DpaSnapshot(models.Model):
    _name = 'dpa.snapshot'
    _inherit = 'dpa.snapshot.frozen'
    _description = 'Profile report or snapshot'
    _order = 'run_date desc, id desc'

    name = fields.Char(required=True)
    profile_id = fields.Many2one('dpa.profile', string="Profile", ondelete='set null', index=True)
    profile_name = fields.Char()
    model_id = fields.Many2one('ir.model', string="Model", ondelete='set null')
    model_name = fields.Char(required=True, index=True)
    model_label = fields.Char()
    run_date = fields.Datetime(required=True, index=True)
    user_id = fields.Many2one('res.users', string="Author", ondelete='set null')
    is_saved = fields.Boolean(string="Saved as snapshot", index=True)
    origin = fields.Selection(
        [('manual', "Manual"), ('scheduled', "Scheduled")], default='manual', required=True)
    company_ids = fields.Many2many('res.company', string="Companies profiled")
    empty_threshold = fields.Float(string="Empty-value threshold (%)")
    outdated_days = fields.Integer(string="Outdated period (days)")
    record_count = fields.Integer(string="Records profiled")
    outdated_count = fields.Integer(string="Outdated records")
    has_log_access = fields.Boolean(
        string="Keeps last update date", default=True, help="False when the model keeps no last update date.")
    notice = fields.Char(compute='_compute_notice')
    field_line_ids = fields.One2many('dpa.snapshot.field', 'snapshot_id', string="Fields")
    finding_ids = fields.One2many('dpa.snapshot.finding', 'snapshot_id', string="Findings")
    outdated_ids = fields.One2many(
        'dpa.snapshot.finding', 'snapshot_id', string="Outdated records", domain=[('kind', '=', 'outdated')])

    def _frozen_snapshots(self):
        return self

    @api.model_create_multi
    def create(self, vals_list):
        """Create every snapshot unsaved with its lines, then save those that asked for it.

        Requirements: FR-DPA-0018
        """
        saved_flags = [bool(vals.get('is_saved')) for vals in vals_list]
        records = super().create([{**vals, 'is_saved': False} for vals in vals_list])
        to_save = records.browse([record.id for record, saved in zip(records, saved_flags) if saved])
        super(DpaSnapshotFrozen, to_save).write({'is_saved': True})
        return records

    def unlink(self):
        """Delete snapshots with all their lines: the one change a saved snapshot allows.

        Requirements: FR-DPA-0030
        """
        return super(DpaSnapshotFrozen, self).unlink()

    def action_save_snapshot(self):
        """Keep the shown report as a snapshot, once, with every line as it is now.

        Requirements: FR-DPA-0018
        """
        self.ensure_one()
        if self.is_saved:
            raise UserError(self.env._("This report is already saved as a snapshot."))
        super(DpaSnapshotFrozen, self).write({'is_saved': True, 'origin': 'manual', 'user_id': self.env.uid})
        self.profile_id.invalidate_recordset(['report_id'])

    def export_basename(self):
        """The file name of an export without its extension: the profile name and the run day.

        Requirements: FR-DPA-0026, FR-DPA-0027
        """
        self.ensure_one()
        run_day = fields.Datetime.context_timestamp(self, self.run_date).date().isoformat()
        return "%s %s" % (self.profile_name, run_day)

    def _export_xlsx(self):
        """The XLSX of the report as (file name, content), read as the caller.

        Requirements: FR-DPA-0026
        """
        self.ensure_one()
        self.check_access('read')
        return "%s.xlsx" % self.export_basename(), xlsx.build(self)

    def _export_pdf(self):
        """The PDF of the report as (file name, content), read as the caller.

        Requirements: FR-DPA-0027
        """
        self.ensure_one()
        self.check_access('read')
        report = self.env.ref('data_profiling_analysis_app.action_report_snapshot')
        content, _report_type = self.env['ir.actions.report']._render_qweb_pdf(report, self.ids)
        return "%s.pdf" % self.export_basename(), content

    def action_export_xlsx(self):
        """Open the download of the XLSX of the shown report.

        Requirements: FR-DPA-0026
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/data_profiling/snapshot/%d/xlsx' % self.id,
            'target': 'self',
        }

    def action_export_pdf(self):
        """Open the download of the PDF of the shown report.

        Requirements: FR-DPA-0027
        """
        self.ensure_one()
        # sudo: a Data Profiling member cannot read report actions; only the action is read, the data stays the caller's
        # config=False: under sudo Odoo takes everyone for an admin and would open the admin-only layout setup
        report = self.env.ref('data_profiling_analysis_app.action_report_snapshot').sudo()
        return report.report_action(self, config=False)

    def type_label(self, line):
        """The label Odoo gives the field type stored on a line ("char" reads "Text"), as the exports print it.

        Requirements: FR-DPA-0026, FR-DPA-0027
        """
        return line.field_type_label

    def _pdf_top_values(self, line):
        """The most frequent values of a field line, as many as the PDF lists.

        Requirements: FR-DPA-0027
        """
        return line.value_ids[:PDF_TOP_VALUES]

    def _pdf_values_more(self, line):
        """How many distinct values of a field line the PDF leaves out: stored lines past the cut and unstored ones.

        Requirements: FR-DPA-0027
        """
        return max(len(line.value_ids) - PDF_TOP_VALUES, 0) + line.more_count

    def _pdf_flagged(self):
        """The flagged values and the outdated records the PDF lists, each with how many more there are.

        Requirements: FR-DPA-0027
        """
        self.ensure_one()
        flagged = self.finding_ids.filtered(lambda finding: finding.kind != 'outdated')
        outdated = self.finding_ids - flagged
        return [
            {'title': self.env._("Flagged values"), 'listed': flagged[:PDF_MAX_LINES],
             'more': max(len(flagged) - PDF_MAX_LINES, 0)},
            {'title': self.env._("Outdated records"), 'listed': outdated[:PDF_MAX_LINES],
             'more': max(len(outdated) - PDF_MAX_LINES, 0)},
        ]

    @api.depends('record_count')
    def _compute_notice(self):
        """Say that a report of a model without records holds nothing to rate.

        Requirements: FR-DPA-0028
        """
        for report in self:
            report.notice = False if report.record_count else self.env._("No records to profile.")

    @api.model
    def _store_report(self, profile, result, run_date, origin=None):
        """Write the report of a run of the profile.

        A shown report replaces the previous unsaved one; a scheduled run (origin 'scheduled') leaves it alone
        and keeps its result as a saved snapshot.

        Requirements: FR-DPA-0003, FR-DPA-0004, FR-DPA-0009, FR-DPA-0010, FR-DPA-0011, NFR-REL-0001
        """
        if not origin:
            self.search([('profile_id', '=', profile.id), ('is_saved', '=', False)]).unlink()
        local_day = fields.Datetime.context_timestamp(self, run_date).date().isoformat()
        model = profile.model_id
        field_ids = {
            field.name: field.id
            for field in self.env['ir.model.fields'].search([('model_id', '=', model.id)])
        }
        report = self.create({
            'name': "%s %s" % (profile.name, local_day),
            'profile_id': profile.id,
            'profile_name': profile.name,
            'model_id': model.id,
            'model_name': profile.model_name,
            'model_label': model.name,
            'run_date': run_date,
            'user_id': self.env.user.id,
            'company_ids': [Command.set(self.env.companies.ids)],
            'empty_threshold': profile.empty_threshold,
            'outdated_days': profile.outdated_days,
            'record_count': result['record_count'],
            'outdated_count': len(result['outdated']),
            'has_log_access': self.env[profile.model_name]._log_access,
            'field_line_ids': [
                Command.create(self.env['dpa.snapshot.field']._line_values(line, sequence, field_ids))
                for sequence, line in enumerate(result['fields'], start=1)
            ],
        })
        report._store_findings(result)
        profile.invalidate_recordset(['report_id'])
        if origin:
            super(DpaSnapshotFrozen, report).write({'is_saved': True, 'origin': origin})
        return report

    def _store_findings(self, result):
        """Create the findings the engine returned, each on the line of its field.

        Requirements: FR-DPA-0009, FR-DPA-0010, FR-DPA-0011
        """
        self.ensure_one()
        lines = {line.field_name: line for line in self.field_line_ids}
        self.env['dpa.snapshot.finding'].create([
            {**finding, 'snapshot_id': self.id, 'field_line_id': lines[line['field'].name].id}
            for line in result['fields'] for finding in line.get('findings', [])
        ] + [{**finding, 'snapshot_id': self.id} for finding in result['outdated']])
