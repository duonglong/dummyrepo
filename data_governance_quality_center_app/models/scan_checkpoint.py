from odoo import api, fields, models
from odoo.fields import Domain


class DataQualityScanCheckpoint(models.Model):
    """Where a scan stands in one model.

    Requirements: FR-DGQ-0015
    """
    _name = 'data.quality.scan.checkpoint'
    _description = 'Data Quality Scan Checkpoint'

    scan_id = fields.Many2one('data.quality.scan', required=True, ondelete='cascade', index=True)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    last_id = fields.Integer()
    records_done = fields.Integer()
    done = fields.Boolean()

    @api.model
    def _access_domain(self, operation):
        """A checkpoint is as visible as its scan, which is separated by company.

        The ``access`` operator applies the scan's own access rules as the user; ``any`` would
        not, since Odoo evaluates access domains as superuser.

        Requirements: FR-DGQ-0071, BR-DGQ-0011
        """
        domain = super()._access_domain(operation)
        if operation == 'create':
            return domain
        return domain & Domain('scan_id', 'access', 'read')

    _scan_model_unique = models.Constraint(
        'UNIQUE(scan_id, model_id)', 'A scan has one checkpoint per model.')
