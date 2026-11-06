from odoo import fields, models


class RecordChangeEntry(models.Model):
    """Requirements: FR-RCT-001, FR-RCT-003.

    One readable change of one followed field of one record: field, old and new value as text,
    author and date.
    """
    _name = 'record.change.entry'
    _description = 'Recorded change of a followed field'
    _order = 'change_date desc, id desc'

    res_model = fields.Char(string='Record type', required=True, index=True, readonly=True)
    res_id = fields.Many2oneReference(
        string='Record', model_field='res_model', required=True, index=True, readonly=True)
    field_id = fields.Many2one(
        'ir.model.fields', string='Field', ondelete='set null', readonly=True)
    field_name = fields.Char(string='Technical name', required=True, readonly=True)
    field_label = fields.Char(string='Field label', required=True, readonly=True)
    old_value = fields.Text(string='Old value', readonly=True)
    new_value = fields.Text(string='New value', readonly=True)
    author_id = fields.Many2one(
        'res.users', string='Changed by', ondelete='set null', readonly=True)
    change_date = fields.Datetime(
        string='Changed on', required=True, default=fields.Datetime.now, index=True, readonly=True)

    def init(self):
        """Index serving the history of one record, newest first (NFR-PERF-001)."""
        self.env.cr.execute(
            "CREATE INDEX IF NOT EXISTS record_change_entry_record_date_idx "
            "ON record_change_entry (res_model, res_id, change_date DESC)")
