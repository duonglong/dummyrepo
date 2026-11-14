from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """The app's block in Odoo's Settings: whether assignment notices are also sent by e-mail.

    Requirements: FR-DGQ-0076
    """
    _inherit = 'res.config.settings'

    dgq_notify_email = fields.Boolean(
        string='E-mail assignment notices', config_parameter='data_quality.notify_email', default=True,
        help="Also send an e-mail to the person an issue is assigned to; they are always notified in Odoo.")
