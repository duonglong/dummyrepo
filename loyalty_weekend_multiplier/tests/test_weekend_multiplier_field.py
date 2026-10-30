from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestWeekendMultiplierField(TransactionCase):
    """Acceptance tests of T-002: Weekend Multiplier on Loyalty Cards programs."""

    def _program(self, **vals):
        return self.env['loyalty.program'].create({
            'name': 'Weekend test',
            'program_type': 'loyalty',
            **vals,
        })

    def test_default_is_one(self):
        """Requirements: FR-LAP-001 - SC-LAP-001-1: a new program shows 1.00."""
        program = self._program()
        self.assertEqual(program['weekend_multiplier'], 1.0)
        defaults = self.env['loyalty.program'].default_get(['weekend_multiplier'])
        self.assertEqual(defaults.get('weekend_multiplier'), 1.0)

    def test_store_2_50(self):
        """Requirements: FR-LAP-001, BR-LAP-002 - SC-LAP-001-2: 2.50 is stored and re-read."""
        program = self._program()
        program.write({'weekend_multiplier': 2.5})
        self.env.flush_all()
        program.invalidate_recordset()
        self.assertEqual(program['weekend_multiplier'], 2.5)

    def test_store_below_one(self):
        """Requirements: FR-LAP-001, BR-LAP-002 - SC-LAP-001-3: 0.50 is stored."""
        program = self._program(weekend_multiplier=0.5)
        self.env.flush_all()
        program.invalidate_recordset()
        self.assertEqual(program['weekend_multiplier'], 0.5)

    def test_refuse_zero(self):
        """Requirements: FR-LAP-002 - SC-LAP-002-1: 0 is refused naming Weekend Multiplier."""
        program = self._program()
        with self.assertRaisesRegex(ValidationError, 'Weekend Multiplier'):
            program.write({'weekend_multiplier': 0})
            self.env.flush_all()

    def test_refuse_negative(self):
        """Requirements: FR-LAP-002 - SC-LAP-002-2: -1 is refused naming Weekend Multiplier."""
        program = self._program()
        with self.assertRaisesRegex(ValidationError, 'Weekend Multiplier'):
            program.write({'weekend_multiplier': -1})
            self.env.flush_all()

    def test_refuse_on_create(self):
        """Requirements: FR-LAP-002 - SC-LAP-002-1: creating with 0 is refused too."""
        with self.assertRaisesRegex(ValidationError, 'Weekend Multiplier'):
            self._program(weekend_multiplier=0)
            self.env.flush_all()

    def test_accept_smallest(self):
        """Requirements: FR-LAP-002, BR-LAP-002 - SC-LAP-002-3: 0.01 is accepted."""
        program = self._program()
        program.write({'weekend_multiplier': 0.01})
        self.env.flush_all()
        program.invalidate_recordset()
        self.assertEqual(program['weekend_multiplier'], 0.01)
