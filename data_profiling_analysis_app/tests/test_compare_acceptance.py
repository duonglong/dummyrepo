from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, tagged

SAME_MODEL_MSG = "Choose two different snapshots of the same model."


@tagged('post_install', '-at_install')
class TestCompareAcceptance(TransactionCase):
    """Acceptance tests of T-009 (compare two snapshots).

    The comparison dialog is made through Odoo's Form helper on the transient
    model ``dpa.snapshot.compare`` (snapshot A, snapshot B), saved, and then the
    method ``action_compare`` (a name the design leaves open) is called, as the
    analyst. A refusal may come at the form's save or at ``action_compare``:
    both are inside the asserted block. If ``action_compare`` returns an action
    with ``res_id`` the result is read from that record, else from the dialog.
    The saved snapshots are created directly (the runs and saving are tested
    with T-003 and T-007).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env['res.groups'].search([('name', '=', 'Data Profiling')], limit=1)
        internal = cls.env.ref('base.group_user')
        cls.anna = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Anna Analyst', 'login': 'anna_cmp', 'email': 'anna_cmp@example.com',
            'group_ids': [(6, 0, internal.ids + group.ids)],
        })
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.profile = cls.env['dpa.profile'].create({'name': 'Customer quality', 'model_id': cls.partner_model.id})

    # -- helpers ---------------------------------------------------------
    def _snapshot(self, run_date, lines, model='res.partner', records=100, outdated=0, profile=None, origin='manual'):
        """A saved snapshot; ``lines`` maps a field name to its stored numbers."""
        model_rec = self.env['ir.model']._get(model)
        return self.env['dpa.snapshot'].create({
            'name': 'Snapshot %s' % run_date,
            'profile_id': ((profile or self.profile).id if model == 'res.partner' else False),
            'profile_name': 'Customer quality',
            'model_id': model_rec.id,
            'model_name': model,
            'model_label': model_rec.name,
            'run_date': run_date,
            'is_saved': True,
            'origin': origin,
            'record_count': records,
            'outdated_count': outdated,
            'field_line_ids': [(0, 0, {
                'sequence': sequence,
                'field_name': fname,
                'field_label': fname.title(),
                'field_type': 'char',
                'is_rated': True,
                'record_count': records,
                **values,
            }) for sequence, (fname, values) in enumerate(lines.items(), start=1)],
        })

    def _compare(self, first, second):
        form = Form(self.env['dpa.snapshot.compare'].with_user(self.anna))
        form.snapshot_a_id = first
        form.snapshot_b_id = second
        compare = form.save()
        action = compare.action_compare()
        if isinstance(action, dict) and action.get('res_id'):
            compare = compare.browse(action['res_id'])
        return compare

    def _line(self, compare, field_name):
        line = compare.line_ids.filtered(lambda l: l.field_name == field_name)
        self.assertEqual(len(line), 1, "one comparison line expected for %s" % field_name)
        return line

    def _november_december(self):
        november = self._snapshot('2026-11-01 09:00:00', {
            'email': {'completeness': 80.0, 'empty_rate': 20.0, 'distinct_count': 50,
                      'inconsistent_count': 4, 'unusual_count': 3},
        }, records=100, outdated=10)
        december = self._snapshot('2026-12-01 09:00:00', {
            'email': {'completeness': 90.0, 'empty_rate': 10.0, 'distinct_count': 60,
                      'inconsistent_count': 1, 'unusual_count': 5},
        }, records=120, outdated=7)
        return november, december

    # -- FR-DPA-0023 -------------------------------------------------------
    def test_completeness_improved(self):
        """Requirements: FR-DPA-0023, SC-DPA-0023-1."""
        november, december = self._november_december()
        line = self._line(self._compare(november, december), 'email')
        self.assertEqual(line.older_completeness, 80.0)
        self.assertEqual(line.newer_completeness, 90.0)
        self.assertAlmostEqual(line.completeness_change, 10.0)
        self.assertTrue(line.in_older and line.in_newer)

    def test_every_measure_with_its_change(self):
        """Requirements: FR-DPA-0023 (empty rate, distinct, inconsistent and unusual of each, with the change)."""
        november, december = self._november_december()
        line = self._line(self._compare(november, december), 'email')
        self.assertEqual((line.older_empty_rate, line.newer_empty_rate), (20.0, 10.0))
        self.assertAlmostEqual(line.empty_rate_change, -10.0)
        self.assertEqual((line.older_distinct, line.newer_distinct, line.distinct_change), (50, 60, 10))
        self.assertEqual(
            (line.older_inconsistent, line.newer_inconsistent, line.inconsistent_change), (4, 1, -3))
        self.assertEqual((line.older_unusual, line.newer_unusual, line.unusual_change), (3, 5, 2))

    def test_records_and_outdated_of_each(self):
        """Requirements: FR-DPA-0023 (records profiled and outdated records of each snapshot)."""
        november, december = self._november_december()
        compare = self._compare(november, december)
        self.assertEqual((compare.older_record_count, compare.newer_record_count), (100, 120))
        self.assertEqual((compare.older_outdated_count, compare.newer_outdated_count), (10, 7))

    def test_older_first_whatever_the_order(self):
        """Requirements: FR-DPA-0023, SC-DPA-0023-2 (December picked first, November second)."""
        november, december = self._november_december()
        compare = self._compare(december, november)
        self.assertEqual(compare.older_id, november)
        self.assertEqual(compare.newer_id, december)
        self.assertEqual((compare.older_record_count, compare.newer_record_count), (100, 120))
        line = self._line(compare, 'email')
        self.assertEqual((line.older_completeness, line.newer_completeness), (80.0, 90.0))
        self.assertAlmostEqual(line.completeness_change, 10.0)

    def test_snapshots_of_two_profiles_compare(self):
        """Requirements: FR-DPA-0023 (a manual and a scheduled snapshot of two profiles of one model compare)."""
        other = self.env['dpa.profile'].create({'name': 'Other', 'model_id': self.partner_model.id})
        first = self._snapshot('2026-11-01 09:00:00', {'email': {'completeness': 50.0}})
        second = self._snapshot('2026-12-01 09:00:00', {'email': {'completeness': 70.0}}, profile=other, origin='scheduled')
        line = self._line(self._compare(first, second), 'email')
        self.assertAlmostEqual(line.completeness_change, 20.0)

    def test_archived_profile_snapshots_still_compare(self):
        """Requirements: FR-DPA-0031, SC-DPA-0031-2 (the schedule part is tested with T-008)."""
        november, december = self._november_december()
        self.profile.action_archive()
        self.assertFalse(self.profile.active)
        line = self._line(self._compare(november, december), 'email')
        self.assertAlmostEqual(line.completeness_change, 10.0)

    # -- FR-DPA-0024 -------------------------------------------------------
    def test_different_models_refused(self):
        """Requirements: FR-DPA-0024, SC-DPA-0024-1 (contacts against countries stand for products)."""
        contacts = self._snapshot('2026-11-01 09:00:00', {'email': {'completeness': 80.0}})
        countries = self._snapshot('2026-11-02 09:00:00', {'code': {'completeness': 100.0}}, model='res.country')
        with self.assertRaises(UserError) as cm:
            self._compare(contacts, countries)
        self.assertIn(SAME_MODEL_MSG, str(cm.exception))

    def test_same_snapshot_refused(self):
        """Requirements: FR-DPA-0024, SC-DPA-0024-2."""
        november, _december = self._november_december()
        with self.assertRaises(UserError) as cm:
            self._compare(november, november)
        self.assertIn(SAME_MODEL_MSG, str(cm.exception))

    # -- FR-DPA-0025 -------------------------------------------------------
    def test_field_added_later(self):
        """Requirements: FR-DPA-0025, SC-DPA-0025-1 (Mobile profiled on 1 December only)."""
        november = self._snapshot('2026-11-01 09:00:00', {'email': {'completeness': 80.0}})
        december = self._snapshot('2026-12-01 09:00:00', {
            'email': {'completeness': 90.0}, 'mobile': {'completeness': 60.0}})
        compare = self._compare(november, december)
        mobile = self._line(compare, 'mobile')
        self.assertFalse(mobile.in_older)
        self.assertTrue(mobile.in_newer)
        self.assertEqual(mobile.newer_completeness, 60.0)
        self.assertFalse(mobile.completeness_change)
        self.assertFalse(mobile.empty_rate_change)
        self.assertFalse(mobile.distinct_change)
        self.assertFalse(mobile.inconsistent_change)
        self.assertFalse(mobile.unusual_change)

    def test_field_removed_later(self):
        """Requirements: FR-DPA-0025 (the line of a field only in the older snapshot is kept, no change)."""
        november = self._snapshot('2026-11-01 09:00:00', {
            'email': {'completeness': 80.0}, 'fax': {'completeness': 30.0}})
        december = self._snapshot('2026-12-01 09:00:00', {'email': {'completeness': 90.0}})
        fax = self._line(self._compare(november, december), 'fax')
        self.assertTrue(fax.in_older)
        self.assertFalse(fax.in_newer)
        self.assertEqual(fax.older_completeness, 30.0)
        self.assertFalse(fax.completeness_change)

    def test_lines_follow_the_older_snapshot_order(self):
        """Requirements: FR-DPA-0025 (older snapshot's order first, fields of the newer one only after)."""
        november = self._snapshot('2026-11-01 09:00:00', {
            'phone': {'completeness': 10.0}, 'email': {'completeness': 80.0}})
        december = self._snapshot('2026-12-01 09:00:00', {
            'email': {'completeness': 90.0}, 'mobile': {'completeness': 60.0}, 'phone': {'completeness': 20.0}})
        compare = self._compare(december, november)
        ordered = compare.line_ids.sorted(lambda l: (l.sequence, l.id))
        self.assertEqual(ordered.mapped('field_name'), ['phone', 'email', 'mobile'])
