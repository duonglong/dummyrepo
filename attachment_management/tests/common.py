from odoo.tests.common import TransactionCase, new_test_user


class AttachmentManagementCase(TransactionCase):
    """Base of the module tests: an administrator and two plain internal users."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.am_admin = new_test_user(cls.env, login='am_base_admin', groups='base.group_system')
        cls.am_user = new_test_user(cls.env, login='am_base_user', groups='base.group_user')
        cls.am_other_user = new_test_user(cls.env, login='am_base_other', groups='base.group_user')
