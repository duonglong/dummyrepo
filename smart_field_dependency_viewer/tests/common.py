from odoo.tests.common import TransactionCase, new_test_user


class SfdCommon(TransactionCase):
    """Base of the module tests: a settings administrator and a plain internal user."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sfd_admin = new_test_user(cls.env, login='sfd_base_admin', groups='base.group_system')
        cls.sfd_user = new_test_user(cls.env, login='sfd_base_user', groups='base.group_user')
