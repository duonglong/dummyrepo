from odoo.tests import tagged

from .common import WeekendMultiplierCase


@tagged('post_install', '-at_install')
class TestBugB0010(WeekendMultiplierCase):

    def test_bug_b_0010_loyalty_program_extended_from_one_file(self):
        """Regression test for B-0010: loyalty.program is extended from one file only.

        Requirements: NFR-MNT-001, FR-LAP-013
        """
        classes = [
            cls for cls in type(self.env['loyalty.program']).__mro__
            if cls.__module__.startswith('odoo.addons.loyalty_weekend_multiplier.')
        ]
        files = sorted({cls.__module__ for cls in classes})
        self.assertEqual(
            files, ['odoo.addons.loyalty_weekend_multiplier.models.loyalty_program'],
            "loyalty.program must be extended from a single file",
        )
