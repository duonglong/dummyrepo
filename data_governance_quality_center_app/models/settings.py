from odoo import fields, models

from ..const import NOTIFY_EMAIL_DEFAULT, NOTIFY_EMAIL_PARAMETER


class ResConfigSettings(models.TransientModel):
    """The app's block in Odoo's Settings: whether assignment notices are also sent by e-mail.

    Requirements: FR-DGQ-0076
    """
    _inherit = 'res.config.settings'

    dgq_notify_email = fields.Boolean(
        string='E-mail assignment notices', config_parameter=NOTIFY_EMAIL_PARAMETER,
        default=NOTIFY_EMAIL_DEFAULT,
        help="Also send an e-mail to the person an issue is assigned to; they are always notified in Odoo.")
