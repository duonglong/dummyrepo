from odoo import api, fields, models

EXCEPTION_KINDS = [
    ('price', 'Price difference'),
    ('quantity', 'Quantity difference'),
    ('duplicate', 'Possible duplicate'),
]


class IprException(models.Model):
    _name = 'ipr.exception'
    _description = 'Bill Reconciliation Exception'
    _order = 'move_id, id'

    move_id = fields.Many2one(
        'account.move', string='Bill', required=True, ondelete='cascade', index=True, readonly=True)
    company_id = fields.Many2one(
        'res.company', related='move_id.company_id', string='Company', store=True)
    kind = fields.Selection(EXCEPTION_KINDS, string='Kind', required=True, readonly=True)
    check_line_id = fields.Many2one(
        'ipr.check.line', string='Comparison Line', ondelete='cascade', readonly=True)
    duplicate_move_ids = fields.Many2many(
        'account.move', 'ipr_exception_duplicate_move_rel', 'exception_id', 'move_id',
        string='Other Bills', readonly=True)
    description = fields.Char(string='Description', compute='_compute_description')

    @api.depends('kind', 'check_line_id', 'duplicate_move_ids')
    def _compute_description(self):
        """Describe the exception in the reader's language.

        Requirements: FR-IPR-0003, FR-IPR-0004
        """
        for exception in self:
            line = exception.check_line_id
            if exception.kind == 'price':
                exception.description = self.env._(
                    '%(product)s: ordered price %(ordered)s, billed price %(billed)s, difference %(difference)s',
                    product=line.name or '',
                    ordered=line.currency_id.format(line.ordered_price),
                    billed=line.currency_id.format(line.billed_price),
                    difference=line.currency_id.format(line.price_difference))
            elif exception.kind == 'quantity':
                exception.description = self.env._(
                    '%(product)s: received %(received)s, billed %(billed)s, difference %(difference)s',
                    product=line.name or '',
                    received=line.received_qty, billed=line.billed_qty,
                    difference=line.qty_difference)
            else:
                exception.description = self.env._(
                    'Same supplier and reference as %(bills)s',
                    bills=', '.join(exception.duplicate_move_ids.mapped('display_name')))
