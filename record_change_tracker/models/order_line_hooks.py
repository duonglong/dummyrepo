from odoo import api, models


class RecordChangeLineMixin(models.AbstractModel):
    """Records the change of a parent order's stored computed fields (the total) that a line
    create, write or unlink causes outside the order's own save (the product catalog, a price recompute).

    Requirements: FR-RCT-001, FR-RCT-009
    """
    _name = 'record.change.line.mixin'
    _description = 'Change recording through the order lines'

    def _rct_line_snapshot(self, orders):
        """(orders, snapshot) or None; only the stored computed followed fields are watched."""
        if self.env.context.get('tracking_disable') or self.env.context.get('rct_in_write'):
            return None
        orders = orders.filtered('id')
        snapshot = orders._rct_snapshot({'order_line': True})
        return (orders, snapshot) if snapshot else None

    @api.model_create_multi
    def create(self, vals_list):
        orders = self.env[self._fields['order_id'].comodel_name].browse(
            [v['order_id'] for v in vals_list if v.get('order_id')])
        state = self._rct_line_snapshot(orders)
        lines = super().create(vals_list)
        if state:
            state[0]._rct_record_changes(state[1])
        return lines

    def write(self, vals):
        state = self._rct_line_snapshot(self.order_id)
        result = super().write(vals)
        if state:
            state[0]._rct_record_changes(state[1])
        return result

    def unlink(self):
        state = self._rct_line_snapshot(self.order_id)
        result = super().unlink()
        if state:
            state[0]._rct_record_changes(state[1])
        return result
