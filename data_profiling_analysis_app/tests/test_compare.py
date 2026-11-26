from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCompareLines(TransactionCase):
    """Requirements: FR-DPA-0023, FR-DPA-0025."""

    def test_line_values_without_older_has_no_change(self):
        """A field only in the newer snapshot carries no change (FR-DPA-0025)."""
        snapshot = self.env['dpa.snapshot'].create({
            'name': 's', 'model_name': 'res.partner', 'run_date': '2026-11-01 09:00:00',
            'field_line_ids': [(0, 0, {'field_name': 'email', 'field_label': 'Email', 'completeness': 5.0})],
        })
        values = self.env['dpa.snapshot.compare.line']._line_values(1, False, snapshot.field_line_ids)
        self.assertFalse(values['in_older'])
        self.assertEqual(values['newer_completeness'], 5.0)
        self.assertNotIn('completeness_change', values)

    def _snapshot(self, **values):
        return self.env['dpa.snapshot'].create({
            'name': 's', 'model_name': 'res.partner', 'run_date': '2026-11-01 09:00:00', **values})

    def test_one_sided_line_says_not_profiled(self):
        """The side without the field carries the words "Not profiled" (FR-DPA-0025)."""
        snapshot = self._snapshot(field_line_ids=[
            Command.create({'field_name': 'email', 'field_label': 'Email', 'completeness': 5.0})])
        values = self.env['dpa.snapshot.compare.line']._line_values(1, False, snapshot.field_line_ids)
        self.assertEqual(values['older_note'], "Not profiled")
        self.assertFalse(values['newer_note'])

    def test_unsaved_report_refused(self):
        """A report not saved as a snapshot cannot be compared (FR-DPA-0024)."""
        saved = self._snapshot(is_saved=True)
        unsaved = self._snapshot()
        with self.assertRaises(ValidationError):
            self.env['dpa.snapshot.compare'].create({'snapshot_a_id': saved.id, 'snapshot_b_id': unsaved.id})

    def test_prefill_from_two_selected_snapshots(self):
        """Two selected list rows prefill the pickers (FR-DPA-0023)."""
        first, second = self._snapshot(is_saved=True), self._snapshot(is_saved=True)
        compare = Form(self.env['dpa.snapshot.compare'].with_context(
            active_model='dpa.snapshot', active_ids=[first.id, second.id]))
        self.assertEqual(compare.snapshot_a_id, first)
        self.assertEqual(compare.snapshot_b_id, second)

    def test_changing_a_snapshot_clears_the_lines(self):
        """The lines follow the pair: changing a picker drops the old lines (FR-DPA-0023)."""
        first = self._snapshot(is_saved=True, field_line_ids=[
            Command.create({'field_name': 'email', 'field_label': 'Email'})])
        second = self._snapshot(is_saved=True)
        third = self._snapshot(is_saved=True)
        compare = self.env['dpa.snapshot.compare'].create({'snapshot_a_id': first.id, 'snapshot_b_id': second.id})
        compare.action_compare()
        self.assertTrue(compare.line_ids)
        form = Form(compare)
        form.snapshot_b_id = third
        self.assertFalse(form.line_ids._records)


@tagged('post_install', '-at_install')
class TestCompareUnratedSnapshot(TransactionCase):
    """Requirements: FR-DPA-0028, FR-DPA-0023."""

    def _snapshot(self, record_count, filled_count, run_date):
        rated = bool(record_count)
        return self.env['dpa.snapshot'].create({
            'name': 's', 'model_name': 'res.partner', 'run_date': run_date, 'is_saved': True,
            'record_count': record_count,
            'field_line_ids': [Command.create({
                'field_name': 'email', 'field_label': 'Email', 'is_rated': rated,
                'completeness': filled_count * 100 / record_count if rated else 0.0,
                'empty_rate': 100 - filled_count * 100 / record_count if rated else 0.0,
                'distinct_count': 0,
            })],
        })

    def test_bug_b_0295_unrated_side_has_no_rates_and_no_change(self):
        """Regression test for B-0295: a 0-record snapshot shows "-" and no change for the rates (FR-DPA-0028).

        Walk state: saved a 0-record snapshot and a 10-record one, picked both, pressed Compare;
        the screen itself is not rendered here.
        """
        empty = self._snapshot(0, 0, '2026-11-01 09:00:00')
        later = self._snapshot(10, 8, '2026-11-02 09:00:00')
        compare = self.env['dpa.snapshot.compare'].create({'snapshot_a_id': later.id, 'snapshot_b_id': empty.id})
        compare.action_compare()
        line = compare.line_ids
        self.assertFalse(line.older_rated)
        self.assertTrue(line.newer_rated)
        self.assertEqual(line.older_note, "-")
        self.assertEqual(line.newer_completeness, 80.0)
        self.assertFalse(line.older_completeness)
        self.assertFalse(line.completeness_change)
        self.assertFalse(line.empty_rate_change)

    def test_bug_b_0295_newer_unrated_side_has_no_change(self):
        """The newer side without records shows "-" too, and no change (FR-DPA-0028)."""
        older = self._snapshot(10, 8, '2026-11-01 09:00:00')
        empty = self._snapshot(0, 0, '2026-11-02 09:00:00')
        compare = self.env['dpa.snapshot.compare'].create({'snapshot_a_id': older.id, 'snapshot_b_id': empty.id})
        compare.action_compare()
        line = compare.line_ids
        self.assertEqual(line.newer_note, "-")
        self.assertFalse(line.completeness_change)
        self.assertFalse(line.empty_rate_change)

    def test_bug_b_0295_rated_sides_keep_the_change(self):
        """Two rated sides still show the change (FR-DPA-0023)."""
        older = self._snapshot(10, 5, '2026-11-01 09:00:00')
        newer = self._snapshot(10, 8, '2026-11-02 09:00:00')
        compare = self.env['dpa.snapshot.compare'].create({'snapshot_a_id': older.id, 'snapshot_b_id': newer.id})
        compare.action_compare()
        self.assertEqual(compare.line_ids.completeness_change, 30.0)
        self.assertFalse(compare.line_ids.older_note)
