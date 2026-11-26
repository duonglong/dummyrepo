from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services.reconciliation import EVENTS, STATES


class IprHistory(models.Model):
    _name = 'ipr.history'
    _description = 'Bill Reconciliation History'
    _order = 'date desc, id desc'
    _rec_name = 'bill_reference'

    move_id = fields.Many2one(
        'account.move', string='Bill', ondelete='set null', index=True, readonly=True)
    move_name = fields.Char(string='Bill Number', readonly=True)
    partner_id = fields.Many2one(
        'res.partner', string='Supplier', ondelete='set null', readonly=True)
    partner_name = fields.Char(string='Supplier Name', readonly=True)
    bill_reference = fields.Char(string='Reference', readonly=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, ondelete='restrict',
        index=True, readonly=True)
    date = fields.Datetime(
        string='Date', required=True, default=fields.Datetime.now, readonly=True)
    user_id = fields.Many2one(
        'res.users', string='User', required=True, ondelete='restrict', readonly=True)
    event = fields.Selection(EVENTS, string='Event', required=True, readonly=True)
    state_after = fields.Selection(STATES, string='Status After', readonly=True)
    comment = fields.Text(string='Comment', readonly=True)
    price_tolerance_type = fields.Selection(
        [('percent', 'Percentage'), ('fixed', 'Fixed amount')],
        string='Price Tolerance Type', readonly=True)
    price_tolerance_percent = fields.Float(string='Price Tolerance (%)', readonly=True)  # sdd-allow: money-float a percentage, not money (T-003)
    price_tolerance_amount = fields.Monetary(
        string='Price Tolerance Amount', currency_field='tolerance_currency_id', readonly=True)
    tolerance_currency_id = fields.Many2one(
        'res.currency', string='Tolerance Currency', ondelete='restrict', readonly=True)
    qty_tolerance_type = fields.Selection(
        [('percent', 'Percentage'), ('fixed', 'Fixed units')],
        string='Quantity Tolerance Type', readonly=True)
    qty_tolerance_value = fields.Float(string='Quantity Tolerance', readonly=True)
    tolerances_display = fields.Char(string='Tolerances', compute='_compute_tolerances_display')
    exceptions_data = fields.Json(string='Exceptions Data', readonly=True)
    exception_count = fields.Integer(string='Exceptions Count', readonly=True)
    exceptions_display = fields.Text(string='Exceptions', compute='_compute_exceptions_display')

    _move_index = models.Index('(move_id)')
    _company_date_index = models.Index('(company_id, date)')
    _event_index = models.Index('(event)')

    @api.depends(
        'price_tolerance_type', 'price_tolerance_percent', 'price_tolerance_amount',
        'tolerance_currency_id', 'qty_tolerance_type', 'qty_tolerance_value')
    def _compute_tolerances_display(self):
        """Render the tolerances in force in the reader's language.

        Requirements: FR-IPR-0020
        """
        for entry in self:
            if not (entry.price_tolerance_type and entry.qty_tolerance_type):
                entry.tolerances_display = False
                continue
            if entry.price_tolerance_type == 'percent':
                price = f'{entry.price_tolerance_percent:g} %'
            else:
                price = entry.tolerance_currency_id.format(entry.price_tolerance_amount)
            unit = '%' if entry.qty_tolerance_type == 'percent' else self.env._('units')
            entry.tolerances_display = self.env._(
                'Price: %(price)s, Quantity: %(qty)s',
                price=price, qty=f'{entry.qty_tolerance_value:g} {unit}')

    @api.depends('exceptions_data')
    def _compute_exceptions_display(self):
        """Render the stored exceptions in the reader's language.

        Requirements: FR-IPR-0020
        """
        kind_labels = {
            'price': self.env._('Price difference'),
            'quantity': self.env._('Quantity difference'),
            'duplicate': self.env._('Possible duplicate'),
        }
        for entry in self:
            lines = []
            for item in entry.exceptions_data or []:
                label = kind_labels.get(item.get('kind'), item.get('kind') or '')
                details = [item.get('product')] + list(item.get('other_bills') or [])
                lines.append(' - '.join([label] + [str(d) for d in details if d]))
            entry.exceptions_display = '\n'.join(lines) or False

    def write(self, vals):
        """Refuse every change: history entries are only ever added.

        Requirements: FR-IPR-0021
        """
        raise UserError(self.env._('Reconciliation history cannot be changed or deleted.'))

    def unlink(self):
        """Refuse every deletion, for every user.

        Requirements: FR-IPR-0021
        """
        raise UserError(self.env._('Reconciliation history cannot be changed or deleted.'))
