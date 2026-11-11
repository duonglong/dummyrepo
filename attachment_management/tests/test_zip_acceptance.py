"""Acceptance tests of T-004: download a selection as one zip.

Requirements: FR-AM-0008, FR-AM-0009, FR-AM-0010.

The spec names no method or route. The entry point is the "Download as zip"
server action bound to ir.attachment (found by its name and binding); it is
run as the user on the selected ids and must return an act_url action whose
url the user then opens, which is what the browser does. The request record
is attachment.zip.download. Not covered here: the background build above
100 MB and the memory bound (NFR-AM-0002), which need a very large
selection and are left to the build's own tests.
"""
import io
import os
import shutil
import tempfile
import zipfile
from datetime import timedelta

from odoo.exceptions import AccessError
from odoo.fields import Datetime
from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestZipAcceptance(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='am_zip_user', groups='base.group_user')
        cls.partner = cls.env['res.partner'].create({'name': 'Zip Partner AM'})
        cls.hidden = cls.env['ir.config_parameter'].sudo().create(
            {'key': 'am.zip.hidden', 'value': '1'})

    def _attach(self, name, content, record=None):
        record = record or self.partner
        return self.env['ir.attachment'].sudo().create({
            'name': name, 'raw': content,
            'res_model': record._name, 'res_id': record.id})

    def _zip_action(self):
        action = self.env['ir.actions.server'].sudo().search([
            ('binding_model_id.model', '=', 'ir.attachment'),
            ('name', 'ilike', 'zip')], limit=1)
        self.assertTrue(action, "No 'Download as zip' action is bound to attachments")
        return action

    def _request(self, attachments):
        """Run the bound action as the user; return the action it answers."""
        action = self._zip_action().with_user(self.user).with_context(
            active_model='ir.attachment', active_ids=attachments.ids,
            active_id=attachments[:1].id)
        return action.run()

    def _download(self, result):
        self.assertTrue(isinstance(result, dict) and result.get('url'),
                        "The zip action must answer an act_url action, got %r" % (result,))
        self.authenticate(self.user.login, self.user.login)
        return self.url_open(result['url'], timeout=120)

    def _zip_of(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(response.content)),
                        "The answer is not a zip file")
        return zipfile.ZipFile(io.BytesIO(response.content))

    # -- FR-AM-0008 ------------------------------------------------------
    def test_sc_0008_1_three_files_two_same_name(self):
        """Requirements: FR-AM-0008. SC-AM-0008-1: three attachments, two named
        report.pdf: one zip with three files; the second report keeps a suffix."""
        atts = (self._attach('report.pdf', b'one') + self._attach('report.pdf', b'two')
                + self._attach('other.pdf', b'three'))
        archive = self._zip_of(self._download(self._request(atts)))
        names = archive.namelist()
        self.assertEqual(len(names), 3)
        self.assertEqual(len(set(names)), 3, "File names in the zip must be distinct")
        self.assertIn('report.pdf', names)
        self.assertIn('other.pdf', names)
        suffixed = [n for n in names if n not in ('report.pdf', 'other.pdf')]
        self.assertEqual(len(suffixed), 1)
        self.assertTrue(suffixed[0].startswith('report') and suffixed[0].endswith('.pdf'))
        self.assertEqual({archive.read(n) for n in names}, {b'one', b'two', b'three'})

    def test_sc_0008_2_thousand_files_in_one_zip(self):
        """Requirements: FR-AM-0008. SC-AM-0008-2: 1,000 attachments, one zip holding all 1,000."""
        atts = self.env['ir.attachment'].sudo().create([{
            'name': 'bulk_%04d.txt' % i, 'raw': b'am-bulk-%d' % i,
            'res_model': 'res.partner', 'res_id': self.partner.id} for i in range(1000)])
        archive = self._zip_of(self._download(self._request(atts)))
        self.assertEqual(len(archive.namelist()), 1000)

    def test_fr_0008_single_file_selection(self):
        """Requirements: FR-AM-0008. One selected file still comes as a zip with that file."""
        att = self._attach('solo.txt', b'solo-content')
        archive = self._zip_of(self._download(self._request(att)))
        self.assertEqual(archive.namelist(), ['solo.txt'])
        self.assertEqual(archive.read('solo.txt'), b'solo-content')

    # -- FR-AM-0009 ------------------------------------------------------
    def _zip_records(self):
        return self.env['attachment.zip.download'].sudo().search(
            [('user_id', '=', self.user.id)], order='id desc')

    def test_sc_0009_1_temporary_file_gone_after_download(self):
        """Requirements: FR-AM-0009. SC-AM-0009-1: once the download has been served the
        temporary zip file no longer exists on the server."""
        att = self._attach('gone.txt', b'gone')
        result = self._request(att)
        records = self._zip_records()
        self.assertTrue(records, "The zip request was not recorded")
        paths = {records[0].file_path} - {False}
        self._zip_of(self._download(result))
        self.env.invalidate_all()
        record = self._zip_records()[0]
        paths |= {record.file_path} - {False}
        for path in paths:
            self.assertFalse(os.path.exists(path), "Temporary zip %s still exists" % path)
        self.assertEqual(record.state, 'downloaded')

    def test_fr_0009_file_deleted_after_24_hours(self):
        """Requirements: FR-AM-0009. A zip built more than 24 hours ago and never downloaded
        is deleted by the scheduled clean-up; a recent one is kept."""
        att = self._attach('old.txt', b'old')
        folder = self.env['attachment.zip.download'].sudo()
        paths = {}
        for label, age in (('old', timedelta(hours=25)), ('recent', timedelta(hours=1))):
            handle = os.path.join(self._tmpdir(), 'am_%s.zip' % label)
            with open(handle, 'wb') as fh:
                fh.write(b'PK')
            paths[label] = handle
            folder.create({
                'user_id': self.user.id, 'attachment_ids': [(6, 0, att.ids)],
                'zip_name': 'am_%s.zip' % label, 'state': 'ready',
                'file_path': handle, 'ready_date': Datetime.now() - age})
        cron = self.env['ir.cron'].sudo().search(
            [('model_id.model', '=', 'attachment.zip.download')], limit=1)
        self.assertTrue(cron, "No scheduled clean-up of zip downloads")
        cron.method_direct_trigger()
        self.assertFalse(os.path.exists(paths['old']), "A zip older than 24 hours was kept")
        self.assertTrue(os.path.exists(paths['recent']), "A recent zip was removed")

    def _tmpdir(self):
        path = tempfile.mkdtemp(prefix='am_zip_test_')
        self.addCleanup(shutil.rmtree, path, True)
        return path

    # -- FR-AM-0010 ------------------------------------------------------
    def test_sc_0010_1_refused_when_one_file_is_not_openable(self):
        """Requirements: FR-AM-0010. SC-AM-0010-1: a request naming one openable and one
        non-openable attachment is refused with an access error and no zip is delivered."""
        ok = self._attach('mine.txt', b'mine')
        secret = self._attach('secret.txt', b'secret', record=self.hidden)
        try:
            result = self._request(ok + secret)
        except AccessError:
            return
        response = self._download(result) if isinstance(result, dict) and result.get('url') else None
        self.assertTrue(
            response is None or response.status_code != 200
            or not zipfile.is_zipfile(io.BytesIO(response.content)),
            "A zip was delivered although one file is not openable")
        self.fail("The request naming a non-openable attachment was not refused with an access error")
