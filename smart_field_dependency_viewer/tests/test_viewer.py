from odoo.exceptions import AccessError

from .common import SfdCommon


class TestViewer(SfdCommon):
    """Access and screen record of the viewer.

    Requirements: NFR-SEC-0001, FR-SFD-0001
    """

    def test_menu_visible_to_administrator_only(self):
        """NFR-SEC-0001: the viewer menu is restricted to the Settings group."""
        menu = self.env.ref('smart_field_dependency_viewer.sfd_dependency_viewer_menu')
        self.assertEqual(menu.group_ids, self.env.ref('base.group_system'))

    def test_plain_user_cannot_read_screen_model(self):
        """NFR-SEC-0001: a plain internal user has no access row."""
        with self.assertRaises(AccessError):
            self.env['sfd.dependency.viewer'].with_user(self.sfd_user).search([])

    def test_map_data_empty_without_a_field(self):
        """FR-SFD-0001: no field chosen, no map."""
        viewer = self.env['sfd.dependency.viewer'].with_user(self.sfd_admin).create({})
        self.assertFalse(viewer.map_data)
