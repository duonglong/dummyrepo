from odoo.exceptions import ValidationError
from odoo.tests import Form

from .acceptance_m2b_helpers import DISMISS, M2BCase, call_first, dump


class TestDuplicateCompareAcceptance(M2BCase):
    """Acceptance tests of T-017: compare a group, set it aside, preview a merge.

    The linked documents of a contact are counted from the records that point at it: the module has
    no sales orders, so the tests use the contact's bank accounts as the linked documents (5 and 1).
    The side-by-side screen has no model or method in the design: its figures are read from the
    group's lines (score and linked-document count) and the marking of differing values from the
    merge preview's value lines; the screen itself is left to the build. The merge preview is a
    ``Form`` of ``data.quality.merge.wizard`` opened from the group's context and saved.

    Requirements: FR-DGQ-0040, FR-DGQ-0041, FR-DGQ-0042
    """

    def setUp(self):
        super().setUp()
        self.rule = self.contact_rule()
        self.acme = self.partner('Acme Ltd', email='info@acme.com')
        self.acme_two = self.partner('ACME Ltd.', email='INFO@acme.com')

    def acme_group(self):
        return self.make_group(self.rule, self.acme | self.acme_two)

    def link_five_and_one(self):
        for number in range(5):
            self.link_bank(self.acme, 'ACME-A-%d' % number)
        self.link_bank(self.acme_two, 'ACME-B-1')

    # ---- FR-DGQ-0040 -------------------------------------------------------

    def test_lines_carry_each_record_score_and_linked_document_count(self):
        """SC-DGQ-0040-1: the group shows both records with their scores and 5 and 1 linked documents."""
        self.link_five_and_one()
        self.acme.phone = '555-0100'
        self.acme_two.phone = '555-0199'
        group = self.acme_group()
        lines = group.line_ids
        by_id = {line.res_id: line for line in lines}
        self.assertEqual(set(by_id), {self.acme.id, self.acme_two.id})
        self.assertEqual(by_id[self.acme.id].link_count, 5)
        self.assertEqual(by_id[self.acme_two.id].link_count, 1)
        for line in lines:
            self.assertGreaterEqual(line.score, 85)
        self.assertTrue(all(line.record_name for line in lines))

    def test_differing_values_are_marked_in_the_preview(self):
        """SC-DGQ-0040-1: the phone values, different on both records, are marked as different."""
        self.acme.phone = '555-0100'
        self.acme_two.phone = '555-0199'
        wizard = self.open_preview(group=self.acme_group())
        values = {line.field_name: line for line in wizard.value_line_ids}
        self.assertTrue(values['phone'].differs)

    # ---- FR-DGQ-0041 -------------------------------------------------------

    def test_set_aside_group_is_closed_and_not_grouped_again(self):
        """SC-DGQ-0041-1: two 'John Smith' marked not duplicates stay out of any open group at the next check."""
        john = self.partner('John Smith', email='john@smith.com')
        john_two = self.partner('John Smith', email='JOHN@smith.com')
        group = self.make_group(self.rule, john | john_two)
        call_first(group.with_user(self.manager_a), DISMISS)
        group.invalidate_recordset()
        self.assertEqual(group.state, 'dismissed')
        self.run_check(self.rule)
        self.assertFalse(self.group_of(self.rule, john) & self.group_of(self.rule, john_two))
        for open_group in self.open_groups(self.rule):
            self.assertFalse({john.id, john_two.id} <= self.group_ids(open_group))

    def test_a_new_matching_record_forms_a_new_group(self):
        """FR-DGQ-0041: a third 'John Smith' created after the decision still forms an open group."""
        john = self.partner('John Smith', email='john@smith.com')
        john_two = self.partner('John Smith', email='JOHN@smith.com')
        group = self.make_group(self.rule, john | john_two)
        call_first(group.with_user(self.manager_a), DISMISS)
        third = self.partner('John Smith', email='John@Smith.com')
        self.run_check(self.rule)
        self.assertTrue(self.group_of(self.rule, third))

    def test_the_decision_is_remembered_per_pair(self):
        """FR-DGQ-0041: the set-aside pair is stored once with the user who decided."""
        john = self.partner('John Smith', email='john@smith.com')
        john_two = self.partner('John Smith', email='JOHN@smith.com')
        group = self.make_group(self.rule, john | john_two)
        call_first(group.with_user(self.manager_a), DISMISS)
        dismissals = self.env['data.quality.duplicate.dismissal'].sudo().search(
            [('rule_id', '=', self.rule.id)])
        self.assertEqual(len(dismissals), 1)
        self.assertEqual({dismissals.res_id_a, dismissals.res_id_b}, {john.id, john_two.id})
        self.assertLess(dismissals.res_id_a, dismissals.res_id_b)
        self.assertEqual(dismissals.user_id, self.manager_a)

    # ---- FR-DGQ-0042 -------------------------------------------------------

    def test_preview_keeps_the_record_with_most_links_and_fills_the_gaps(self):
        """SC-DGQ-0042-1: keeps 'Acme Ltd', phone 555-0100 from 'ACME Ltd.', 1 document to move, 'ACME Ltd.' archived, reversible."""
        self.link_five_and_one()
        self.acme_two.phone = '555-0100'
        wizard = self.open_preview(group=self.acme_group())
        self.assertEqual(wizard.kept_res_id, self.acme.id)
        phone = wizard.value_line_ids.filtered(lambda line: line.field_name == 'phone')
        self.assertEqual(len(phone), 1)
        self.assertEqual(phone.source_res_id, self.acme_two.id)
        self.assertIn('555-0100', phone.value_display or '')
        self.assertIn('res.partner.bank', dump(wizard.link_summary))
        self.assertIn('1', dump(wizard.link_summary))
        self.assertIn('ACME Ltd.', wizard.archive_names or '')
        self.assertNotIn('Acme Ltd', wizard.archive_names or '')
        self.assertTrue(wizard.reversible)

    def test_another_kept_record_can_be_chosen(self):
        """FR-DGQ-0042: the manager changes the kept record and the archived one follows."""
        self.link_five_and_one()
        group = self.acme_group()
        Wizard = self.env['data.quality.merge.wizard'].with_user(self.manager_a)
        context = {
            'active_model': 'data.quality.duplicate.group', 'active_id': group.id,
            'default_group_id': group.id, 'default_res_model': 'res.partner',
            'default_res_ids': sorted(self.group_ids(group)),
        }
        with Form(Wizard.with_context(**context)) as form:
            form.kept_res_id = self.acme_two.id
        wizard = form.record
        self.assertEqual(wizard.kept_res_id, self.acme_two.id)
        self.assertIn('Acme Ltd', wizard.archive_names or '')

    def test_closing_the_preview_changes_nothing(self):
        """SC-DGQ-0042-2: after the preview is opened and left, both contacts, their links and the history are unchanged."""
        self.link_five_and_one()
        self.acme_two.phone = '555-0100'
        group = self.acme_group()
        self.open_preview(group=group)
        self.env.flush_all()
        for partner in (self.acme, self.acme_two):
            partner.invalidate_recordset()
            self.assertTrue(partner.active)
        self.assertFalse(self.acme.phone)
        self.assertEqual(self.acme_two.phone, '555-0100')
        self.assertEqual(
            self.env['res.partner.bank'].search_count([('partner_id', '=', self.acme_two.id)]), 1)
        self.assertEqual(group.state, 'open')
        # That no merge record exists is checked with the merge model, in the merge task's tests.

    def test_a_merge_takes_two_to_ten_records(self):
        """FR-DGQ-0042: a preview of 1 or 11 records is refused; 2 and 10 are accepted."""
        Wizard = self.env['data.quality.merge.wizard']
        partners = self.env['res.partner']
        for index in range(11):
            partners |= self.partner('Many %d' % index)
        for count in (2, 10):
            wizard = Wizard.create({
                'res_model': 'res.partner', 'res_ids': partners[:count].ids,
                'kept_res_id': partners[0].id})
            self.assertEqual(len(wizard.res_ids), count)
        for count in (1, 11):
            with self.assertRaises(ValidationError, msg='%d records' % count):
                Wizard.create({
                    'res_model': 'res.partner', 'res_ids': partners[:count].ids,
                    'kept_res_id': partners[0].id})
