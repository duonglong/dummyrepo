from odoo.exceptions import AccessError, ValidationError

from .common import SfdCommon

MESSAGE = "The depth limit must be a whole number of at least 1."


class TestDepthLimit(SfdCommon):
    """Unit tests of the depth limit setter and default.

    Requirements: BR-SFD-0004, FR-SFD-0010, FR-SFD-0013
    """

    def test_effective_limit_default_and_own(self) -> None:
        """BR-SFD-0004: 5 until the user sets one (SC-SFD-0010-1)."""
        user = self.sfd_admin.with_user(self.sfd_admin)
        self.assertEqual(user._sfd_effective_depth_limit(), 5)
        user.sfd_set_depth_limit(2)
        self.assertEqual(user._sfd_effective_depth_limit(), 2)

    def test_string_digits_accepted(self) -> None:
        """FR-SFD-0010: the viewer box sends text; digits are a whole number."""
        self.assertEqual(self.sfd_admin.with_user(self.sfd_admin).sfd_set_depth_limit(" 3 "), 3)

    def test_non_whole_values_refused(self) -> None:
        """FR-SFD-0013: blank, text, fractions, booleans keep the old limit."""
        user = self.sfd_admin.with_user(self.sfd_admin)
        user.sfd_set_depth_limit(4)
        for value in ("", "abc", "2.5", "-2", "0", 2.5, True, None):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError, msg=MESSAGE):
                    user.sfd_set_depth_limit(value)
                self.assertEqual(user.sfd_depth_limit, 4)

    def test_other_user_refused(self) -> None:
        """Only one's own limit can be set."""
        with self.assertRaises(AccessError):
            self.sfd_user.with_user(self.sfd_admin).sfd_set_depth_limit(3)
        with self.assertRaises(AccessError):
            self.sfd_user.with_user(self.sfd_user).sfd_set_depth_limit(3)
