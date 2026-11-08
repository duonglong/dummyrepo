from .acceptance_base import AuditAcceptanceCase


class TestA2Own(AuditAcceptanceCase):
    """Requirements: FR-OPA-0006 (T-005)."""

    def _a2(self):
        found = self.findings(self.start_run(), 'A2')
        return found.filtered(lambda f: f.field_id.model == 'x_line')

    def test_fix_params_name_table_and_column(self):
        """FR-OPA-0006 / SC-OPA-0006-1: the finding carries the table and column of its fix."""
        self.make_x_line(rows=200000)
        found = self._a2().filtered(lambda f: f.field_id.name == 'x_partner_id')
        self.assertEqual(found.fix_params['table'], 'x_line')
        self.assertEqual(found.fix_params['columns'], ['x_partner_id'])
        self.assertEqual(found.code_suggestion, 'index=True on x_line.x_partner_id')

    def test_other_columns_are_not_reported(self):
        """FR-OPA-0006: only many2one columns are reported, not the char or date ones."""
        self.make_x_line(rows=200000)
        self.assertEqual(
            sorted(self._a2().mapped('field_id.name')), ['create_uid', 'write_uid', 'x_partner_id'])
