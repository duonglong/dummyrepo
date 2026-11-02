from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class WeekendPointsCommon(TransactionCase):
    """Shared base for the module's tests."""
