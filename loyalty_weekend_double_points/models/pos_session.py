from odoo import api, models


class LoyaltyProgram(models.Model):
    _inherit = 'loyalty.program'

    @api.model
    def _load_pos_data_fields(self, config):
        """Requirements: FR-DLP-007: send the weekend option to the point of sale."""
        return super()._load_pos_data_fields(config) + ['weekend_double_points']


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _load_pos_data_fields(self, config):
        """Requirements: FR-DLP-007, BR-DLP-001: send the timezone that decides the weekend."""
        return super()._load_pos_data_fields(config) + ['tz']
