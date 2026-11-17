from odoo import api, fields, models
from odoo.fields import Domain

from ..services.scan import TALLY_COUNTERS


class DataQualityScanTally(models.Model):
    """Running counts of a scan per model, company and rule: the input of the score.

    A row with no rule is the model row (records scanned and failing); a row with a rule
    counts the checks of that rule. Each batch adds its counts, so they survive the commits
    between batches.

    Requirements: FR-DGQ-0019
    """
    _name = 'data.quality.scan.tally'
    _description = 'Data Quality Scan Tally'

    scan_id = fields.Many2one('data.quality.scan', required=True, ondelete='cascade', index=True)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    company_id = fields.Many2one('res.company', ondelete='cascade')
    rule_id = fields.Many2one('data.quality.rule', ondelete='cascade')
    rule_version = fields.Integer()
    severity = fields.Selection([
        ('info', 'Information'),
        ('warning', 'Warning'),
        ('critical', 'Critical'),
    ])
    records_scanned = fields.Integer()
    records_applicable = fields.Integer()
    records_failed = fields.Integer()
    records_failing = fields.Integer()
    records_error = fields.Integer()
    records_warning = fields.Integer()

    @api.model
    def _access_domain(self, operation):
        """A tally is as visible as its scan, which is separated by company.

        Requirements: FR-DGQ-0071, BR-DGQ-0011
        """
        domain = super()._access_domain(operation)
        if operation == 'create':
            return domain
        return domain & Domain('scan_id', 'access', 'read')

    @api.model
    def _dgq_add(self, scan, model, batch):
        """Add the counts of one batch to the scan's rows; ``batch`` maps (company id, rule id) to counts.

        Requirements: FR-DGQ-0019, FR-DGQ-0016
        """
        existing = {
            (row.company_id.id, row.rule_id.id): row
            for row in self.search([('scan_id', '=', scan.id), ('model_id', '=', model.id)])}
        created = []
        for key, counts in batch.items():
            row = existing.get(key)
            if row:
                row.write({name: row[name] + counts[name] for name in TALLY_COUNTERS})
            else:
                company_id, rule_id = key
                created.append(dict(counts, scan_id=scan.id, model_id=model.id,
                                    company_id=company_id or False, rule_id=rule_id or False))
        self.create(created)

    _scan_row_unique = models.Constraint(
        'UNIQUE NULLS NOT DISTINCT (scan_id, model_id, company_id, rule_id)', 'A scan has one tally per model, company and rule.')
