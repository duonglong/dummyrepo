import base64

from odoo import fields, http
from odoo.exceptions import LockError, UserError
from odoo.tools import mimetypes
from odoo.http import request

from ..services import onboarding

ROUTE = onboarding.ONBOARDING_PATH + '<string:token>'
CONTACT_FIELDS = ('name', 'email', 'phone', 'function')
# Empty rows the page offers next to the contacts the application already holds.
SPARE_CONTACT_ROWS = 2
# (field key, application field, label) in the order the page shows them.
TEXT_FIELDS = (
    ('legal_name', 'legal_name', "Legal name"),
    ('phone', 'phone', "Phone"),
    ('website', 'website', "Website"),
    ('vat', 'vat', "Tax identifier"),
    ('street', 'street', "Street"),
    ('street', 'zip', "Zip"),
    ('street', 'city', "City"),
    ('bank', 'bank_account_number', "Bank account"),
    ('bank', 'bank_name', "Bank"),
    ('bank', 'bank_bic', "BIC"),
    ('bank', 'bank_holder', "Account holder"),
)


class SupplierOnboarding(http.Controller):
    """The supplier side of an onboarding application, reached through a single-use link.

    Requirements: FR-SPM-0001, FR-SPM-0002, FR-SPM-0003
    The token is the credential: the application is read and written with system rights
    (NFR-SPM-0002 named place: invitation link) only after the token has been checked, and
    only that application is reachable.
    """

    def _application_of(self, token):
        """Return ``(application, state)`` for ``token``; the state is ok, used, expired or unknown."""
        application = request.env['procurement.supplier.application'].sudo().search(  # sudo: the token is the credential
            [('invitation_token_hash', '=', onboarding.hash_token(token))], limit=1)
        if not application:
            return application, 'unknown'
        state = onboarding.link_state(application)
        if state == 'ok' and application.state not in onboarding.EDITABLE_STATES:
            state = 'used'
        return application, state

    def _log(self, application, action, result, detail=''):
        # Elevated (named place: portal log write): the visitor has no account and the log has no create right.
        request.env['procurement.portal.log'].sudo().create({
            'user_id': request.env.user.id if not request.env.user._is_public() else False,
            'action': action,
            'res_model': application._name if application else False,
            'res_id': application.id if application else False,
            'result': result,
            'detail': detail,
        })

    def _refuse(self, application, state):
        self._log(application, 'denied', 'refused', state)
        messages = {
            'expired': request.env._("This invitation has expired. Please contact your buyer for a new one."),
            'used': request.env._("This invitation link has already been used. Please contact your buyer for a new one."),
            'unknown': request.env._("This invitation link is not valid. Please contact your buyer."),
        }
        return request.render('supplier_procurement_management_hub.onboarding_refused', {
            'message': messages[state]}, status=403)

    def _render_form(self, token, application, errors=None):
        rules = application.spm_field_rules()
        return request.render('supplier_procurement_management_hub.onboarding_form', {
            'application': application,
            'rules': rules,
            'token': token,
            'countries': request.env['res.country'].sudo().search([]),  # sudo: reference list for a visitor
            'categories': request.env['product.category'].sudo().search([]),  # sudo: reference list for a visitor
            'contacts': list(application.contact_ids) + [None] * SPARE_CONTACT_ROWS,
            'required_fields': {onboarding.required_field(key) for key, rule in rules.items()
                                if rule == 'required' and key in onboarding.KEY_FIELDS},
            'required_documents': application.supplier_type_id.required_document_type_ids,
            'uploaded': application.document_ids.document_type_id,
            'text_fields': [item for item in TEXT_FIELDS if rules[item[0]] != 'hidden'],
            'errors': errors or [],
        })

    @http.route(ROUTE, type='http', auth='public', methods=['GET'], website=False, sitemap=False)
    def onboarding_page(self, token, **kw):
        """Show the application the link was made for.

        Requirements: FR-SPM-0001
        """
        application, state = self._application_of(token)
        if state != 'ok':
            return self._refuse(application, state)
        self._log(application, 'view', 'ok')
        return self._render_form(token, application)

    @http.route(ROUTE, type='http', auth='public', methods=['POST'], website=False, sitemap=False, csrf=True)
    def onboarding_save(self, token, **post):
        """Save the supplier's data and uploads, and submit when asked.

        Requirements: FR-SPM-0002, FR-SPM-0003
        """
        application, state = self._application_of(token)
        if state != 'ok':
            return self._refuse(application, state)
        try:
            application.lock_for_update()
        except LockError:
            return self._render_form(token, application, [request.env._(
                "Your application is being saved elsewhere. Please try again in a moment.")])
        errors = self._save(application, post)
        if not errors and 'submit' in post:
            try:
                with request.env.cr.savepoint():
                    application.action_spm_submit()
            except UserError as error:  # the refusal names everything missing
                errors = [str(error)]
            else:
                application.invitation_used_date = fields.Datetime.now()
                self._log(application, 'submit', 'ok')
                return request.render('supplier_procurement_management_hub.onboarding_done', {})
        return self._render_form(token, application, errors)

    def _save(self, application, post):
        """Write the allowed values and the uploads; return the list of refusals."""
        errors = []
        values = {}
        valid_countries = set(request.env['res.country'].sudo().search([]).ids)  # sudo: reference list for a visitor
        for name in application._spm_portal_field_names():
            field = application._fields[name]
            raw = post.get(name)
            if raw is None or field.type == 'many2many':
                continue
            if field.type == 'many2one':
                country_id = int(raw) if raw.isdigit() else False
                if raw and country_id not in valid_countries:
                    errors.append(request.env._("Choose a country from the list."))
                    continue
                values[name] = country_id
            else:
                values[name] = raw.strip() or False
        if application.spm_field_rules()['categories'] != 'hidden':
            ids = [int(i) for i in request.httprequest.form.getlist('product_category_ids') if i.isdigit()]
            values['product_category_ids'] = [(6, 0, ids)]
        try:
            with request.env.cr.savepoint():
                application.write(values)
        except UserError as error:
            errors.append(str(error))
        self._save_contacts(application, post)
        return errors + self._save_documents(application)

    def _save_contacts(self, application, post):
        """Update the contacts the page showed by id, add the new named rows, delete the emptied ones."""
        if application.spm_field_rules()['contacts'] == 'hidden':
            return
        commands = []
        for contact in application.contact_ids:
            row = self._contact_row(post, f'contact_{{}}_{contact.id}')
            commands.append((1, contact.id, row) if row['name'] else (2, contact.id, 0))
        for index in range(SPARE_CONTACT_ROWS):
            row = self._contact_row(post, f'contact_{{}}_new{index}')
            if row['name']:
                commands.append((0, 0, row))
        if commands:
            application.write({'contact_ids': commands})

    @staticmethod
    def _contact_row(post, pattern):
        return {name: (post.get(pattern.format(name)) or '').strip() for name in CONTACT_FIELDS}

    def _save_documents(self, application):
        errors = []
        for doc_type in application.supplier_type_id.required_document_type_ids:
            upload = request.httprequest.files.get(f'document_{doc_type.id}')
            if not upload or not upload.filename:
                continue
            data = upload.read(onboarding.UPLOAD_MAX_BYTES + 1)
            mimetype = mimetypes.guess_mimetype(data)
            if len(data) > onboarding.UPLOAD_MAX_BYTES or mimetype not in onboarding.UPLOAD_MIMETYPES:
                errors.append(request.env._(
                    "%(document)s: only PDF, PNG or JPEG files up to %(size)s MB are accepted.",
                    document=doc_type.name, size=onboarding.UPLOAD_MAX_MB))
                continue
            document = application.document_ids.filtered(lambda doc, dt=doc_type: doc.document_type_id == dt)[:1]
            if not document:
                document = request.env['procurement.supplier.document'].sudo().create({  # sudo: upload after the token check
                    'application_id': application.id, 'document_type_id': doc_type.id})
            attachment = request.env['ir.attachment'].sudo().create({  # sudo: upload after the token check
                'name': upload.filename, 'datas': base64.b64encode(data),
                'res_model': document._name, 'res_id': document.id, 'mimetype': mimetype})
            document.write({'attachment_id': attachment.id, 'verification_state': 'to_verify'})
        return errors
