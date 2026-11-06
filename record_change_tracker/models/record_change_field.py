from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

FOLLOWABLE_TYPES = (
    'char', 'text', 'integer', 'float', 'monetary', 'date', 'datetime',
    'boolean', 'selection', 'html', 'many2one', 'many2many',
)
BOOKKEEPING_FIELDS = tuple(models.MAGIC_COLUMNS) + ('display_name', 'access_token')
# Name prefixes of the chatter, activity and portal mixins: logs and bookkeeping, not business data.
MIXIN_FIELD_PREFIXES = ('message_', 'activity_', 'website_message_', 'access_')


def followable_domain(model_name):
    """Domain on ir.model.fields leaving out the technical fields of a record type."""
    return [
        ('model', '=', model_name),
        ('store', '=', True),
        ('ttype', 'in', FOLLOWABLE_TYPES),
        ('name', 'not in', BOOKKEEPING_FIELDS),
    ] + [('name', 'not =like', prefix + '%') for prefix in MIXIN_FIELD_PREFIXES]


class RecordChangeField(models.Model):
    """Requirements: FR-RCT-008, FR-RCT-010, FR-RCT-011, BR-RCT-003, BR-RCT-004.

    One field of a record type, with its followed and sensitive marks.
    """
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

    is_tracked = fields.Boolean(
        string='Recorded', compute='_compute_is_tracked',
        help="Followed and not sensitive: a sensitive field is never recorded.")

    _tracked_model_field_uniq = models.Constraint(
        'unique (tracked_model_id, field_id)', 'A field is set once per record type.')

    @api.depends('is_followed', 'is_sensitive')
    def _compute_is_tracked(self):
        """Requirements: BR-RCT-004. Sensitive wins over followed."""
        for line in self:
            line.is_tracked = line.is_followed and not line.is_sensitive

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
