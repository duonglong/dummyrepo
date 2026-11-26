from odoo import Command, api, fields, models
from odoo.exceptions import UserError


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
            raise UserError(self.env._("A saved snapshot never changes."))

    def write(self, vals):
        self._check_not_saved()
        return super().write(vals)

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
        self.write({'is_saved': True})

    @api.depends('record_count')
    def _compute_notice(self):
        """Say that a report of a model without records holds nothing to rate.

        Requirements: FR-DPA-0028
        """
        for report in self:
            report.notice = False if report.record_count else self.env._("No records to profile.")

    @api.model
    def _store_report(self, profile, result, run_date):
        """Write the report of a run of the profile, replacing its previous unsaved report.

        Requirements: FR-DPA-0003, FR-DPA-0004, FR-DPA-0009, FR-DPA-0010, FR-DPA-0011, NFR-REL-0001
        """
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
