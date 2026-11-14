from odoo import fields, models


class DataQualityCorrectionLog(models.Model):
    """One value that a run would change, changed, skipped or failed to change, before and after.

    Requirements: FR-DGQ-0047, FR-DGQ-0049, FR-DGQ-0050
    """
    _name = 'data.quality.correction.log'
    _inherit = ['data.quality.result.mixin']
    _description = 'Data Quality Correction Log'
    _order = 'run_id, id'

    run_id = fields.Many2one(
        'data.quality.correction.run', string='Run', required=True, ondelete='cascade', index=True)
    res_id = fields.Many2oneReference(string='Record', model_field='res_model', index=True)
    field_name = fields.Char(string='Field')
    value_before = fields.Json(string='Before')
    value_after = fields.Json(string='After')
    state = fields.Selection(
        [('would_change', 'Would change'), ('changed', 'Changed'), ('skipped', 'Skipped'),
         ('failed', 'Failed'), ('restored', 'Restored'), ('not_restored', 'Not restored')],
        string='Result', required=True, index=True)
    reason = fields.Char(string='Reason')
    user_id = fields.Many2one('res.users', string='User', ondelete='set null')
    date = fields.Datetime(string='Time')
    restored = fields.Boolean(
        string='Restored', readonly=True,
        help="The rollback put the value before back.")
