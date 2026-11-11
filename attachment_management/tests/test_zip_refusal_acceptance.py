"""Acceptance tests of T-009: refuse a zip that holds a file the user cannot open.

Requirements: FR-AM-0010 (US-AM-004).

The spec names no method or route. As in the zip tests of T-004, the entry
point is the "Download as zip" server action bound to ir.attachment, run as
the user on the selected ids. The action has no form or dialog of its own,
so there is no form step to drive. A "non-openable" attachment is one on a
record the plain internal user cannot read (an ir.config_parameter).
"""
import io
import os
import zipfile

from odoo.exceptions import AccessError
from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestZipRefusalAcceptance(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='am_refusal_user', groups='base.group_user')
        cls.partner = cls.env['res.partner'].create({'name': 'Refusal Partner AM'})
        cls.hidden = cls.env['ir.config_parameter'].sudo().create(
            {'key': 'am.refusal.hidden', 'value': '1'})

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
        action = self._zip_action().with_user(self.user).with_context(
            active_model='ir.attachment', active_ids=attachments.ids,
            active_id=attachments[:1].id)
        return action.run()

    def _records(self):
        return self.env['attachment.zip.download'].sudo().search(
            [('user_id', '=', self.user.id)])

    def _assert_refused(self, attachments, message):
        before = self._records()
        with self.assertRaises(AccessError, msg=message):
            self._request(attachments)
        self.env.invalidate_all()
        new = self._records() - before
        for record in new:
            self.assertFalse(record.file_path and os.path.exists(record.file_path),
                             "A zip file was written for a refused request")
            self.assertNotIn(record.state, ('ready', 'downloaded'),
                             "A refused request produced a deliverable zip")

    def test_sc_0010_1_one_openable_one_not_is_refused(self):
        """Requirements: FR-AM-0010. SC-AM-0010-1: a request naming one attachment the user
        can open and one they cannot is refused with an access error."""
        ok = self._attach('mine.txt', b'mine')
        secret = self._attach('secret.txt', b'secret', record=self.hidden)
        self._assert_refused(ok + secret, "The mixed request was not refused")

    def test_sc_0010_1_no_zip_is_delivered(self):
        """Requirements: FR-AM-0010. SC-AM-0010-1: And no zip is delivered; the openable file
        is not handed over alone and no download link can be opened."""
        ok = self._attach('mine2.txt', b'mine2')
        secret = self._attach('secret2.txt', b'secret2', record=self.hidden)
        before = self._records()
        result = None
        try:
            result = self._request(ok + secret)
        except AccessError:
            pass
        self.assertIsNone(result, "The refused request answered an action")
        self.env.invalidate_all()
        for record in self._records() - before:
            self.authenticate(self.user.login, self.user.login)
            response = self.url_open('/attachment_management/zip/%d' % record.id, timeout=60)
            self.assertFalse(
                response.status_code == 200 and zipfile.is_zipfile(io.BytesIO(response.content)),
                "A zip was delivered for a refused request")

    def test_sc_0010_1_order_does_not_matter(self):
        """Requirements: FR-AM-0010. The non-openable attachment named first still refuses
        the whole request."""
        ok = self._attach('mine3.txt', b'mine3')
        secret = self._attach('secret3.txt', b'secret3', record=self.hidden)
        self._assert_refused(secret + ok, "The request naming the hidden file first was not refused")

    def test_fr_0010_only_non_openable_is_refused(self):
        """Requirements: FR-AM-0010. A request naming only a non-openable attachment is refused."""
        secret = self._attach('secret4.txt', b'secret4', record=self.hidden)
        self._assert_refused(secret, "A request for a hidden file alone was not refused")

    def test_fr_0010_many_openable_one_hidden_is_refused(self):
        """Requirements: FR-AM-0010. One non-openable file among many openable ones refuses
        everything: files are not silently dropped."""
        oks = self.env['ir.attachment']
        for i in range(5):
            oks += self._attach('many_%d.txt' % i, b'many-%d' % i)
        secret = self._attach('secret5.txt', b'secret5', record=self.hidden)
        self._assert_refused(oks + secret, "Hidden file among many was dropped silently")

    def test_fr_0010_all_openable_is_not_refused(self):
        """Requirements: FR-AM-0010. Control: with only openable attachments the request is
        accepted and answers a download action (the refusal is not blanket)."""
        ok = self._attach('fine.txt', b'fine') + self._attach('fine2.txt', b'fine2')
        result = self._request(ok)
        self.assertTrue(isinstance(result, dict) and result.get('url'),
                        "An all-openable request must answer a download action")
