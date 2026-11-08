from odoo import api, fields, models


class DuplicateMergeRecordLine(models.TransientModel):
    _name = 'duplicate.merge.record.line'
    _description = 'Merge Preview: Candidate Record'
    _order = 'record_create_date, res_id'

    wizard_id = fields.Many2one(
        'duplicate.merge.wizard', required=True, ondelete='cascade')
    res_id = fields.Integer(required=True)
    name = fields.Char()
    record_create_date = fields.Datetime()
    include = fields.Boolean(default=True)
    is_kept = fields.Boolean(compute='_compute_is_kept')

    @api.depends('wizard_id.kept_res_id', 'res_id')
    def _compute_is_kept(self) -> None:
        """Requirements: FR-DRD-0009 (the line of the record that is kept is marked)."""
        for line in self:
            line.is_kept = line.res_id == line.wizard_id.kept_res_id

    def action_keep_this(self) -> bool:
        """Requirements: FR-DRD-0009, FR-DRD-0017.

        Make this record the one that is kept; the preview is recomputed.
        """
        self.ensure_one()
        self.wizard_id._check_merge_rights()
        self.wizard_id.kept_res_id = self.res_id
        return True
