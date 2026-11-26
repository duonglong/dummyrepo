from datetime import datetime

from odoo.tests import Form, TransactionCase, tagged
from odoo.tests.common import freeze_time


@tagged('post_install', '-at_install')
class TestValuesAcceptance(TransactionCase):
    """Acceptance tests of T-004 (flags, distinct values, relational distribution, unused selection values).

    The profile is made through Odoo's Form helper (name, model, chosen fields,
    thresholds); the Run button is the method ``action_run`` of the profile,
    called on the saved record as the analyst, because the button itself is
    built with the view by the task. The report is read back as the unsaved
    ``dpa.snapshot`` of the profile. Contacts (and contact tags or currency
    rates where a scenario needs a date) stand in for the products of the
    scenarios: this module depends on base only.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env['res.groups'].search([('name', '=', 'Data Profiling')], limit=1)
        internal = cls.env.ref('base.group_user')
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.anna = Users.create({
            'name': 'Anna Analyst', 'login': 'anna_val', 'email': 'anna_val@example.com',
            'group_ids': [(6, 0, internal.ids + group.ids)],
        })
        cls.marc = Users.create({
            'name': 'Marc', 'login': 'marc_val', 'email': 'marc_val@example.com',
            'group_ids': [(6, 0, internal.ids)],
        })
        cls.lisa = Users.create({
            'name': 'Lisa', 'login': 'lisa_val', 'email': 'lisa_val@example.com',
            'group_ids': [(6, 0, internal.ids)],
        })
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        # Only the contacts a test creates are profiled: archive every other one.
        cls.env.cr.execute("UPDATE res_partner SET active = false")
        cls.env.invalidate_all()

    def _contacts(self, vals_list):
        return self.env['res.partner'].create(
            [dict(vals, name=vals.get('name', 'Contact %d' % i)) for i, vals in enumerate(vals_list)])

    def _profile(self, field_names=(), name='Customer quality', model='res.partner', **settings):
        """Create a profile through the form, as the analyst."""
        form = Form(self.env['dpa.profile'].with_user(self.anna))
        form.name = name
        form.model_id = self.env['ir.model']._get(model)
        for fname in field_names:
            form.field_ids.add(self.env['ir.model.fields']._get(model, fname))
        for key, value in settings.items():
            setattr(form, key, value)
        return form.save()

    def _run(self, profile):
        profile.with_user(self.anna).action_run()
        self.env.flush_all()
        self.env.invalidate_all()
        report = self.env['dpa.snapshot'].search(
            [('profile_id', '=', profile.id), ('is_saved', '=', False)])
        self.assertEqual(len(report), 1, "one unsaved report must result from a run")
        return report

    def _line(self, report, field_name):
        line = report.field_line_ids.filtered(lambda l: l.field_name == field_name)
        self.assertEqual(len(line), 1, "one line expected for %s" % field_name)
        return line

    def _values(self, line):
        return line.value_ids.sorted(lambda v: (v.sequence, v.id))

    def _findings(self, report, kind, line=None):
        found = report.finding_ids.filtered(lambda f: f.kind == kind)
        return found.filtered(lambda f: f.field_line_id == line) if line else found

    # -- FR-DPA-0005 -------------------------------------------------------
    def test_flag_above_and_equal_to_threshold(self):
        """Requirements: FR-DPA-0005, SC-DPA-0005-1 (30% empty flagged, 20% equal not flagged).

        The mobile of the scenario is the website link (this version has no
        mobile field); both are single-line text.
        """
        self._contacts(
            [{'email': 'a%d@x.com' % i, 'website': 'w%d.com' % i} for i in range(5)]
            + [{'email': 'b%d@x.com' % i} for i in range(2)]           # website empty: 3 in all
            + [{'website': 'v%d.com' % i} for i in range(3)])          # email empty: 3
        # email: 7 filled of 10 -> 30% empty; website: 8 filled of 10 -> 20% empty
        report = self._run(self._profile(['email', 'website']))
        email, website = self._line(report, 'email'), self._line(report, 'website')
        self.assertEqual(email.empty_rate, 30.0)
        self.assertTrue(email.high_empty)
        self.assertEqual(website.empty_rate, 20.0)
        self.assertFalse(website.high_empty)

    def test_flag_with_lower_threshold(self):
        """Requirements: FR-DPA-0005, SC-DPA-0005-2 (threshold 10%, 2 of 10 empty is flagged)."""
        self._contacts([{'website': 'w%d.com' % i} for i in range(8)] + [{}, {}])
        profile = self._profile(['website'], empty_threshold=10.0)
        report = self._run(profile)
        self.assertTrue(self._line(report, 'website').high_empty)

    # -- FR-DPA-0007 -------------------------------------------------------
    def test_values_most_frequent_first(self):
        """Requirements: FR-DPA-0007, SC-DPA-0007-1 (Brussels 5, Paris 3, (empty) 2)."""
        self._contacts([{'city': 'Brussels'}] * 5 + [{'city': 'Paris'}] * 3 + [{}, {}])
        line = self._line(self._run(self._profile(['city'])), 'city')
        self.assertEqual(line.distinct_count, 2)
        values = self._values(line)
        self.assertEqual(
            [(v.value_label, v.count, v.share) for v in values],
            [('Brussels', 5, 50.0), ('Paris', 3, 30.0), ('(empty)', 2, 20.0)])
        self.assertTrue(values[2].is_empty)

    def test_long_lists_cut_at_100(self):
        """Requirements: FR-DPA-0007, SC-DPA-0007-2 (150 cities: 100 listed, 50 more)."""
        self._contacts([{'city': 'City %03d' % (i % 150)} for i in range(300)])
        line = self._line(self._run(self._profile(['city'])), 'city')
        self.assertEqual(line.distinct_count, 150)
        self.assertEqual(len(self._values(line).filtered(lambda v: not v.is_empty)), 100)
        self.assertEqual(line.more_count, 50)

    # -- FR-DPA-0013 -------------------------------------------------------
    def test_records_per_salesperson(self):
        """Requirements: FR-DPA-0013, SC-DPA-0013-1 (Marc 6, Lisa 3, (none) 1)."""
        self._contacts([{'user_id': self.marc.id}] * 6 + [{'user_id': self.lisa.id}] * 3 + [{}])
        line = self._line(self._run(self._profile(['user_id'])), 'user_id')
        self.assertEqual(
            [(v.value_label, v.count) for v in self._values(line)],
            [('Marc', 6), ('Lisa', 3), ('(none)', 1)])

    def test_many2many_counts_each_link(self):
        """Requirements: FR-DPA-0013, SC-DPA-0013-2 (VIP 2, Export 1, (none) 1)."""
        vip, export = self.env['res.partner.category'].create([{'name': 'VIP'}, {'name': 'Export'}])
        self._contacts([
            {'category_id': [(6, 0, (vip | export).ids)]},
            {'category_id': [(6, 0, vip.ids)]},
            {},
        ])
        line = self._line(self._run(self._profile(['category_id'])), 'category_id')
        self.assertEqual(
            [(v.value_label, v.count) for v in self._values(line)],
            [('VIP', 2), ('Export', 1), ('(none)', 1)])

    # -- FR-DPA-0014 -------------------------------------------------------
    def test_one_unused_selection_value(self):
        """Requirements: FR-DPA-0014, SC-DPA-0014-1.

        The Address Type selection stands in for Priority: Contact, Invoice and
        Delivery are used, Other is not.
        """
        self._contacts([{'type': 'contact'}, {'type': 'invoice'}, {'type': 'delivery'}])
        line = self._line(self._run(self._profile(['type'])), 'type')
        self.assertEqual(line.unused_values, 'Other')

    def test_all_selection_values_used(self):
        """Requirements: FR-DPA-0014, SC-DPA-0014-2 (every Address Type used: None)."""
        self._contacts(
            [{'type': 'contact'}, {'type': 'invoice'}, {'type': 'delivery'}, {'type': 'other'}])
        line = self._line(self._run(self._profile(['type'])), 'type')
        self.assertEqual(line.unused_values, 'None')

    # -- FR-DPA-0015 -------------------------------------------------------
    def test_counts_of_one_field(self):
        """Requirements: FR-DPA-0015, SC-DPA-0015-1 (City: 10 records, 8 filled, 2 empty, 2 distinct).

        The scenario says the type shows as "Text"; the spec does not say
        whether the stored field type is the technical name or that label, so
        either is accepted here (open point).
        """
        self._contacts([{'city': 'Brussels'}] * 5 + [{'city': 'Paris'}] * 3 + [{}, {}])
        line = self._line(self._run(self._profile(['city'])), 'city')
        self.assertIn(line.field_type, ('char', 'Text'))
        self.assertEqual(
            (line.record_count, line.filled_count, line.empty_count, line.distinct_count),
            (10, 8, 2, 2))
