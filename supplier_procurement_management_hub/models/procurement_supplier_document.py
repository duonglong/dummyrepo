from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services import supplier_control

# Fields of the review: only a Buyer or Procurement manager writes them.
SPM_REVIEW_FIELDS = ('verification_state', 'reviewer_id', 'review_date', 'review_comment')
SPM_REVIEWED_STATES = ('verified', 'rejected')


class ProcurementSupplierDocument(models.Model):
    """A supplier document with file, dates, verification and validity.

    Requirements: FR-SPM-0007, FR-SPM-0008
    """
    _name = 'procurement.supplier.document'
    _description = 'Supplier Document'
    _order = 'partner_id, document_type_id, id desc'

    partner_id = fields.Many2one(
        'res.partner', string="Supplier", index=True, ondelete='restrict')
    application_id = fields.Many2one(
        'procurement.supplier.application', string="Application", index=True, ondelete='restrict')
    document_type_id = fields.Many2one(
        'procurement.document.type', string="Document type", required=True, ondelete='restrict')
    attachment_id = fields.Many2one(
        'ir.attachment', string="File", ondelete='set null', copy=False)
    file_name = fields.Char(related='attachment_id.name', string="File name")
    issue_date = fields.Date()
    expiry_date = fields.Date()
    verification_state = fields.Selection([
        ('to_verify', 'To verify'),
        ('verified', 'Verified'),
        ('rejected', 'Rejected'),
    ], string="Verification", required=True, default='to_verify', index=True, copy=False)
    reviewer_id = fields.Many2one('res.users', string="Reviewed by", readonly=True, copy=False)
    review_date = fields.Datetime(string="Reviewed on", readonly=True, copy=False)
    review_comment = fields.Text(string="Reviewer's comment", copy=False)
    is_current = fields.Boolean(string="Current", default=True, index=True, copy=False)
    previous_version_id = fields.Many2one(
        'procurement.supplier.document', string="Replaces", copy=False, index='btree_not_null',
        ondelete='set null', readonly=True)
    replaced_date = fields.Datetime(string="Replaced on", copy=False, readonly=True)
    uploaded_from_portal = fields.Boolean(string="Uploaded in the portal", copy=False, readonly=True)
    validity_state = fields.Selection([
        ('valid', 'Valid'),
        ('expiring', 'Expiring'),
        ('to_verify', 'To verify'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired'),
    ], string="Validity", compute='_compute_validity_state', search='_search_validity_state')

    @api.constrains('issue_date', 'expiry_date')
    def _check_dates(self):
        """Requirements: FR-SPM-0007"""
        for document in self:
            if document.issue_date and document.expiry_date and document.expiry_date < document.issue_date:
                raise ValidationError(self.env._("The expiry date is before the issue date."))

    @api.constrains('verification_state', 'review_comment')
    def _check_rejected_comment(self):
        """Requirements: FR-SPM-0007"""
        for document in self:
            if document.verification_state == 'rejected' and not (document.review_comment or '').strip():
                raise ValidationError(self.env._("A rejected document needs a comment."))

    @api.depends('verification_state', 'expiry_date', 'document_type_id.reminder_days')
    @api.depends_context('company', 'uid')
    def _compute_validity_state(self):
        """Requirements: FR-SPM-0008"""
        today = supplier_control.company_today(self.env)
        for document in self:
            document.validity_state = supplier_control.document_validity(
                document.verification_state, document.expiry_date,
                document.document_type_id.reminder_days, today)

    def _search_validity_state(self, operator, value):
        """Requirements: FR-SPM-0008

        The state depends on today and on the reminder period of the type, so
        it is computed for the documents in play and not stored.
        """
        if operator not in ('=', '!=', 'in', 'not in'):
            raise ValidationError(self.env._("Unsupported search on the document validity."))
        values = {value} if isinstance(value, str) else set(value)
        matching = self.search([]).filtered(
            lambda document: (document.validity_state in values) == (operator in ('=', 'in')))
        return [('id', 'in', matching.ids)]

    # --- review rights --------------------------------------------------

    def _spm_check_review_allowed(self, vals):
        """Refuse a review field written by anyone but a Buyer or Procurement manager."""
        if self.env.su or not any(name in vals for name in SPM_REVIEW_FIELDS):
            return
        if not self.env.user.has_group('supplier_procurement_management_hub.group_spm_buyer'):
            raise AccessError(self.env._("Only a Buyer or a Procurement manager can verify a document."))

    def _spm_review_values(self, vals):
        """Return ``vals`` with the reviewer and date of a verified or rejected state."""
        state = vals.get('verification_state')
        if not state:
            return vals
        if state in SPM_REVIEWED_STATES:
            return dict(vals, reviewer_id=self.env.user.id, review_date=fields.Datetime.now())
        return dict(vals, reviewer_id=False, review_date=False)

    def _spm_bind_attachments(self):
        """Bind a loose file to its document so every reader of the document opens it.

        A file without a record is readable by its uploader only (native rule), so
        only the acting user's own loose file is bound, as the acting user. A file
        bound to another record is refused.
        """
        for document in self:
            attachment = document.attachment_id
            if not attachment or self.env.su:
                if attachment and not (attachment.res_model and attachment.res_id):
                    attachment.write({'res_model': self._name, 'res_id': document.id})
                continue
            if attachment.res_model == self._name and attachment.res_id == document.id:
                continue
            if attachment.res_model or attachment.res_id or attachment.create_uid != self.env.user:
                raise AccessError(self.env._("You can only attach a file you uploaded yourself."))
            attachment.write({'res_model': self._name, 'res_id': document.id})

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0007"""
        for vals in vals_list:
            self._spm_check_review_allowed(vals)
        documents = super().create([self._spm_review_values(vals) for vals in vals_list])
        documents._spm_bind_attachments()
        return documents

    def write(self, vals):
        """Requirements: FR-SPM-0007"""
        self._spm_check_review_allowed(vals)
        result = super().write(self._spm_review_values(vals))
        if 'attachment_id' in vals:
            self._spm_bind_attachments()
        return result
