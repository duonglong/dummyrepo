import os
import zipfile
from unittest.mock import patch

from odoo.addons.attachment_management.models import attachment_zip_download as zip_module
from odoo.addons.attachment_management.models.attachment_zip_download import unique_member_names
from odoo.exceptions import AccessError, UserError

from .common import AttachmentManagementCase


class TestZipDownload(AttachmentManagementCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Zip Unit Partner'})
        cls.Zip = cls.env['attachment.zip.download']

    def _attach(self, name, content=b'data'):
        return self.env['ir.attachment'].create({
            'name': name, 'raw': content, 'res_model': 'res.partner', 'res_id': self.partner.id})

    def _request(self, attachments, user=None):
        return self.Zip.with_user(user or self.am_admin)._request_from_attachments(
            attachments.with_user(user or self.am_admin))

    def _run_build_job(self):
        """Run the build job; the test cursor cannot commit, so the commit of each request is faked."""
        with patch.object(self.env.registry['ir.cron'], '_commit_progress', return_value=1):
            self.Zip._cron_build_queued()

    def test_unique_names_get_a_number_before_the_extension(self):
        """Requirements: FR-AM-0008. Same names are all kept, the later ones numbered."""
        self.assertEqual(
            unique_member_names(['a.pdf', 'a.pdf', 'a.pdf', 'b', 'b']),
            ['a.pdf', 'a (2).pdf', 'a (3).pdf', 'b', 'b (2)'])

    def test_path_separators_in_names_are_replaced(self):
        """Requirements: FR-AM-0008. A name cannot place a member outside the zip root."""
        self.assertEqual(unique_member_names(['../x.txt']), ['.._x.txt'])

    def test_small_selection_is_served_by_link(self):
        """Requirements: FR-AM-0008. Up to the threshold the answer is the download link."""
        result = self._request(self._attach('a.txt'))
        download = self.Zip.search([('user_id', '=', self.am_admin.id)], limit=1)
        self.assertEqual(result['url'], '/attachment_management/zip/%d' % download.id)
        self.assertEqual(download.state, 'ready')

    def test_selection_of_links_only_is_refused(self):
        """Requirements: FR-AM-0008. Link attachments hold no file."""
        link = self.env['ir.attachment'].create({'name': 'site', 'type': 'url', 'url': 'https://example.com'})
        with self.assertRaises(UserError):
            self._request(link)

    def test_empty_selection_is_refused(self):
        """Requirements: FR-AM-0008."""
        with self.assertRaises(UserError):
            self._request(self.env['ir.attachment'])

    def test_unreadable_file_refuses_whole_request(self):
        """Requirements: FR-AM-0010. One file the user cannot open refuses all, nothing is recorded."""
        hidden = self.env['ir.attachment'].create({
            'name': 'secret', 'raw': b's', 'res_model': 'ir.config_parameter',
            'res_id': self.env['ir.config_parameter'].create({'key': 'am.zip.t', 'value': '1'}).id})
        before = self.Zip.search_count([])
        with self.assertRaises(AccessError):
            self._request(self._attach('ok') + hidden, user=self.am_user)
        self.assertEqual(self.Zip.search_count([]), before)

    def test_large_selection_is_queued_then_built_by_the_job(self):
        """Requirements: FR-AM-0008, FR-AM-0009. Above the threshold the zip is queued, built
        by the job as a temporary file and offered as ready."""
        attachments = self._attach('big1.txt', b'one') + self._attach('big2.txt', b'two')
        with patch.object(zip_module, 'ZIP_INLINE_MAX_BYTES', 0):
            result = self._request(attachments)
        self.assertEqual(result['tag'], 'display_notification')
        download = self.Zip.search([('user_id', '=', self.am_admin.id)], limit=1)
        self.assertEqual(download.state, 'queued')
        self._run_build_job()
        self.assertEqual(download.state, 'ready')
        path = download.sudo().file_path
        self.assertTrue(os.path.exists(path))
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(sorted(archive.namelist()), ['big1.txt', 'big2.txt'])

    def test_failed_build_is_recorded(self):
        """Requirements: FR-AM-0008. A file missing from the store fails the whole zip, no partial zip is kept."""
        attachment = self._attach('lost.txt', b'lost')
        with patch.object(zip_module, 'ZIP_INLINE_MAX_BYTES', 0):
            self._request(attachment)
        download = self.Zip.search([('user_id', '=', self.am_admin.id)], limit=1)
        os.unlink(attachment._full_path(attachment.store_fname))
        self._run_build_job()
        self.assertEqual(download.state, 'failed')
        self.assertIn('lost.txt', download.error)
        self.assertFalse(download.sudo().file_path)

    def test_cleanup_removes_downloaded_requests(self):
        """Requirements: FR-AM-0009. A downloaded request is removed by the clean-up job."""
        self._request(self._attach('d.txt'))
        download = self.Zip.search([('user_id', '=', self.am_admin.id)], limit=1)
        download.state = 'downloaded'
        self.Zip._cron_cleanup()
        self.assertFalse(download.exists())
