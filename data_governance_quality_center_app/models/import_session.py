from odoo import api, fields, models


class DataQualityImportSession(models.Model):
    """A file checked against the quality rules before it is imported.

    Built by T-027 for T-023 (which realises the check itself): the record, its file and its scope,
    so that the cleanup can empty the file of an old session (FR-DGQ-0077).

    Requirements: FR-DGQ-0058, FR-DGQ-0077
    """
    _name = 'data.quality.import.session'
    _inherit = ['data.quality.job.mixin', 'data.quality.result.mixin']
    _description = 'Data Quality Import Check'
    _order = 'id desc'

    name = fields.Char(string='Reference', compute='_compute_name', store=True)
    model_id = fields.Many2one(
        'ir.model', string='Model', required=True, ondelete='cascade',
        help="The model the file would be imported into.")
    res_model = fields.Char(related='model_id.model', store=True, readonly=True, required=False)
    company_id = fields.Many2one(default=lambda self: self.env.company)
    file = fields.Binary(string='File', attachment=True, copy=False, help="The CSV or XLSX file to check.")
    file_name = fields.Char(string='File name')
    mode = fields.Selection(
        [('strict', 'Strict'), ('warning', 'Warning only')], string='Mode', default='strict', required=True,
        help="Strict stops the import when a row has an error; warning only imports anyway.")
    rule_ids = fields.Many2many('data.quality.rule', string='Rules', help="The rules applied to every row.")
    duplicate_field_ids = fields.Many2many(
        'ir.model.fields', string='Duplicate fields', help="Rows with the same values in these fields are duplicates.")

    @api.depends('file_name', 'model_id')
    def _compute_name(self):
        for session in self:
            session.name = session.file_name or session.model_id.name or False
