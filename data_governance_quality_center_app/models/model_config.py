from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class DataQualityModelConfig(models.Model):
    _name = 'data.quality.model.config'
    _description = 'Data Quality Model Mark'
    _rec_name = 'model_id'

    model_id = fields.Many2one(
        'ir.model', string='Model', required=True, ondelete='cascade', index=True)
    model_name = fields.Char(related='model_id.model', string='Technical Name', store=True)
    status = fields.Selection(
        [('supported', 'Supported'), ('excluded', 'Excluded'), ('restricted', 'Restricted')],
        string='Status', required=True, default='supported',
        help="Supported and restricted models are offered in rules, duplicate rules, corrections, "
             "profiles and import checks. A restricted model is protected: corrections and merges "
             "need an explicit authorization.")

    _model_unique = models.Constraint(
        'UNIQUE(model_id)', 'A model can only carry one mark.')

    @api.constrains('model_id')
    def _check_model_has_table(self):
        """Refuse a model that has no table of its own (abstract, transient or view-backed).

        Requirements: FR-DGQ-0075
        """
        for config in self:
            model = self.env.registry.get(config.model_id.model)
            if model is None or not model._auto or model._transient:
                raise ValidationError(_(
                    "%s has no table of its own and cannot be checked.", config.model_id.name))

    @api.model
    def _get_status(self, model_name):
        """Return the mark of a model: 'supported', 'excluded', 'restricted', or False if unmarked.

        Requirements: FR-DGQ-0075
        """
        # sudo: the mark is an administrator setting that every scan must see
        config = self.sudo().search([('model_name', '=', model_name)], limit=1)
        return config.status or False
