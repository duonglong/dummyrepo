"""Acceptance tests of T-003: preview PDF and image, download for the rest.

Requirements: FR-AM-0006, FR-AM-0007.

The spec names no method or view id, so the observable is the form of an
attachment opened from the central search (the Attachments menu action):
its arch must hold a Preview action and a Download action, and the Preview
action's `invisible` condition, evaluated on the record, decides whether it
is shown. What the click does (Odoo's file viewer opening in the browser, no
file downloaded) is JavaScript and is left to the build.
"""
import base64

from lxml import etree

from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

SPREADSHEET = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
WORD = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


@tagged('post_install', '-at_install')
class TestPreviewAcceptance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='am_prev_user', groups='base.group_user')
        cls.partner = cls.env['res.partner'].create({'name': 'Preview Partner AM'})

    def _attach(self, name, mimetype):
        return self.env['ir.attachment'].sudo().create({
            'name': name,
            'mimetype': mimetype,
            'raw': b'am-preview-' + name.encode(),
            'res_model': 'res.partner',
            'res_id': self.partner.id,
        })

    def _form_arch(self):
        menus = self.env['ir.ui.menu'].sudo().search([('name', '=', 'Attachments')]).filtered(
            lambda m: m.action and m.action._name == 'ir.actions.act_window'
            and m.action.res_model == 'ir.attachment'
            and not (m.get_external_id().get(m.id) or '').startswith('base.'))
        self.assertTrue(menus, "No Attachments menu of the module opens ir.attachment")
        action = menus[0].action
        form_view = action.view_ids.filtered(lambda v: v.view_mode == 'form')[:1].view_id
        view = self.env['ir.attachment'].with_user(self.user).get_view(
            view_id=form_view.id or False, view_type='form')
        return etree.fromstring(view['arch'])

    def _find_action(self, arch, word):
        found = []
        for node in arch.iter('button', 'a'):
            label = ' '.join(filter(None, [node.get('string'), node.get('name'), node.text,
                                           node.get('title')])).lower()
            if word in label:
                found.append(node)
        return found

    def _shown(self, node, attachment):
        invisible = node.get('invisible')
        if not invisible:
            return True
        values = attachment.with_user(self.user).read(
            ['name', 'mimetype', 'type', 'url', 'res_model', 'res_id', 'file_type_group'])[0]
        values['id'] = attachment.id
        return not safe_eval(invisible, values)

    def test_sc_0006_1_preview_action_shown_for_pdf(self):
        """Requirements: FR-AM-0006. SC-AM-0006-1: a PDF the user can open offers Preview."""
        arch = self._form_arch()
        previews = self._find_action(arch, 'preview')
        self.assertTrue(previews, "The attachment form has no Preview action")
        pdf = self._attach('am_prev.pdf', 'application/pdf')
        self.assertTrue(any(self._shown(n, pdf) for n in previews),
                        "Preview must be shown for a PDF")

    def test_fr_0006_preview_action_shown_for_image(self):
        """Requirements: FR-AM-0006. An image offers Preview as well."""
        previews = self._find_action(self._form_arch(), 'preview')
        self.assertTrue(previews, "The attachment form has no Preview action")
        img = self._attach('am_prev.png', 'image/png')
        self.assertTrue(any(self._shown(n, img) for n in previews),
                        "Preview must be shown for an image")

    def test_sc_0007_1_no_preview_for_spreadsheet_but_download(self):
        """Requirements: FR-AM-0007. SC-AM-0007-1: a spreadsheet shows no Preview
        action and does show a Download action."""
        arch = self._form_arch()
        sheet = self._attach('am_prev.xlsx', SPREADSHEET)
        previews = self._find_action(arch, 'preview')
        self.assertTrue(previews, "The attachment form has no Preview action to hide")
        self.assertFalse(any(self._shown(n, sheet) for n in previews),
                         "Preview must not be shown for a spreadsheet")
        downloads = self._find_action(arch, 'download')
        self.assertTrue(downloads, "The attachment form has no Download action")
        self.assertTrue(any(self._shown(n, sheet) for n in downloads),
                        "Download must be shown for a spreadsheet")

    def test_fr_0007_no_preview_for_word_or_unknown_type(self):
        """Requirements: FR-AM-0007. Any file that is neither PDF nor image: no Preview."""
        previews = self._find_action(self._form_arch(), 'preview')
        self.assertTrue(previews, "The attachment form has no Preview action to hide")
        for name, mimetype in (('am_prev.docx', WORD), ('am_prev.bin', 'application/octet-stream')):
            att = self._attach(name, mimetype)
            self.assertFalse(any(self._shown(n, att) for n in previews),
                             "Preview must not be shown for %s" % mimetype)
