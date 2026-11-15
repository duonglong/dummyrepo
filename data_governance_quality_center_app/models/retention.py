from odoo import api, models

from ..services import retention


class DataQualityRetention(models.AbstractModel):
    """The entry point of the daily cleanup scheduled action.

    Requirements: FR-DGQ-0077
    """
    _name = 'data.quality.retention'
    _description = 'Data Quality Retention'

    @api.model
    def _dgq_cleanup(self):
        """Delete the scan details, import files and closed issues past their retention.

        Requirements: FR-DGQ-0077
        """
        return retention.purge(self.env)
