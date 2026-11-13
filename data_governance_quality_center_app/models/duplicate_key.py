from odoo import fields, models


class DataQualityDuplicateKey(models.Model):
    """A blocking key of one record during a check: a work table that the check empties at its end.

    Only records sharing a key are compared (ADR-DGQ-009), so a million records need no pairwise
    comparison. The table has no access group: only the background runner, in sudo, reads it.

    Requirements: FR-DGQ-0039, NFR-DGQ-0003
    """
    _name = 'data.quality.duplicate.key'
    _description = 'Data Quality Duplicate Blocking Key'

    check_id = fields.Many2one(
        'data.quality.duplicate.check', required=True, ondelete='cascade', index=True)
    block_key = fields.Char(required=True)
    res_id = fields.Integer(required=True)

    _check_block_key_idx = models.Index('(check_id, block_key)')
