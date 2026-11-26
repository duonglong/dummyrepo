from collections import defaultdict

from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    ipr_history_ids = fields.One2many(
        'ipr.history', 'move_id', string='Reconciliation History', copy=False, readonly=True)

    def _ipr_add_history(self, event, state_after, comment=None, exceptions=None, tolerances=None):
        """Add one history entry per bill, copying what keeps it readable alone.

        Requirements: FR-IPR-0020
        """
        tolerances = tolerances or {}
        exceptions = exceptions or []
        vals_list = [{
            'move_id': move.id,
            'move_name': move.name if move.name and move.name != '/' else False,
            'partner_id': move.partner_id.id,
            'partner_name': move.partner_id.name,
            'bill_reference': move.ref,
            'company_id': move.company_id.id,
            'user_id': self.env.user.id,
            'event': event,
            'state_after': state_after,
            'comment': comment,
            'price_tolerance_type': tolerances.get('price_type'),
            'price_tolerance_percent': tolerances.get('price_percent'),
            'price_tolerance_amount': tolerances.get('price_amount'),
            'tolerance_currency_id': tolerances.get('currency') and tolerances['currency'].id,
            'qty_tolerance_type': tolerances.get('qty_type'),
            'qty_tolerance_value': tolerances.get('qty_value'),
            'exceptions_data': exceptions,
            'exception_count': len(exceptions),
        } for move in self]
        # sudo: no user holds a create right on the history, only this helper writes it
        return self.env['ipr.history'].sudo().create(vals_list)

    def unlink(self):
        """Record the deletion of a draft vendor bill that has history, then delete it.

        Requirements: FR-IPR-0022
        """
        # sudo: a user who deletes the bill may not hold the right to read its history
        with_history = self.sudo().filtered(
            lambda move: move.move_type == 'in_invoice' and move.state == 'draft'
            and move.ipr_history_ids)
        by_last_state = defaultdict(lambda: self.browse())
        for move in with_history:
            by_last_state[move.ipr_history_ids.sorted('id')[-1].state_after] |= move
        for last_state, moves in by_last_state.items():
            moves._ipr_add_history('delete', last_state, comment=self.env._('Bill deleted'))
        return super().unlink()
