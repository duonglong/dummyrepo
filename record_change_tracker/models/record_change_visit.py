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
        # One atomic upsert: two simultaneous first openings must not hit the unique constraint.
        self.env.cr.execute("""
            INSERT INTO record_change_visit
                   (user_id, res_model, res_id, last_visit, create_uid, create_date,
                    write_uid, write_date)
            SELECT %(uid)s, %(model)s, rec_id, %(now)s, %(uid)s, %(now)s, %(uid)s, %(now)s
              FROM unnest(%(ids)s) AS rec_id
            ON CONFLICT (user_id, res_model, res_id) DO UPDATE
               SET previous_visit = record_change_visit.last_visit,
                   last_visit = EXCLUDED.last_visit,
                   write_uid = EXCLUDED.write_uid, write_date = EXCLUDED.write_date
        """, {'uid': self.env.uid, 'model': records._name, 'now': now, 'ids': list(records.ids)})
        self.invalidate_model(['last_visit', 'previous_visit'])
