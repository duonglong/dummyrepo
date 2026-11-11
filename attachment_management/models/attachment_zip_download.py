import logging
import os
import shutil
import tempfile
import time
import zipfile
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.tools import SQL, config

_logger = logging.getLogger(__name__)

ZIP_INLINE_MAX_BYTES = 100 * 1024 * 1024
ZIP_KEEP_HOURS = 24
ZIP_STALE_BUILD_HOURS = 1
ZIP_CHUNK_BYTES = 64 * 1024
ZIP_FOLDER = 'attachment_management_zip'
# Files of these groups are already compressed: store them as they are.
STORED_TYPE_GROUPS = ('pdf', 'image', 'archive')


def unique_member_names(names):
    """Return the file names for a zip: a repeated name gets " (2)", " (3)" before its extension."""
    seen = set()
    result = []
    for name in names:
        name = (name or '').replace('/', '_').replace('\\', '_') or 'file'
        stem, extension = os.path.splitext(name)
        candidate, number = name, 1
        while candidate in seen:
            number += 1
            candidate = '%s (%d)%s' % (stem, number, extension)
        seen.add(candidate)
        result.append(candidate)
    return result


class AttachmentZipDownload(models.Model):
    _name = 'attachment.zip.download'
    _description = "Attachment Zip Download"
    _rec_name = 'zip_name'
    _order = 'id desc'

    user_id = fields.Many2one(
        'res.users', string="Requested by", required=True, default=lambda self: self.env.user,
        index=True, ondelete='cascade')
    attachment_ids = fields.Many2many('ir.attachment', string="Files", required=True)
    zip_name = fields.Char(string="Name", required=True)
    total_size = fields.Integer(string="Size", help="Total size of the files, in bytes.")
    state = fields.Selection(
        [('queued', "Being prepared"), ('building', "Building"), ('ready', "Ready"), ('downloaded', "Downloaded"), ('failed', "Failed")],
        required=True, default='queued', index=True)
    file_path = fields.Char(groups='base.group_system')
    ready_date = fields.Datetime(string="Ready since")
    build_start = fields.Datetime(string="Build started")
    error = fields.Char()
    file_count = fields.Integer(string="Files", compute='_compute_file_count')

    @api.depends('attachment_ids')
    def _compute_file_count(self):
        for download in self:
            # sudo: count every file of the request, also ones the viewer cannot open since.
            download.file_count = len(download.sudo().attachment_ids)  # sudo: see above

    def copy(self, default=None):
        """Requirements: FR-AM-0009. A zip request is a one-off; it is not copied."""
        raise UserError(self.env._("A zip request cannot be duplicated."))

    # -- request ---------------------------------------------------------
    @api.model
    def _selected_attachments(self, attachments):
        """Return the attachments the person selected; a capped selection is resolved from its filter."""
        limit = self.env['ir.config_parameter'].sudo().get_int('web.active_ids_limit', 20000)
        active_domain = self.env.context.get('active_domain')
        if active_domain and len(attachments) >= limit:
            return self.env['ir.attachment'].search(Domain(active_domain))
        return attachments

    @api.model
    def _request_from_attachments(self, attachments):
        """Requirements: FR-AM-0008, FR-AM-0009, FR-AM-0010.

        Record a zip request for the selected files and answer the action that
        delivers it: the download link up to ZIP_INLINE_MAX_BYTES, otherwise a
        notice that the zip is being prepared in the background.
        Any file the person cannot open refuses the whole request.
        """
        attachments = self._selected_attachments(attachments)
        if not attachments:
            raise UserError(self.env._("Select at least one attachment."))
        attachments.check_access('read')
        attachments._am_check_parent_access()
        files = attachments.filtered(lambda attachment: attachment.type == 'binary')
        if not files:
            raise UserError(self.env._("None of the selected attachments holds a file."))
        total_size = sum(files.mapped('file_size'))
        in_request = total_size <= ZIP_INLINE_MAX_BYTES
        download = self.create({
            'attachment_ids': [fields.Command.set(files.ids)],
            'zip_name': self.env._("attachments-%(date)s.zip", date=fields.Date.context_today(self)),
            'total_size': total_size,
            'state': 'ready' if in_request else 'queued',
            'ready_date': fields.Datetime.now() if in_request else False,
        })
        if in_request:
            return download.action_download()
        self.env.ref('attachment_management.ir_cron_am_zip_build')._trigger()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'info',
                'message': self.env._("Your zip is being prepared. Find it under My zip downloads when it is ready."),
                'sticky': False,
            },
        }

    def action_download(self):
        """Requirements: FR-AM-0008. Open the download link of a zip that is ready."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/attachment_management/zip/%d' % self.id,
            'target': 'self',
        }

    def _stream_then_release(self, path):
        """Requirements: FR-AM-0009. Yield the file in chunks; once the last chunk is read, mark
        the request downloaded and delete the file.

        A transfer that is closed before that point leaves the request ready with its file,
        so the person can fetch it again and the 24-hour clean-up still bounds it. The release
        runs just before the last chunk is handed out, so the file is gone when the client has
        received everything.
        """
        # The request cursor is closed when the response is streamed: keep plain values only.
        registry, download_id = self.env.registry, self.id
        with open(path, 'rb') as handle:
            chunk = handle.read(ZIP_CHUNK_BYTES)
            while True:
                following = handle.read(ZIP_CHUNK_BYTES)
                if not following:
                    break
                yield chunk
                chunk = following
        with registry.cursor() as cr:
            # sudo: requesters cannot write their requests; the state is kept by the server.
            api.Environment(cr, SUPERUSER_ID, {})[self._name].browse(download_id).write({'state': 'downloaded'})
        os.unlink(path)
        yield chunk

    # -- build -----------------------------------------------------------
    @api.model
    def _zip_folder(self):
        folder = os.path.join(config['data_dir'], ZIP_FOLDER, self.env.cr.dbname)
        os.makedirs(folder, exist_ok=True)
        return folder

    def _build(self):
        """Requirements: FR-AM-0008, FR-AM-0009, FR-AM-0010, NFR-AM-0002.

        Write the zip of the request to a temporary file, one file at a time, and
        keep its path. Access is checked again as the current user.
        """
        self.ensure_one()
        # sudo: the link table lists every file of the request, also those the user can no
        # longer open; the access check below must see them.
        attachment_ids = self.sudo().attachment_ids.ids
        attachments = self.env['ir.attachment'].browse(attachment_ids)
        attachments.check_access('read')
        attachments._am_check_parent_access()
        started = time.monotonic()
        handle, path = tempfile.mkstemp(suffix='.zip', dir=self._zip_folder())
        os.close(handle)
        try:
            self._write_zip(path, attachments)
        except BaseException:
            os.unlink(path)
            raise
        # sudo: requesters may only create and read their requests; the state is ours to keep.
        self.sudo().write({'file_path': path, 'state': 'ready', 'ready_date': fields.Datetime.now()})
        _logger.info(
            "zip %s: %s files, %s bytes in %.1fs",
            self.id, len(attachment_ids), os.path.getsize(path), time.monotonic() - started)

    def _write_zip(self, path, attachments):
        names = unique_member_names(attachments.mapped('name'))
        with zipfile.ZipFile(path, 'w', allowZip64=True) as archive:
            for attachment_id, member in zip(attachments.ids, names):
                # One record at a time keeps database-stored content out of the cache.
                attachment = self.env['ir.attachment'].browse(attachment_id)
                self._add_member(archive, attachment, member)
                attachment.invalidate_recordset()

    def _add_member(self, archive, attachment, member):
        if attachment.store_fname and not os.path.exists(attachment._full_path(attachment.store_fname)):
            raise UserError(self.env._("File %s could not be read.", attachment.name))
        info = zipfile.ZipInfo(member, date_time=max(attachment.create_date.timetuple()[:6], (1980, 1, 1, 0, 0, 0)))
        info.compress_type = (
            zipfile.ZIP_STORED if attachment.file_type_group in STORED_TYPE_GROUPS else zipfile.ZIP_DEFLATED)
        with attachment.raw.open() as source, archive.open(info, 'w', force_zip64=True) as target:
            shutil.copyfileobj(source, target)

    # -- scheduled jobs --------------------------------------------------
    @api.model
    def _cron_build_queued(self):
        """Requirements: FR-AM-0008, FR-AM-0009. Build the zips queued for the background, one request at a time.

        A request is marked ``building`` and committed before its build, so a build
        killed half way never keeps the queue: it is reset to ``failed`` after
        ZIP_STALE_BUILD_HOURS and the job goes on with the next request.
        """
        self._fail_stale_builds()
        while True:
            self.env.flush_all()  # the next query reads the state in SQL
            # Locked rows are skipped: two workers never build the same request.
            self.env.cr.execute(SQL(
                "SELECT id FROM %s WHERE state = 'queued' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED",
                SQL.identifier(self._table)))
            row = self.env.cr.fetchone()
            if not row:
                return
            download = self.browse(row[0])
            # sudo: see _build.
            download.sudo().write({'state': 'building', 'build_start': fields.Datetime.now()})
            self.env['ir.cron']._commit_progress(0)
            self._build_as_requester(download)
            if not self.env['ir.cron']._commit_progress(1):
                self.env.ref('attachment_management.ir_cron_am_zip_build')._trigger()
                return

    @api.model
    def _fail_stale_builds(self):
        """Requirements: FR-AM-0009. Give up the requests left building for more than ZIP_STALE_BUILD_HOURS."""
        cutoff = fields.Datetime.now() - timedelta(hours=ZIP_STALE_BUILD_HOURS)
        # sudo: a system job that must see every request.
        stale = self.sudo().search([('state', '=', 'building'), ('build_start', '<', cutoff)])
        stale.write({'state': 'failed', 'error': self.env._("The zip could not be prepared in time.")})
        if stale:
            _logger.warning("zip requests %s were left building and are failed", stale.ids)

    @api.model
    def _build_as_requester(self, download):
        """Build one queued zip with the rights of the person who asked for it; any failure is recorded."""
        try:
            with self.env.cr.savepoint():
                download.with_user(download.user_id)._build()
        except Exception as error:  # noqa: BLE001 - one bad request must never block the queue
            _logger.warning("zip %s failed: %s", download.id, error, exc_info=not isinstance(
                error, (UserError, AccessError, OSError)))
            # sudo: see _build.
            download.sudo().write({'state': 'failed', 'error': str(error)})

    @api.model
    def _cron_cleanup(self):
        """Requirements: FR-AM-0009. Delete zips downloaded, failed or older than ZIP_KEEP_HOURS, with their files."""
        cutoff = fields.Datetime.now() - timedelta(hours=ZIP_KEEP_HOURS)
        # sudo: a system job that must see every request.
        expired = self.sudo().search(Domain.OR([
            [('state', 'in', ('downloaded', 'failed'))],
            [('ready_date', '!=', False), ('ready_date', '<', cutoff)],
            [('ready_date', '=', False), ('create_date', '<', cutoff)],
        ]))
        for download in expired:
            if download.file_path and os.path.exists(download.file_path):
                os.unlink(download.file_path)
        expired.unlink()
        self._remove_orphan_files(time.time() - ZIP_KEEP_HOURS * 3600)
        _logger.info("cleanup removed %s zips", len(expired))

    @api.model
    def _remove_orphan_files(self, before):
        """Remove files of the zip folder older than ``before`` (a timestamp), whatever request left them."""
        folder = self._zip_folder()
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.getmtime(path) < before:
                os.unlink(path)
