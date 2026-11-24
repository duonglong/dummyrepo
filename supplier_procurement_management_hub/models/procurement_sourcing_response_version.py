from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import naming


class ProcurementSourcingResponseVersion(models.Model):
    """One submitted quotation version of a supplier; never changed after its submission.

    Requirements: FR-SPM-0020, FR-SPM-0021, FR-SPM-0034
    Only the current mark moves from one version to the next.
    """
    _name = 'procurement.sourcing.response.version'
    _description = 'Quotation Version'
    _order = 'response_id, version desc'

    response_id = fields.Many2one(
        'procurement.sourcing.response', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='response_id.company_id', store=True, index=True)
    version = fields.Integer(required=True)
    submitted_date = fields.Datetime(required=True, default=fields.Datetime.now)
    submitted_by_id = fields.Many2one('res.users', string="Submitted by", ondelete='set null')
    entered_by_buyer = fields.Boolean(string="Entered by the buyer")
    is_current = fields.Boolean(string="Current", index=True)
    currency_id = fields.Many2one('res.currency', required=True)
    payment_term_id = fields.Many2one('account.payment.term', string="Payment terms")
    payment_days = fields.Integer()
    validity_date = fields.Date(string="Valid until")
    warranty_months = fields.Integer()
    notes = fields.Text()
    attachment_ids = fields.Many2many(
        'ir.attachment', 'procurement_sourcing_response_version_attachment_rel',
        'version_id', 'attachment_id', string="Attachments")
    line_ids = fields.One2many('procurement.sourcing.response.line', 'version_id', string="Lines")
    amount_untaxed = fields.Monetary(compute='_compute_amounts', store=True)
    amount_total = fields.Monetary(compute='_compute_amounts', store=True)

    _version_unique = models.Constraint(
        'UNIQUE(response_id, version)', 'A version number is used once per response.')

    @api.depends('response_id', 'version')
    def _compute_display_name(self):
        """Requirements: FR-SPM-0035

        The response it belongs to and its version number.
        """
        for version in self:
            number = version.version and f"v{version.version}"
            version.display_name = naming.join_name([version.response_id.display_name, number], version._description)

    @api.depends('line_ids.price_subtotal', 'line_ids.tax_ids', 'currency_id')
    def _compute_amounts(self):
        for version in self:
            untaxed = total = 0.0
            for line in version.line_ids:
                taxes = line.tax_ids.compute_all(
                    line.price_subtotal, currency=version.currency_id, quantity=1.0)
                untaxed += taxes['total_excluded']
                total += taxes['total_included']
            version.amount_untaxed = untaxed
            version.amount_total = total

    def write(self, vals):
        """Refuse every change except the current mark.

        Requirements: FR-SPM-0021
        """
        if set(vals) - {'is_current'}:
            raise UserError(self.env._("A submitted quotation version cannot be changed."))
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0021 (refused for everyone)."""
        raise UserError(self.env._("A submitted quotation version cannot be deleted."))


class ProcurementSourcingResponseLine(models.Model):
    """The offer of a supplier for one line the buyer asked for; fixed with its version.

    Requirements: FR-SPM-0020, FR-SPM-0021
    The asked quantity is copied, so a later change of the event line cannot move a price.
    """
    _name = 'procurement.sourcing.response.line'
    _description = 'Quotation Version Line'
    _order = 'version_id, id'

    version_id = fields.Many2one(
        'procurement.sourcing.response.version', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='version_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='version_id.currency_id', store=True)
    event_line_id = fields.Many2one(
        'procurement.sourcing.event.line', required=True, ondelete='cascade', index=True)
    quantity = fields.Float(digits='Product Unit', required=True)
    uom_id = fields.Many2one('uom.uom', string="Unit")
    price_unit = fields.Monetary(required=True)
    discount = fields.Float(string="Discount (%)")  # sdd-allow: money-float a percentage between 0 and 100, not an amount
    tax_ids = fields.Many2many(
        'account.tax', 'procurement_sourcing_response_line_tax_rel', 'line_id', 'tax_id', string="Taxes")
    lead_time_days = fields.Integer()
    price_subtotal = fields.Monetary(compute='_compute_price_subtotal', store=True)
    awarded = fields.Boolean(copy=False)

    _version_event_line_unique = models.Constraint(
        'UNIQUE(version_id, event_line_id)', 'A quotation version answers a line once.')
    _price_not_negative = models.Constraint('CHECK(price_unit >= 0)', 'The price cannot be negative.')
    _discount_range = models.Constraint(
        'CHECK(discount >= 0 AND discount <= 100)', 'The discount is between 0 and 100 percent.')
    _lead_time_not_negative = models.Constraint(
        'CHECK(lead_time_days >= 0)', 'The lead time cannot be negative.')

    @api.depends('quantity', 'price_unit', 'discount')
    def _compute_price_subtotal(self):
        for line in self:
            line.price_subtotal = line.quantity * line.price_unit * (1 - line.discount / 100.0)

    @api.model_create_multi
    def create(self, vals_list):
        """Lines exist only with the version being submitted."""
        if not self.env.context.get('spm_new_version'):
            raise UserError(self.env._("The lines of a quotation version are set when it is submitted."))
        return super().create(vals_list)

    def write(self, vals):
        """Refuse every change after the submission (the award mark aside).

        Requirements: FR-SPM-0021
        """
        if set(vals) - {'awarded'}:
            raise UserError(self.env._("A submitted quotation version cannot be changed."))
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0021 (refused for everyone)."""
        raise UserError(self.env._("A submitted quotation version cannot be changed."))


