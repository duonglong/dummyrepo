from odoo import Command, api, fields, models


class DpaSnapshot(models.Model):
    _name = 'dpa.snapshot'
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
    field_line_ids = fields.One2many('dpa.snapshot.field', 'snapshot_id', string="Fields")
    finding_ids = fields.One2many('dpa.snapshot.finding', 'snapshot_id', string="Findings")

    @api.model
    def _store_report(self, profile, result, run_date):
        """Write the report of a run of the profile, replacing its previous unsaved report.

        Requirements: FR-DPA-0003, FR-DPA-0004, FR-DPA-0009, FR-DPA-0010, NFR-REL-0001
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
            'field_line_ids': [
                Command.create(self.env['dpa.snapshot.field']._line_values(line, sequence, field_ids))
                for sequence, line in enumerate(result['fields'], start=1)
            ],
        })
        report._store_findings(result)
        return report

    def _store_findings(self, result):
        """Create the findings the engine returned, each on the line of its field.

        Requirements: FR-DPA-0009, FR-DPA-0010
        """
        self.ensure_one()
        lines = {line.field_name: line for line in self.field_line_ids}
        self.env['dpa.snapshot.finding'].create([
            {**finding, 'snapshot_id': self.id, 'field_line_id': lines[line['field'].name].id}
            for line in result['fields'] for finding in line.get('findings', [])
        ])
