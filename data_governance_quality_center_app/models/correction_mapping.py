from odoo import api, fields, models


class DataQualityCorrectionMapping(models.Model):
    """A local spelling and the country or state it stands for.

    A change of a mapping is a change of what its correction does: the correction goes back to
    draft and loses its approval.

    Requirements: FR-DGQ-0048
    """
    _name = 'data.quality.correction.mapping'
    _description = 'Data Quality Correction Mapping'

    correction_id = fields.Many2one(
        'data.quality.correction', string='Correction', required=True, ondelete='cascade', index=True)
    source_text = fields.Char(string='Spelling', required=True)
    country_id = fields.Many2one('res.country', string='Country', ondelete='restrict')
    state_id = fields.Many2one(
        'res.country.state', string='State', ondelete='cascade',
        domain="[('country_id', '=?', country_id)]")

    @api.model_create_multi
    def create(self, vals_list):
        mappings = super().create(vals_list)
        mappings.correction_id._dgq_back_to_draft()
        return mappings

    def write(self, vals):
        corrections = self.correction_id
        result = super().write(vals)
        (corrections | self.correction_id)._dgq_back_to_draft()
        return result

    def unlink(self):
        corrections = self.correction_id
        result = super().unlink()
        corrections._dgq_back_to_draft()
        return result
