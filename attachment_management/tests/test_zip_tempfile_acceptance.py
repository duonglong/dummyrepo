"""Acceptance tests of T-008: build large zips as a temporary file.

Requirements: FR-AM-0009 (SC-AM-0009-1).

The spec names no method or route. The entry point is the "Download as zip"
server action bound to ir.attachment, run as the user on the selected ids; it
returns an act_url action the user then opens. The zip request is the
attachment.zip.download record, whose file_path names the temporary file.
No form or dialog is named by the spec, so the action is called directly and
the selection step is left to the build.
"""
import io
import os
import shutil
import tempfile
import zipfile
from datetime import timedelta

from odoo.fields import Datetime
from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestZipTempFileAcceptance(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp_user = new_test_user(cls.env, login='am_t8_zip_user', groups='base.group_user')
        cls.tmp_partner = cls.env['res.partner'].create({'name': 'T8 Zip Partner'})

    def _make(self, name, content):
        return self.env['ir.attachment'].sudo().create({
            'name': name, 'raw': content,
            'res_model': 'res.partner', 'res_id': self.tmp_partner.id})

    def _records(self):
        return self.env['attachment.zip.download'].sudo().search(
            [('user_id', '=', self.tmp_user.id)], order='id desc')

    def test_sc_0009_1_temporary_file_gone_after_download(self):
        """Requirements: FR-AM-0009. SC-AM-0009-1: a user downloaded a zip; once
        the download has been served the temporary zip file no longer exists."""
        att = self._make('t8.txt', b't8 content')
        action = self.env['ir.actions.server'].sudo().search([
            ('binding_model_id.model', '=', 'ir.attachment'),
            ('name', 'ilike', 'zip')], limit=1)
        self.assertTrue(action, "No 'Download as zip' action is bound to attachments")
        result = action.with_user(self.tmp_user).with_context(
            active_model='ir.attachment', active_ids=att.ids, active_id=att.id).run()
        self.assertTrue(isinstance(result, dict) and result.get('url'),
                        "The zip action must return a download link, got %r" % (result,))
        records = self._records()
        self.assertTrue(records, "The zip request was not recorded")
        path = records[0].file_path
        self.assertTrue(path, "The zip was not written to a temporary file")
        self.assertTrue(os.path.exists(path), "The temporary zip file was not written")
        self.authenticate(self.tmp_user.login, self.tmp_user.login)
        response = self.url_open(result['url'], timeout=120)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(response.content)))
        self.assertFalse(os.path.exists(path),
                         "Temporary zip %s still exists after the download" % path)

    def test_fr_0009_unserved_file_deleted_after_24_hours(self):
        """Requirements: FR-AM-0009. A zip built more than 24 hours ago and not
        downloaded is deleted by the scheduled clean-up; a recent one is kept."""
        att = self._make('t8old.txt', b'old')
        folder = tempfile.mkdtemp(prefix='am_t8_')
        self.addCleanup(shutil.rmtree, folder, True)
        paths = {}
        for label, age in (('old', timedelta(hours=25)), ('recent', timedelta(hours=1))):
            paths[label] = os.path.join(folder, 'am_%s.zip' % label)
            with open(paths[label], 'wb') as fh:
                fh.write(b'PK')
            self.env['attachment.zip.download'].sudo().create({
                'user_id': self.tmp_user.id, 'attachment_ids': [(6, 0, att.ids)],
                'zip_name': 'am_%s.zip' % label, 'state': 'ready',
                'file_path': paths[label], 'ready_date': Datetime.now() - age})
        cron = self.env['ir.cron'].sudo().search(
            [('model_id.model', '=', 'attachment.zip.download')], limit=1)
        self.assertTrue(cron, "No scheduled clean-up of zip downloads")
        cron.method_direct_trigger()
        self.assertFalse(os.path.exists(paths['old']), "A zip older than 24 hours was kept")
        self.assertTrue(os.path.exists(paths['recent']), "A recent zip was removed")
