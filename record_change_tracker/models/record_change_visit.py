from odoo import api, fields, models


class RecordChangeVisit(models.Model):
    """Requirements: FR-RCT-004, FR-RCT-005, FR-RCT-006, BR-RCT-005.

    When one user last opened one record, and the opening before that.
    """
    _name = 'record.change.visit'
    _description = 'Last visit of a user to a record'

    user_id = fields.Many2one('res.users', required=True, ondelete='cascade', index=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Many2oneReference(model_field='res_model', required=True)
    last_visit = fields.Datetime(required=True)
    previous_visit = fields.Datetime()

    _unique_visit = models.Constraint(
        'unique (user_id, res_model, res_id)', 'One visit line per user and record.')

    @api.model
    def _register_visit(self, records):
        """Requirements: FR-RCT-004. Shift last visit into previous and store now for each record."""
        now = fields.Datetime.now()
        visits = self.sudo().search([
            ('user_id', '=', self.env.uid), ('res_model', '=', records._name),
            ('res_id', 'in', records.ids)])
        by_id = {visit.res_id: visit for visit in visits}
        to_create = []
        for rec_id in records.ids:
            visit = by_id.get(rec_id)
            if visit:
                visit.write({'previous_visit': visit.last_visit, 'last_visit': now})
            else:
                to_create.append({
                    'user_id': self.env.uid, 'res_model': records._name,
                    'res_id': rec_id, 'last_visit': now})
        if to_create:
            self.sudo().create(to_create)
