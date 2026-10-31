from odoo import Command
from odoo.tests.common import TransactionCase, new_test_user


class WeekendMultiplierCase(TransactionCase):
    """Shared fixtures of the loyalty_weekend_multiplier tests.

    A Loyalty Cards program earning 1 point per currency unit spent, a
    partner, a product priced 100.00 and three users: one in
    Europe/Brussels, one in America/New_York and one with no timezone.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Weekend Loyalty',
            'program_type': 'loyalty',
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_mode': 'money',
                'reward_point_amount': 1,
            })],
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Weekend Customer'})
        cls.product = cls.env['product.product'].create({
            'name': 'Weekend Product',
            'list_price': 100.0,
        })
        groups = 'base.group_user'
        cls.user_brussels = new_test_user(
            cls.env, login='weekend_brussels', groups=groups, tz='Europe/Brussels')
        cls.user_new_york = new_test_user(
            cls.env, login='weekend_new_york', groups=groups, tz='America/New_York')
        cls.user_no_tz = new_test_user(
            cls.env, login='weekend_no_tz', groups=groups, tz=False)
