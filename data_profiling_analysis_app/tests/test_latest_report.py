from lxml import etree

from odoo.tests import tagged

from .common import DpaCase


@tagged('post_install', '-at_install')
class TestLatestReport(DpaCase):
    """Requirements: FR-DPA-0003, FR-DPA-0018."""

    def _run_profile(self):
        profile = self.env['dpa.profile'].create({
            'name': 'Customer quality', 'model_id': self.env['ir.model']._get('res.partner').id})
        profile.with_user(self.member).action_run()
        return profile

    def test_bug_b_0297_latest_report_opens_from_the_profile(self):
        """Regression test for B-0297: the unsaved report of a run opens again from the profile.

        Walk state: created the profile Customer quality, ran it as a member (the report is unsaved), then used the
        Latest report button's method. The test does not render the form.
        Requirements: FR-DPA-0003
        """
        profile = self._run_profile()
        report = self.env['dpa.snapshot'].search([('profile_id', '=', profile.id), ('is_saved', '=', False)])
        self.assertEqual(profile.report_id, report)
        action = profile.with_user(self.member).action_open_report()
        self.assertEqual((action['res_model'], action['res_id']), ('dpa.snapshot', report.id))

    def test_bug_b_0297_saved_report_is_no_longer_the_latest_report(self):
        """Regression test for B-0297: once saved, the report is a snapshot and the button disappears.

        Walk state: ran the profile as a member, saved the report as a snapshot.
        Requirements: FR-DPA-0018
        """
        profile = self._run_profile()
        self.assertTrue(profile.report_id)
        profile.report_id.action_save_snapshot()
        self.assertFalse(profile.report_id)

    def test_bug_b_0297_profile_form_has_the_latest_report_button(self):
        """Regression test for B-0297: the profile form carries a button on the latest report.

        Requirements: FR-DPA-0003
        """
        arch = etree.fromstring(self.env['dpa.profile'].get_view(view_type='form')['arch'])
        self.assertTrue(arch.xpath("//div[@name='button_box']/button[@name='action_open_report']"))
