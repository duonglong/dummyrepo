from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase

MESSAGE = "The depth limit must be a whole number of at least 1."


class TestDepthLimitAcceptance(TransactionCase):
    """Acceptance tests of T-002: the per-user depth limit on res.users.

    Requirements: BR-SFD-0004, FR-SFD-0010, FR-SFD-0013.
    The limit is set through the method ``sfd_set_depth_limit(value)``; the
    viewer's input box that calls it is left to the build of the viewer
    screen, so no Form step is tested here. An empty stored value (0) means
    "never set": the default of 5 then applies (BR-SFD-0004).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env.ref("base.group_system")
        users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.anna = users.create({
            "name": "Anna", "login": "sfd_anna", "group_ids": [(6, 0, group.ids)],
        })
        cls.ben = users.create({
            "name": "Ben", "login": "sfd_ben", "group_ids": [(6, 0, group.ids)],
        })

    def _set(self, user, value):
        return user.with_user(user).sfd_set_depth_limit(value)

    def _stored(self, user):
        self.env.flush_all()
        self.env.invalidate_all()
        return user.sudo().sfd_depth_limit

    def test_unset_limit_is_empty_value(self):
        """BR-SFD-0004: a user who never set a limit holds 0 (default 5 applies)."""
        self.assertEqual(self._stored(self.ben), 0)

    def test_own_limit_kept_per_user(self):
        """SC-SFD-0010-1: Anna sets 2; Ben, who set none, is untouched."""
        self._set(self.anna, 2)
        self.assertEqual(self._stored(self.anna), 2)
        self.assertEqual(self._stored(self.ben), 0)

    def test_limit_kept_for_next_visit(self):
        """SC-SFD-0010-2: the new limit is stored and read back next time."""
        self._set(self.anna, 5)
        self._set(self.anna, 2)
        self.assertEqual(self._stored(self.anna), 2)

    def test_invalid_limit_refused_and_previous_kept(self):
        """SC-SFD-0013-1: 0, -2 and 2.5 are refused with the message; limit stays 4."""
        self._set(self.anna, 4)
        for value in (0, -2, 2.5):
            with self.subTest(value=value):
                with self.assertRaises(UserError) as caught:
                    self._set(self.anna, value)
                self.assertIn(MESSAGE, str(caught.exception))
                self.assertEqual(self._stored(self.anna), 4)

    def test_smallest_valid_limit_accepted(self):
        """SC-SFD-0013-2: 1 is accepted when the limit was 4."""
        self._set(self.anna, 4)
        self._set(self.anna, 1)
        self.assertEqual(self._stored(self.anna), 1)

    def test_constraint_refuses_negative_stored_value(self):
        """FR-SFD-0013: a stored value below 1 (other than 0 = not set) is refused."""
        with self.assertRaises(ValidationError):
            self.anna.sudo().write({"sfd_depth_limit": -3})