class ProcurementQuotationEntry(models.TransientModel):
    """The dialog where the Buyer types a quotation received by e-mail or phone.

    Requirements: FR-SPM-0034
    A line is quoted when its Quoted mark is set or it has a price; the rest are left out.
    """
    _name = 'procurement.quotation.entry'
    _description = 'Enter Quotation'

    response_id = fields.Many2one('procurement.sourcing.response', required=True, readonly=True)
    line_ids = fields.One2many('procurement.quotation.entry.line', 'entry_id', string="Lines")
    payment_term_id = fields.Many2one('account.payment.term', string="Payment terms")
    payment_days = fields.Integer()
    validity_date = fields.Date(string="Valid until")
    warranty_months = fields.Integer()
    notes = fields.Text()
    attachment_ids = fields.Many2many('ir.attachment', string="Attachments")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        response = self.env['procurement.sourcing.response'].browse(values.get('response_id'))
        if 'line_ids' in fields_list and response:
            values['line_ids'] = [(0, 0, {'event_line_id': line.id}) for line in response.event_id.line_ids]
        return values

    def action_confirm(self):
        """Store the typed offer as a new version entered by the buyer.

        Requirements: FR-SPM-0034
        """
        self.ensure_one()
        quoted = self.line_ids.filtered(lambda line: line.quoted or line.price_unit)
        self.response_id.spm_enter_quotation(
            [{'event_line_id': line.event_line_id.id, 'price_unit': line.price_unit,
              'discount': line.discount, 'lead_time_days': line.lead_time_days,
               'tax_ids': line.tax_ids.ids} for line in quoted],
            attachment_ids=self.attachment_ids.ids,
            payment_term_id=self.payment_term_id.id, payment_days=self.payment_days,
            validity_date=self.validity_date or False,
            warranty_months=self.warranty_months, notes=self.notes or False)
        return {'type': 'ir.actions.act_window_close'}


class ProcurementQuotationEntryLine(models.TransientModel):
    """One line of the quotation entry dialog."""
    _name = 'procurement.quotation.entry.line'
    _description = 'Enter Quotation Line'

    entry_id = fields.Many2one('procurement.quotation.entry', required=True, ondelete='cascade')
    event_line_id = fields.Many2one('procurement.sourcing.event.line', required=True, readonly=True)
    name = fields.Char(related='event_line_id.name')
    quantity = fields.Float(related='event_line_id.quantity', digits='Product Unit')
    currency_id = fields.Many2one(related='event_line_id.event_id.currency_id')
    quoted = fields.Boolean(string="Quoted", help="Tick to record a line offered at a price of zero.")
    price_unit = fields.Monetary()
    discount = fields.Float(string="Discount (%)")  # sdd-allow: money-float a percentage between 0 and 100, not an amount
    tax_ids = fields.Many2many(
        'account.tax', 'procurement_quotation_entry_line_tax_rel', 'line_id', 'tax_id', string="Taxes",
        domain="[('type_tax_use', '=', 'purchase'), ('company_id', 'parent_of', company_id)]")
    company_id = fields.Many2one(related='event_line_id.event_id.company_id')
    lead_time_days = fields.Integer()
