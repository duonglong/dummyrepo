from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

FOLLOWABLE_TYPES = (
    'char', 'text', 'integer', 'float', 'monetary', 'date', 'datetime',
    'boolean', 'selection', 'html', 'many2one', 'many2many',
)
BOOKKEEPING_FIELDS = tuple(models.MAGIC_COLUMNS) + ('display_name',)


def followable_domain(model_name):
    """Domain on ir.model.fields leaving out the technical fields of a record type."""
    return [
        ('model', '=', model_name),
        ('store', '=', True),
        ('ttype', 'in', FOLLOWABLE_TYPES),
        ('name', 'not in', BOOKKEEPING_FIELDS),
    ]


class RecordChangeField(models.Model):
    _name = 'record.change.field'
    _description = 'Followed or sensitive field'
    _rec_name = 'field_id'

    tracked_model_id = fields.Many2one(
        'record.change.model', string='Record type', required=True, ondelete='cascade', index=True)
    field_id = fields.Many2one(
        'ir.model.fields', string='Field', required=True, ondelete='cascade',
        domain=lambda self: followable_domain(self.tracked_model_id.model_id.model))
    field_label = fields.Char(related='field_id.field_description', string='Label', readonly=True)
    is_followed = fields.Boolean(string='Followed', default=True)
    is_sensitive = fields.Boolean(string='Sensitive', default=False)

    _tracked_model_field_uniq = models.Constraint(
        'unique (tracked_model_id, field_id)', 'A field is set once per record type.')

    @api.constrains('tracked_model_id', 'field_id')
    def _check_field_followable(self):
        """Requirements: FR-RCT-008, FR-RCT-010, BR-RCT-003.

        The field belongs to the record type and is not technical, whatever the way it is entered.
        """
        for line in self:
            ir_field = line.field_id.sudo()
            model_name = line.tracked_model_id.model_id.model
            if ir_field.model != model_name:
                raise ValidationError(_("The field does not belong to the record type."))
            if not ir_field.filtered_domain(followable_domain(model_name)):
                raise ValidationError(_("Technical fields cannot be followed"))
