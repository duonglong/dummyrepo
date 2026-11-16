from psycopg2 import IntegrityError

from odoo.exceptions import AccessError
from odoo.tools import mute_logger

from .acceptance_m3_helpers import M3Case, choice


class TestDashboardLayoutAcceptance(M3Case):
    """Acceptance tests of T-015: each person's dashboard layout (FR-DGQ-0037).

    The layout is the lines of ``data.quality.dashboard.layout``: no line means the default layout, and
    the reset deletes the person's lines (design). The dashboard screen itself is read through a
    service the design does not name (reported as unclear: the trend, the explanation of a change and
    the drill-down have no test), so what is tested is the stored layout and who may see and change it.
    The reset button is not named either: the reset is tested as the deletion of the person's own lines.

    Requirements: FR-DGQ-0037
    """

    def setUp(self):
        super().setUp()
        self.bob = self.user('viewer', login='dgq_m3_bob', company=self.cx)
        self.ann = self.user('viewer', login='dgq_m3_ann', company=self.cx)
        self.recent = choice(self.env, 'data.quality.dashboard.layout', 'widget', ['recent'])
        self.Layout = self.env['data.quality.dashboard.layout']

    def lines(self, user):
        return self.Layout.with_user(user).search([('user_id', '=', user.id)])

    def hide_recent(self, user):
        return self.Layout.with_user(user).create(
            {'user_id': user.id, 'widget': self.recent, 'visible': False, 'sequence': 10})

    def test_a_hidden_widget_is_kept_for_that_person_only(self):
        """SC-DGQ-0037-1: Bob hides the recent issues widget; Ann has no line and so keeps the default."""
        self.hide_recent(self.bob)
        bob_lines = self.lines(self.bob)
        self.assertEqual(len(bob_lines), 1)
        self.assertEqual(bob_lines.widget, self.recent)
        self.assertFalse(bob_lines.visible)
        self.assertFalse(self.lines(self.ann), 'Ann changed nothing: the default layout applies to her')
        self.assertFalse(
            self.Layout.with_user(self.ann).search([('widget', '=', self.recent), ('visible', '=', False)]),
            "Ann cannot see Bob's line")

    def test_a_widget_can_be_reordered(self):
        """FR-DGQ-0037: the order a person gives is kept in the sequence of the line."""
        line = self.hide_recent(self.bob)
        line.with_user(self.bob).write({'visible': True, 'sequence': 1})
        line.invalidate_recordset()
        self.assertEqual((line.sequence, line.visible), (1, True))

    def test_a_person_cannot_change_another_persons_layout(self):
        """FR-DGQ-0037: Ann cannot change or delete Bob's line."""
        line = self.hide_recent(self.bob)
        with self.assertRaises(AccessError):
            line.with_user(self.ann).write({'visible': True})
        with self.assertRaises(AccessError):
            line.with_user(self.ann).unlink()

    def test_a_widget_has_one_line_per_person(self):
        """FR-DGQ-0037: the widget is unique with the user."""
        self.hide_recent(self.bob)
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.cr.savepoint():
            self.hide_recent(self.bob)

    def test_reset_removes_the_persons_lines_and_only_theirs(self):
        """SC-DGQ-0037-2: after Bob's lines are removed he has the default layout; Ann's lines stay."""
        self.hide_recent(self.bob)
        self.Layout.with_user(self.ann).create(
            {'user_id': self.ann.id, 'widget': self.recent, 'visible': True, 'sequence': 3})
        self.lines(self.bob).unlink()
        self.assertFalse(self.lines(self.bob))
        self.assertEqual(len(self.lines(self.ann)), 1)
