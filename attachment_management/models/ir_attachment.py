import fnmatch
import logging
from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.fields import Domain

_logger = logging.getLogger(__name__)

LABEL_CHUNK = 1000

# File type groups of FR-AM-0005: (group, mimetype patterns, ``*`` matches any
# characters). The first matching group wins; anything unmatched is ``other``.
TYPE_GROUPS = (
    ('pdf', ('application/pdf',)),
    ('image', ('image/*',)),
    ('document', (
        'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.*',
        'application/vnd.oasis.opendocument.text',
        'application/rtf',
        'text/plain',
    )),
    ('spreadsheet', (
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.*',
        'application/vnd.oasis.opendocument.spreadsheet',
        'text/csv',
    )),
    ('presentation', (
        'application/vnd.ms-powerpoint',
        'application/vnd.openxmlformats-officedocument.presentationml.*',
        'application/vnd.oasis.opendocument.presentation',
    )),
    ('archive', (
        'application/zip',
        'application/x-7z-compressed',
        'application/x-rar-compressed',
        'application/x-tar',
        'application/gzip',
    )),
)
OTHER_GROUP = 'other'


def file_type_group_of(mimetype):
    """Return the file type group code of a mimetype (FR-AM-0005)."""
    mimetype = (mimetype or '').lower()
    for group, patterns in TYPE_GROUPS:
        if any(fnmatch.fnmatchcase(mimetype, pattern) for pattern in patterns):
            return group
    return OTHER_GROUP


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    file_type_group = fields.Selection(
        [('pdf', 'PDF'), ('image', 'Image'), ('document', 'Document'),
         ('spreadsheet', 'Spreadsheet'), ('presentation', 'Presentation'),
         ('archive', 'Archive'), ('other', 'Other')],
        string="File Type", compute='_compute_file_type_group', store=True, index=True)
    res_model_id = fields.Many2one(
        'ir.model', string="Kind of Record", compute='_compute_res_model_id',
        store=True, index=True, ondelete='set null')
    record_label = fields.Char(
        string="Record", readonly=True,
        help="Display name of the record the file belongs to; refreshed every night.")
    checksum = fields.Char(index='btree')

    @api.depends('mimetype')
    def _compute_file_type_group(self):
        """Requirements: FR-AM-0005. Read the file type from the recorded mimetype."""
        for attachment in self:
            attachment.file_type_group = file_type_group_of(attachment.mimetype)

    @api.depends('res_model')
    def _compute_res_model_id(self):
        """Requirements: FR-AM-0004. Link the attachment to the model of its record."""
        for attachment in self:
            attachment.res_model_id = (
                self.env['ir.model']._get_id(attachment.res_model) if attachment.res_model else False)

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, *, active_test=True, bypass_access=False):
        """Requirements: FR-AM-0002, FR-AM-0003, BR-AM-0001, BR-AM-0002, NFR-AM-0001.

        Access itself stays native: the parent record decides, and a file with no
        record is only found by its uploader and administrators.

        Mirror every condition on the kind of record as the same condition on
        ``res_model`` so the native access check stays in SQL (it only does so
        when the domain names the models).
        """
        domain = Domain(domain).map_conditions(self._mirror_res_model_condition)
        if self.env.context.get('am_central_list') and not (self.env.su or bypass_access):
            hidden = self._am_hidden_public_files(domain)
            if hidden:
                domain &= Domain('id', 'not in', hidden.ids)
        return super()._search(
            domain, offset, limit, order, active_test=active_test, bypass_access=bypass_access)

    def _mirror_res_model_condition(self, condition):
        if (
            condition.field_expr != 'res_model_id'
            or condition.operator not in ('=', 'in')
            or not condition.value
        ):
            return condition
        ids = condition.value if isinstance(condition.value, (list, tuple, set, frozenset)) else [condition.value]
        if not all(isinstance(model_id, int) for model_id in ids):
            return condition
        # sudo: model names are not sensitive and ordinary users cannot read ir.model.
        names = self.env['ir.model'].sudo().browse(ids).exists().mapped('model')
        return condition & Domain('res_model', 'in', names)

    @api.model
    def _am_hidden_public_files(self, domain):
        """Requirements: BR-AM-0001, BR-AM-0002. The public files of ``domain`` whose parent the user cannot open.

        Odoo lets every internal user read a file flagged public, whatever its record; the
        central list still follows the record (or, with no record, the uploader).
        """
        # sudo: finds the public files the native check lets through, to judge each one by its parent below.
        public = self.sudo().search(Domain(domain) & Domain('public', '=', True))
        return public - public._am_openable()

    def _am_openable(self):
        """Requirements: BR-AM-0001, BR-AM-0002. The files of ``self`` whose parent the current user can open.

        With a record: the user can read that record. With no record: the user uploaded
        the file or is an administrator. Judged on the parent only, never on the file's
        public flag, and in batches per kind of record.
        """
        user = self.env.user
        by_model = defaultdict(set)
        linked = {}
        openable = set()
        # sudo: the link to the parent must be read to judge it, also for a file the user cannot read natively.
        for attachment in self.sudo():
            if attachment.res_model and attachment.res_id:
                by_model[attachment.res_model].add(attachment.res_id)
                linked[attachment.id] = (attachment.res_model, attachment.res_id)
            elif user._is_system() or attachment.create_uid == user:
                openable.add(attachment.id)
        readable = {}
        for res_model, res_ids in by_model.items():
            if res_model in self.env:
                records = self.env[res_model].with_user(user).browse(res_ids).exists()
                readable[res_model] = set(records._filtered_access('read').ids)
        openable.update(
            attachment_id for attachment_id, (res_model, res_id) in linked.items()
            if res_id in readable.get(res_model, ()))
        return self.filtered(lambda attachment: attachment.id in openable)

    def _am_check_parent_access(self):
        """Requirements: BR-AM-0001, FR-AM-0010. Refuse when a file's parent record is not open to the current user."""
        if self - self._am_openable():
            raise AccessError(self.env._("Sorry, you are not allowed to access this document."))

    def _read_format(self, fnames, load='_classic_read'):
        """Requirements: BR-AM-0001. Hand out the stored record name only for files whose parent the user can open."""
        result = super()._read_format(fnames, load)
        if 'record_label' in fnames and not self.env.su:
            labelled = [values for values in result if values.get('record_label')]
            openable = self.browse([values['id'] for values in labelled])._am_openable()
            for values in labelled:
                if values['id'] not in openable.ids:
                    values['record_label'] = False
        return result

    @api.model_create_multi
    def create(self, vals_list):
        attachments = super().create(vals_list)
        # Files of picture and binary fields never appear in the central list.
        attachments.filtered(lambda attachment: not attachment.res_field)._refresh_record_labels()
        return attachments

    def write(self, vals):
        result = super().write(vals)
        if 'res_model' in vals or 'res_id' in vals:
            self._refresh_record_labels()
        return result

    @api.model
    def _record_labels_of(self, res_model, res_ids):
        """Return ``{res_id: display name}`` of the records still there, read in one batch."""
        if not res_model or res_model not in self.env:
            return {}
        # sudo: the label is stored once for everyone; the list only shows it for
        # attachments the viewer can already find through the native access check.
        records = self.env[res_model].sudo().browse({res_id for res_id in res_ids if res_id}).exists()
        return {record.id: record.display_name for record in records}

    def _refresh_record_labels(self):
        """Requirements: FR-AM-0004. Store the display name of each record the files belong to.

        The names are read once per model and each distinct name is written once;
        a file with no record (or a record that is gone) gets an empty label.
        """
        for res_model in set(self.mapped('res_model')):
            attachments = self.filtered(lambda attachment: attachment.res_model == res_model)
            labels = self._record_labels_of(res_model, attachments.mapped('res_id'))
            new_label = {attachment.id: labels.get(attachment.res_id, '') for attachment in attachments}
            changed = attachments.filtered(
                lambda attachment: new_label[attachment.id] != (attachment.record_label or ''))
            for label in set(new_label[attachment.id] for attachment in changed):
                group = changed.filtered(lambda attachment: new_label[attachment.id] == label)
                super(IrAttachment, group).write({'record_label': label or False})

    @api.model
    def _refresh_all_record_labels(self, commit_progress=False):
        """Rewrite the labels that differ from the record names, model by model, in chunks.

        With ``commit_progress`` each chunk is committed (cron use), so a job that
        is stopped keeps the labels it already rewrote.
        """
        # sudo: a system job that must see every attachment.
        attachments = self.sudo()
        base_domain = [('res_field', '=', False)]
        # Files whose model is unset or gone still need their stale label cleared.
        res_models = [
            group[0] for group in attachments._read_group(
                base_domain + [('res_model', '!=', False)], groupby=['res_model'])]
        domains = [base_domain + [('res_model', '=', res_model)] for res_model in res_models]
        domains.append(base_domain + [('res_model', '=', False), ('record_label', '!=', False)])
        for domain in domains:
            offset = 0
            while chunk := attachments.search(domain, offset=offset, limit=LABEL_CHUNK, order='id'):
                chunk._refresh_record_labels()
                offset += LABEL_CHUNK
                if commit_progress:
                    self.env['ir.cron']._commit_progress(len(chunk))

    @api.model
    def _cron_refresh_record_labels(self):
        """Requirements: FR-AM-0004. Nightly refresh of the stored record names."""
        self._refresh_all_record_labels(commit_progress=True)
