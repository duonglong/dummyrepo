import os
import time

from odoo.tests.common import HttpCase, new_test_user, tagged

from odoo.addons.attachment_management.models.attachment_zip_download import ZIP_CHUNK_BYTES

BIG_FILE_BYTES = 40 * 1024 * 1024


@tagged('post_install', '-at_install')
class TestZipTransfer(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.zip_user = new_test_user(cls.env, login='am_transfer_user', groups='base.group_user')
        cls.partner = cls.env['res.partner'].create({'name': 'Transfer Partner'})
        cls.Zip = cls.env['attachment.zip.download']

    def _ready_request(self, content):
        """A ready zip request of the user, built by the route on first use."""
        attachment = self.env['ir.attachment'].create({
            'name': 'transfer.bin', 'raw': content, 'res_model': 'res.partner', 'res_id': self.partner.id})
        action = self.env.ref('attachment_management.action_attachment_download_zip')
        result = action.with_user(self.zip_user).with_context(
            active_model='ir.attachment', active_ids=attachment.ids, active_id=attachment.id).run()
        download = self.Zip.sudo().search([('user_id', '=', self.zip_user.id)], order='id desc', limit=1)
        self.assertEqual(download.state, 'ready')
        return download, result['url']

    def _fetch(self, download, url):
        """The route is called as the user; the zip is built there when it is not yet."""
        self.authenticate(self.zip_user.login, self.zip_user.login)
        return self.opener.get(self.base_url() + url, stream=True, timeout=120)

    def _wait_for_state(self, download, state):
        for _ in range(50):
            download.invalidate_recordset()
            if download.state == state:
                return True
            time.sleep(0.1)
        return False

    def test_bug_b_0181_broken_transfer_keeps_request_and_file(self):
        """Regression test for B-0181: a transfer closed by the client midway leaves the request
        ready and its file in place, so the zip can be fetched again.

        Requirements: FR-AM-0008, FR-AM-0009

        It cannot reach a disconnect the server notices only after the last chunk was sent.
        """
        download, url = self._ready_request(os.urandom(BIG_FILE_BYTES))
        response = self._fetch(download, url)
        self.assertEqual(response.status_code, 200)
        next(response.iter_content(ZIP_CHUNK_BYTES))
        response.close()
        time.sleep(1)  # the server notices the closed connection while it writes the next chunks
        download.invalidate_recordset()
        self.assertEqual(download.state, 'ready')
        self.assertTrue(os.path.exists(download.sudo().file_path))
        again = self._fetch(download, url)
        self.assertEqual(again.status_code, 200)
        again.close()

    def test_bug_b_0181_complete_transfer_marks_downloaded_and_deletes_file(self):
        """Regression test for B-0181: a transfer read to the end marks the request downloaded and
        deletes the file.

        Requirements: FR-AM-0009
        """
        download, url = self._ready_request(b'small content')
        self.authenticate(self.zip_user.login, self.zip_user.login)
        response = self.url_open(url, timeout=120)
        self.assertTrue(response.content)
        path = download.sudo().file_path
        self.assertTrue(self._wait_for_state(download, 'downloaded'))
        self.assertFalse(os.path.exists(path))


@tagged('post_install', '-at_install')
class TestZipDownloadRecheck(HttpCase):
    """Requirements: FR-AM-0009, FR-AM-0010. The zip file exists before the link is opened, and opening it
    checks again that the person can still open every file."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.zip_user = new_test_user(cls.env, login='am_recheck_user', groups='base.group_user')
        cls.partner = cls.env['res.partner'].create({'name': 'Recheck Partner'})
        cls.hidden_record = cls.env['ir.config_parameter'].sudo().search([], limit=1)
        cls.Zip = cls.env['attachment.zip.download']

    def setUp(self):
        super().setUp()
        self.attachment = self.env['ir.attachment'].create({
            'name': 'recheck.txt', 'raw': b'recheck', 'res_model': 'res.partner', 'res_id': self.partner.id})

    def _latest_request(self):
        return self.Zip.sudo().search([('user_id', '=', self.zip_user.id)], order='id desc', limit=1)

    def _hide_file_and_open(self, url):
        """Move the file onto a record the user cannot open, then open the link as the user."""
        self.attachment.write({'res_model': 'ir.config_parameter', 'res_id': self.hidden_record.id})
        self.authenticate(self.zip_user.login, self.zip_user.login)
        return self.url_open(url, timeout=120)

    def test_bug_b_0186_small_zip_written_before_the_link_is_opened(self):
        """Regression test for B-0186: a zip up to 100 MB is written to its temporary file when it is asked for.

        Requirements: FR-AM-0009
        """
        action = self.env.ref('attachment_management.action_attachment_download_zip')
        action.with_user(self.zip_user).with_context(
            active_model='ir.attachment', active_ids=self.attachment.ids, active_id=self.attachment.id).run()
        path = self._latest_request().file_path
        self.assertTrue(path and os.path.exists(path), "The zip was not written to a temporary file")

    def test_bug_b_0186_small_zip_refused_when_access_was_lost(self):
        """Regression test for B-0186: a zip written when asked for is still refused at the link
        when the person can no longer open one of its files.

        Requirements: FR-AM-0010
        """
        action = self.env.ref('attachment_management.action_attachment_download_zip')
        result = action.with_user(self.zip_user).with_context(
            active_model='ir.attachment', active_ids=self.attachment.ids, active_id=self.attachment.id).run()
        response = self._hide_file_and_open(result['url'])
        self.assertEqual(response.status_code, 403)

    def test_bug_b_0186_background_zip_refused_when_access_was_lost(self):
        """Regression test for B-0186: a zip prepared in the background is refused at the link
        when the person can no longer open one of its files.

        Requirements: FR-AM-0010
        """
        download = self.Zip.sudo().create({
            'user_id': self.zip_user.id, 'attachment_ids': [(6, 0, self.attachment.ids)],
            'zip_name': 'recheck.zip', 'state': 'queued'})
        self.Zip._build_as_requester(download)  # the job's step for one request, without its commits
        self.assertEqual(download.state, 'ready')
        response = self._hide_file_and_open(download.action_download()['url'])
        self.assertEqual(response.status_code, 403)
