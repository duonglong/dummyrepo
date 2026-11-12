from .common import FvfCommon


class TestSmoke(FvfCommon):

    def test_module_installed(self):
        """Requirements: NFR-REL-0001 (the module installs cleanly)."""
        module = self.env['ir.module.module'].search(
            [('name', '=', 'form_view_field_organizer')])
        self.assertEqual(module.state, 'installed')

    def test_fixtures_exist(self):
        """Requirements: NFR-REL-0001 (shared fixtures are available)."""
        self.assertTrue(self.group_b.all_implied_ids & self.group_a)
        self.assertIn(self.group_a, self.user_anna.group_ids)
        self.assertNotIn(self.group_a, self.user_ben.group_ids)
        self.assertTrue(self.user_admin.has_group('base.group_system'))
        self.assertEqual(len(self.company_be | self.company_fr), 2)
