from odoo import api, models

QUOTE_VERSION_MODEL = 'procurement.sourcing.response.version'


class IrAttachment(models.Model):
    """Files of a quotation version are never opened by an access token.

    Requirements: BR-SPM-0002, FR-SPM-0017
    A token opens a file for anyone who holds it; a supplier's quotation files belong to the
    supplier and the buyers, so a token written on such a file is dropped.
    """
    _inherit = 'ir.attachment'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('res_model') == QUOTE_VERSION_MODEL:
                vals.pop('access_token', None)
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('access_token'):
            # sudo: reads the target model of each file only
            quote_files = self.filtered(
                lambda a: (vals.get('res_model') or a.sudo().res_model) == QUOTE_VERSION_MODEL)
            if quote_files:
                rest = {key: value for key, value in vals.items() if key != 'access_token'}
                if rest:
                    super(IrAttachment, quote_files).write(rest)
                self -= quote_files
                if not self:
                    return True
        return super().write(vals)
