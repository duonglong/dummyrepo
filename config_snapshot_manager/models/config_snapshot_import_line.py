from odoo import fields, models


class ConfigSnapshotImportLine(models.TransientModel):
    """One difference found between an import file and the settings here.

    Requirements: FR-TCM-006, FR-TCM-007, BR-TCM-001, BR-TCM-002, NFR-SEC-001, NFR-USE-001
    """

    _name = 'config.snapshot.import.line'
    _description = 'Import difference'
    _order = 'key'

    import_id = fields.Many2one(
        'config.snapshot.import', required=True, ondelete='cascade', index=True)
    key = fields.Char(required=True)
    change_type = fields.Selection(
        [('added', 'Added'), ('modified', 'Modified'), ('removed', 'Removed')],
        required=True)
    sensitive = fields.Boolean()
    file_sensitive = fields.Boolean()
    current_value = fields.Text()
    file_value = fields.Text()
    parameter_id = fields.Many2one('ir.config_parameter', ondelete='set null')
