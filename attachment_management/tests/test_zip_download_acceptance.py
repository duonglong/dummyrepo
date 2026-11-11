"""Acceptance tests of T-007: download a selection as one zip.

Requirements: FR-AM-0008 (SC-AM-0008-1, SC-AM-0008-2).

The spec names no method or route. The entry point is the "Download as zip"
server action bound to ir.attachment (found by its name and binding), run as
the user on the selected ids. It must return an act_url action whose url the
user then opens, as the browser does. No form or dialog is named by the spec,
so the method is tested directly and the selection step is left to the build.
"""
import io
import zipfile

from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestZipDownloadAcceptance(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.zip_user = new_test_user(cls.env, login='am_t7_zip_user', groups='base.group_user')
        cls.zip_partner = cls.env['res.partner'].create({'name': 'T7 Zip Partner'})

    def _make(self, name, content):
        return self.env['ir.attachment'].sudo().create({
            'name': name, 'raw': content,
            'res_model': 'res.partner', 'res_id': self.zip_partner.id})

    def _download_zip(self, attachments):
        action = self.env['ir.actions.server'].sudo().search([
            ('binding_model_id.model', '=', 'ir.attachment'),
            ('name', 'ilike', 'zip')], limit=1)
        self.assertTrue(action, "No 'Download as zip' action is bound to attachments")
        result = action.with_user(self.zip_user).with_context(
            active_model='ir.attachment', active_ids=attachments.ids,
            active_id=attachments[:1].id).run()
        self.assertTrue(isinstance(result, dict) and result.get('url'),
                        "The zip action must return a download link, got %r" % (result,))
        self.authenticate(self.zip_user.login, self.zip_user.login)
        response = self.url_open(result['url'], timeout=120)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(response.content)),
                        "The download is not a zip file")
        return zipfile.ZipFile(io.BytesIO(response.content))

    def test_sc_0008_1_duplicate_names_keep_a_suffix(self):
        """Requirements: FR-AM-0008. SC-AM-0008-1: three attachments, two named
        report.pdf; one zip with three files, the second report keeps a number suffix."""
        atts = (self._make('report.pdf', b'first') + self._make('report.pdf', b'second')
                + self._make('summary.pdf', b'third'))
        archive = self._download_zip(atts)
        names = archive.namelist()
        self.assertEqual(len(names), 3)
        self.assertEqual(len(set(names)), 3, "Names inside the zip must be distinct")
        reports = [n for n in names if n.startswith('report')]
        self.assertEqual(len(reports), 2)
        self.assertIn('report.pdf', reports)
        self.assertTrue(all(n.endswith('.pdf') for n in reports))
        self.assertEqual({archive.read(n) for n in names}, {b'first', b'second', b'third'})

    def test_sc_0008_2_thousand_files_no_limit(self):
        """Requirements: FR-AM-0008. SC-AM-0008-2: 1,000 openable attachments
        selected; one zip holding all 1,000 files is delivered."""
        atts = self.env['ir.attachment'].sudo().create([{
            'name': 'many_%04d.txt' % i, 'raw': b't7-%d' % i,
            'res_model': 'res.partner', 'res_id': self.zip_partner.id} for i in range(1000)])
        archive = self._download_zip(atts)
        self.assertEqual(len(archive.namelist()), 1000)
