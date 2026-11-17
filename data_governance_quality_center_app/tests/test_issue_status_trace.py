from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestIssueStatusTrace(TransactionCase):
    """The code that checks the status of an issue names the requirement it implements.

    The check lives in two places, the write that applies the table and the method that
    works out the values; both must carry the requirement, or the round trip from the
    requirement to its code finds nothing.

    Requirements: FR-DGQ-0025
    """

    def assert_names_requirement(self, method):
        self.assertIn('FR-DGQ-0025', method.__doc__ or '', method.__qualname__)

    def test_bug_b_0225_write_names_the_status_requirement(self):
        """Regression test for B-0225: the write of an issue names FR-DGQ-0025.

        Requirements: FR-DGQ-0025
        """
        self.assert_names_requirement(type(self.env['data.quality.issue']).write)

    def test_bug_b_0225_state_values_names_the_status_requirement(self):
        """Regression test for B-0225: the status values of an issue name FR-DGQ-0025.

        Requirements: FR-DGQ-0025
        """
        self.assert_names_requirement(type(self.env['data.quality.issue'])._dgq_state_values)
