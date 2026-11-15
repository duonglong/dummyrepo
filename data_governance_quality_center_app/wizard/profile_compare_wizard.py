from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services import profile_compare


class DataQualityProfileCompareWizard(models.TransientModel):
    """Dialog that compares a profile with an earlier profile of the same model and company.

    Requirements: FR-DGQ-0056
    """
    _name = 'data.quality.profile.compare.wizard'
    _description = 'Profile Comparison'

    profile_id = fields.Many2one(
        'data.quality.profile', string='Profile', required=True, ondelete='cascade',
        domain=[('state', '=', 'done')],
        default=lambda self: self.env.context.get('active_id')
        if self.env.context.get('active_model') == 'data.quality.profile' else False)
    compare_profile_id = fields.Many2one(
        'data.quality.profile', string='Earlier Profile', required=True, ondelete='cascade',
        domain="[('model_id', '=', model_id), ('company_id', '=', company_id), "
               "('state', '=', 'done'), ('id', '<', profile_id)]",
        default=lambda self: self.env['data.quality.profile'].browse(
            self.env.context.get('active_id')).compare_profile_id.id
        if self.env.context.get('active_model') == 'data.quality.profile' else False,
        help="A finished profile of the same model and company, made before the profile.")
    model_id = fields.Many2one(related='profile_id.model_id')
    company_id = fields.Many2one(related='profile_id.company_id')
    line_ids = fields.One2many(
        'data.quality.profile.compare.line', 'wizard_id', string='Fields',
        compute='_compute_line_ids', store=True, readonly=True)

    @api.constrains('profile_id', 'compare_profile_id')
    def _check_profiles(self):
        """Both profiles are finished and of the same model and company; the other is the earlier one.

        Requirements: FR-DGQ-0056
        """
        for wizard in self:
            one, other = wizard.profile_id, wizard.compare_profile_id
            if one.model_id != other.model_id or one.company_id != other.company_id:
                raise ValidationError(self.env._(
                    "A profile can only be compared with another profile of the same model and company."))
            if one.state != 'done' or other.state != 'done' or other.id >= one.id:
                raise ValidationError(self.env._(
                    "Only finished profiles can be compared, and the other one must be the earlier one."))

    @api.depends('profile_id', 'compare_profile_id')
    def _compute_line_ids(self):
        """One line per field of either profile: the change of the empty share, values and shapes.

        Requirements: FR-DGQ-0056
        """
        for wizard in self:
            lines = [(5, 0, 0)]
            if wizard.profile_id and wizard.compare_profile_id:
                lines += [(0, 0, row) for row in profile_compare.compare_profiles(
                    self.env, wizard.profile_id, wizard.compare_profile_id)]
            wizard.line_ids = lines


class DataQualityProfileCompareLine(models.TransientModel):
    """The change of one field between two profiles.

    Requirements: FR-DGQ-0056
    """
    _name = 'data.quality.profile.compare.line'
    _description = 'Profile Comparison Line'
    _order = 'field_name'

    wizard_id = fields.Many2one('data.quality.profile.compare.wizard', required=True, ondelete='cascade')
    field_id = fields.Many2one('ir.model.fields', string='Field', ondelete='cascade')
    field_name = fields.Char(string='Technical Name')
    status = fields.Selection([
        ('changed', 'Changed'), ('same', 'Unchanged'),
        ('added', 'Only in the profile'), ('dropped', 'Only in the earlier profile'),
    ], string='Status')
    empty_share_old = fields.Float(string='Empty Before (%)')
    empty_share_new = fields.Float(string='Empty Now (%)')
    empty_share_change = fields.Float(string='Empty Change (points)')
    distinct_old = fields.Integer(string='Distinct Before')
    distinct_new = fields.Integer(string='Distinct Now')
    distinct_change = fields.Integer(string='Distinct Change')
    shapes_change = fields.Char(string='Shapes')
    numbers_change = fields.Char(string='Numbers')
