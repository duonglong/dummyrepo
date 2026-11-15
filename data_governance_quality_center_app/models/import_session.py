from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..const import JOB_BATCH_SIZE
from ..services import import_check

START_STATES = ('draft', 'failed', 'cancelled')


class DataQualityImportSession(models.Model):
    """A file checked against the fields, links, repeats and quality rules of a model, without importing it.

    The check is a background job: the checkpoint is the number of rows already checked.
    The findings are the rows of ``row_ids``; nothing is written to the model of the file.

    Requirements: FR-DGQ-0058, FR-DGQ-0059, FR-DGQ-0062
    """
    _name = 'data.quality.import.session'
    _inherit = ['data.quality.job.mixin', 'data.quality.result.mixin']
    _description = 'Data Quality Import Check'
    _order = 'id desc'
    _dgq_cron_xmlid = 'data_governance_quality_center_app.ir_cron_dgq_import'

    name = fields.Char(compute='_compute_name', store=True, readonly=False)
    job_kind = fields.Selection([
        ('check', 'Check'),
        ('import', 'Import'),
    ], default='check', required=True, copy=False)
    model_id = fields.Many2one(
        'ir.model', string='Model', required=True, ondelete='cascade', index=True,
        domain=lambda self: self.env['data.quality.rule']._dgq_model_domain(),
        help="The model the file is meant for. Only the models marked supported or restricted are offered.")
    res_model = fields.Char(related='model_id.model', store=True, readonly=True, required=False)
    company_id = fields.Many2one(
        required=True, ondelete='cascade', default=lambda self: self.env.company,
        help="The company the checked records would belong to.")
    user_id = fields.Many2one(default=lambda self: self.env.user)
    file = fields.Binary(string='File', attachment=True, copy=False)
    file_name = fields.Char(string='File Name', copy=False)
    file_type = fields.Selection(
        [('csv', 'CSV'), ('xlsx', 'XLSX')], compute='_compute_file_type', store=True, readonly=False)
    file_checksum = fields.Char(copy=False)
    options = fields.Json(copy=False)
    column_mapping = fields.Json(copy=False, readonly=True)
    mode = fields.Selection([
        ('strict', 'Strict'),
        ('warning', 'Warnings only'),
    ], default='strict', required=True,
        help="Strict refuses the import while any error remains; warnings only imports the rows without errors.")
    rule_ids = fields.Many2many(
        'data.quality.rule', string='Rules', copy=False,
        domain="[('model_id', '=', model_id)]",
        help="The quality rules the rows must pass. Leave empty to use every active rule of the model.")
    duplicate_field_ids = fields.Many2many(
        'ir.model.fields', string='Duplicate Fields', copy=False,
        domain="[('model_id', '=', model_id), ('store', '=', True)]",
        help="Two rows with the same values in these fields are repeats.")
    import_state = fields.Selection([
        ('not_imported', 'Not imported'),
        ('imported', 'Imported'),
        ('failed', 'Failed'),
    ], default='not_imported', required=True, copy=False)
    date_checked = fields.Datetime(copy=False, readonly=True)
    date_imported = fields.Datetime(copy=False, readonly=True)
    row_count = fields.Integer(copy=False, readonly=True)
    error_count = fields.Integer(copy=False, readonly=True)
    warning_count = fields.Integer(copy=False, readonly=True)
    error_row_count = fields.Integer(string='Rows With Errors', copy=False, readonly=True)
    imported_count = fields.Integer(copy=False, readonly=True)
    next_row = fields.Integer(copy=False, help="The index of the next row to check.")
    row_ids = fields.One2many('data.quality.import.row', 'session_id', string='Findings')

    # job state and progress are written by the app only
    state = fields.Selection(readonly=True)
    total_count = fields.Integer(readonly=True)
    done_count = fields.Integer(readonly=True)

    @api.depends('file_name')
    def _compute_name(self):
        for session in self:
            session.name = session.file_name or session.name or False

    @api.depends('file_name')
    def _compute_file_type(self):
        for session in self:
            extension = (session.file_name or '').rsplit('.', 1)[-1].lower()
            session.file_type = extension if extension in ('csv', 'xlsx') else False

    @api.constrains('model_id')
    def _check_model_is_marked(self):
        """Only models marked supported or restricted can be checked.

        Requirements: FR-DGQ-0058
        """
        allowed = self.env['data.quality.rule']._dgq_marked_models()
        for session in self:
            if session.model_id not in allowed:
                raise ValidationError(self.env._(
                    "%s is not marked as supported in the data quality settings and its import files "
                    "cannot be checked. A data quality administrator can mark it in Data Quality > "
                    "Configuration > Models.", session.model_id.name))

    @api.constrains('rule_ids', 'duplicate_field_ids', 'model_id')
    def _check_scope_of_model(self):
        """The rules and the duplicate fields belong to the model of the file."""
        for session in self:
            if (session.rule_ids.model_id - session.model_id
                    or session.duplicate_field_ids.model_id - session.model_id):
                raise ValidationError(self.env._(
                    "The rules and the duplicate fields must belong to %s.", session.model_id.name))

    @api.onchange('model_id')
    def _onchange_model_id(self):
        self.rule_ids = False
        self.duplicate_field_ids = False

    @staticmethod
    def _dgq_as_text(vals):
        """The values with an uploaded file as the base64 text a binary field takes (a screen may send bytes)."""
        if isinstance(vals.get('file'), bytes):
            return dict(vals, file=vals['file'].decode())
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._dgq_as_text(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(self._dgq_as_text(vals))

    # ---- job ---------------------------------------------------------------

    def action_start(self):
        """Read the file, refuse it when it cannot be checked, and queue the check from its first row.

        Requirements: FR-DGQ-0058
        """
        for session in self:
            if session.state not in START_STATES:
                raise UserError(self.env._("Only a draft, failed or cancelled check can be started."))
            session.write({'file_checksum': import_check.file_checksum(session) if session.file else False})
            loaded = import_check.load_file(session)
            import_check.forget_file(session)
            # sudo: the findings are written by the app only
            session.sudo().row_ids.unlink()
            session.write({
                'next_row': 0, 'row_count': 0, 'error_count': 0, 'warning_count': 0, 'error_row_count': 0,
                'done_count': 0, 'total_count': len(loaded.rows), 'date_checked': False,
                'column_mapping': {column.header: column.field.name for column in loaded.columns},
            })
        return super().action_start()

    def _dgq_run_batch(self):
        """Check the next rows of the file; return ``(processed, remaining)``.

        Requirements: FR-DGQ-0058, FR-DGQ-0059, NFR-DGQ-0003
        """
        self.ensure_one()
        if self.job_kind != 'check':
            raise UserError(self.env._("Only the check of a file is available."))
        loaded = import_check.load_file(self)
        first = self.next_row
        findings = import_check.check_batch(self, loaded, first, JOB_BATCH_SIZE)
        # sudo: the findings are written by the app only
        self.env['data.quality.import.row'].sudo().create(findings)
        errors = [finding for finding in findings if finding['severity'] == 'error']
        processed = min(JOB_BATCH_SIZE, len(loaded.rows) - first)
        self.write({
            'next_row': first + processed,
            'row_count': len(loaded.rows),
            'error_count': self.error_count + len(errors),
            'warning_count': self.warning_count + len(findings) - len(errors),
            'error_row_count': self.error_row_count + len({finding['row_number'] for finding in errors}),
        })
        return processed, len(loaded.rows) - first - processed

    def _dgq_record_batch(self, processed, remaining):
        """Store the progress; a finished check is dated and releases the file kept in memory."""
        super()._dgq_record_batch(processed, remaining)
        if not remaining:
            self.write({'date_checked': fields.Datetime.now()})
            import_check.forget_file(self)


class DataQualityImportRow(models.Model):
    """One finding of a checked file: a row, a column and a message.

    Requirements: FR-DGQ-0059
    """
    _name = 'data.quality.import.row'
    _inherit = ['data.quality.result.mixin']
    _description = 'Data Quality Import Finding'
    _order = 'session_id, row_number, id'

    session_id = fields.Many2one(
        'data.quality.import.session', string='Session', required=True, ondelete='cascade', index=True)
    res_model = fields.Char(related='session_id.res_model', store=True, readonly=True, required=False)
    company_id = fields.Many2one(
        related='session_id.company_id', store=True, readonly=True, ondelete='cascade')
    row_number = fields.Integer(string='Row', required=True)
    column = fields.Char(string='Column')
    field_name = fields.Char(string='Technical Name')
    severity = fields.Selection([('error', 'Error'), ('warning', 'Warning')], required=True)
    message = fields.Char(required=True)
    value = fields.Char(string='Value')
