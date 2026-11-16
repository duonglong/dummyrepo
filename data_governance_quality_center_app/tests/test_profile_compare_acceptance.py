from odoo.tests import Form, tagged

from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestProfileCompareAcceptance(M3Case):
    """Acceptance tests of the comparison of two profiles.

    Requirements: FR-DGQ-0056
    The dialog is opened with ``Form`` in the context of ``action_compare`` of the later profile;
    the Compare button itself is left to the screen review.
    """

    def contacts(self, empty, filled, prefix):
        self.env['res.partner'].create(
            [{'name': '%s e%s' % (prefix, n), 'company_id': self.cx.id} for n in range(empty)]
            + [{'name': '%s f%s' % (prefix, n), 'email': '%s%s@example.com' % (prefix, n),
                'company_id': self.cx.id} for n in range(filled)])

    def compare(self, profile, earlier):
        action = profile.action_compare()
        with Form(self.env[action['res_model']].with_context(**action['context'])) as form:
            if form.compare_profile_id != earlier:
                form.compare_profile_id = earlier
        return form.save()

    def test_email_empty_share_down_by_17_points(self):
        """SC-DGQ-0056-1: e-mail 25 percent empty, later 8 percent: empty share down by 17 points."""
        self.isolate('res.partner')
        self.contacts(25, 75, 'a')
        old = self.run_profile(self.make_profile('res.partner', ['email']))
        self.env['res.partner'].search([('name', '=like', 'a e%')], limit=17).write({'email': 'y@example.com'})
        new = self.run_profile(self.make_profile('res.partner', ['email']))
        wizard = self.compare(new, old)
        self.env.flush_all()
        line = wizard.line_ids.filtered(lambda l: l.field_name == 'email')
        self.assertEqual(len(line), 1)
        self.assertAlmostEqual(line.empty_share_old, 25.0)
        self.assertAlmostEqual(line.empty_share_new, 8.0)
        self.assertAlmostEqual(line.empty_share_change, -17.0)

    def test_field_in_one_profile_only_is_marked_not_changed(self):
        """FR-DGQ-0056: a field present in only one profile is marked as such."""
        self.isolate('res.partner')
        self.contacts(1, 1, 'b')
        old = self.run_profile(self.make_profile('res.partner', ['email', 'phone']))
        new = self.run_profile(self.make_profile('res.partner', ['email', 'city']))
        wizard = self.compare(new, old)
        status = {l.field_name: l.status for l in wizard.line_ids}
        self.assertEqual(status['phone'], 'dropped')
        self.assertEqual(status['city'], 'added')
        self.assertEqual(status['email'], 'same')
