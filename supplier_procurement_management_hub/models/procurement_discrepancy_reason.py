from odoo import fields, models

DISCREPANCY_KINDS = [
    ('late', 'Late'), ('short', 'Short'), ('over', 'Over'),
    ('damaged', 'Damaged'), ('wrong_item', 'Wrong item'),
]


class ProcurementDiscrepancyReason(models.Model):
    """A configurable reason of a delivery discrepancy, of one of the five kinds.

    Requirements: FR-SPM-0060
    Shared by all companies (no company field).
    """
    _name = 'procurement.discrepancy.reason'
    _description = 'Delivery Discrepancy Reason'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    kind = fields.Selection(DISCREPANCY_KINDS, required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
