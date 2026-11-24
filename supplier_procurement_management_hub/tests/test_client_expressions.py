"""Expressions of views and actions that the web client, not the server, evaluates.

Requirements: UX-SPM-011, FR-SPM-0013
"""
import re
from datetime import date

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo.tests.common import TransactionCase
from odoo.tools.safe_eval import safe_eval

from .comparison_common import SpmComparisonCase

MODULE = 'supplier_procurement_management_hub'
# The client's date has no replace(); shifting by a relativedelta works on both sides.
CLIENT_UNSUPPORTED_DATE_CALL = re.compile(r'context_today\(\)\)?\.replace\(')


class TestClientExpressions(TransactionCase):
    """Requirements: UX-SPM-011"""

    def _module_views(self):
        view_ids = self.env['ir.model.data'].search([
            ('module', '=', MODULE), ('model', '=', 'ir.ui.view')]).mapped('res_id')
        return self.env['ir.ui.view'].browse(view_ids).exists()

    def _filter_domain(self, view_xmlid, filter_name):
        arch = etree.fromstring(self.env.ref(f'{MODULE}.{view_xmlid}').arch_db.encode())
        return arch.xpath(f'//filter[@name="{filter_name}"]')[0].get('domain')

    def _evaluate(self, domain, today):
        return safe_eval(domain, {'context_today': lambda: today, 'relativedelta': relativedelta})

    def test_bug_b_0277_spend_report_opens_with_its_default_filter(self):
        """Regression test for B-0277: the Spend report opened by address applies 'This year' without a client error.

        Requirements: UX-SPM-011
        It cannot reach the browser: it reads the filter the client evaluates for the opening action's default.
        """
        action = self.env.ref(f'{MODULE}.procurement_report_spend_action')
        self.assertIn('search_default_this_year', action.context)
        domain = self._filter_domain('procurement_report_spend_search', 'this_year')
        self.assertFalse(CLIENT_UNSUPPORTED_DATE_CALL.search(domain), domain)
        self.assertEqual(self._evaluate(domain, date(2026, 11, 24)), [('date', '>=', '2026-01-01')])

    def test_bug_b_0277_audit_log_this_month_filter_is_client_evaluable(self):
        """Regression test for B-0277: the audit log 'This month' filter has the same client-side mistake.

        Requirements: UX-SPM-011
        """
        domain = self._filter_domain('procurement_audit_log_view_search', 'this_month')
        self.assertFalse(CLIENT_UNSUPPORTED_DATE_CALL.search(domain), domain)
        self.assertEqual(self._evaluate(domain, date(2026, 11, 24)), [('date', '>=', '2026-11-01')])

    def test_bug_b_0277_no_view_calls_replace_on_the_client_date(self):
        """Regression test for B-0277: the sweep over every view of the module."""
        bad = [(view.xml_id, attr) for view in self._module_views()
               for node in etree.fromstring(view.arch_db.encode()).iter() if isinstance(node.tag, str)
               for attr in node.attrib.values() if CLIENT_UNSUPPORTED_DATE_CALL.search(attr)]
        self.assertEqual(bad, [])

    def test_bug_b_0277_no_module_action_reads_active_id_in_its_context(self):
        """Regression test for B-0277: an action opened by address has no active record to read.

        Requirements: UX-SPM-011
        """
        actions = self.env['ir.actions.act_window'].search([
            ('id', 'in', self.env['ir.model.data'].search([
                ('module', '=', MODULE), ('model', '=', 'ir.actions.act_window')]).mapped('res_id'))])
        bad = [action.xml_id for action in actions if re.search(r'\bactive_ids?\b', action.context or '')]
        self.assertEqual(bad, [])


class TestSourcingEventOverride(SpmComparisonCase):
    """Requirements: FR-SPM-0013"""

    def test_bug_b_0277_override_dialog_opens_for_the_event_without_active_id(self):
        """Regression test for B-0277: the event's Override button builds its dialog from the record.

        Requirements: FR-SPM-0013
        """
        event = self._sourcing(self.acme | self.beta)
        action = event.action_spm_open_override_wizard()
        self.assertEqual(action['res_model'], 'procurement.reason.wizard')
        self.assertEqual(action['context']['default_res_ids'], event.ids)
        self.assertEqual(action['context']['default_action'], 'override')
