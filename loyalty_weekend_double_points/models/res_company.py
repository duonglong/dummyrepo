from odoo import api, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _load_pos_data_fields(self, config):
        """Requirements: FR-DLP-007, BR-DLP-001: send the timezone that decides the weekend."""
        return super()._load_pos_data_fields(config) + ['tz']
