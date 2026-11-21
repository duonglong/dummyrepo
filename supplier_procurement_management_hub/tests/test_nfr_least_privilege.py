"""Least privilege: portal rights stay narrow, forms need the CSRF token, elevated rights are named.

Requirements: NFR-SPM-0002
"""
import re
from pathlib import Path

from odoo.exceptions import AccessError

from .portal_common import MODULE, SpmPortalCase

MODULE_DIR = Path(__file__).resolve().parent.parent
# Back-office models a supplier portal user must never read.
BACK_OFFICE_MODELS = (
    'procurement.audit.log', 'procurement.risk.assessment', 'procurement.screening',
    'procurement.supplier.scorecard', 'procurement.supplier.review', 'procurement.approval.request',
    'procurement.approval.policy', 'procurement.override', 'procurement.reconciliation',
    'procurement.contract', 'procurement.comparison', 'procurement.notification.log',
    'procurement.job', 'procurement.job.run', 'procurement.corrective.action',
)


class TestLeastPrivilege(SpmPortalCase):
    """Requirements: NFR-SPM-0002"""

    def test_nfr_0002_a_portal_user_reads_no_back_office_model(self):
        """NFR-SPM-0002: the supplier's portal user has no read right on the internal models."""
        for name in BACK_OFFICE_MODELS:
            with self.assertRaises(AccessError, msg=name):
                self.env[name].with_user(self.acme_user).check_access('read')

    def test_nfr_0002_every_route_of_the_module_needs_a_session_but_the_onboarding_one(self):
        """NFR-SPM-0002: each route of the supplier portal asks for a logged-in user and keeps the CSRF check."""
        routing_map = self.env['ir.http'].routing_map()
        seen = 0
        for rule in routing_map.iter_rules():
            endpoint = rule.endpoint
            if getattr(endpoint.original_endpoint, '__module__', '').split('.')[:3] \
                    != ['odoo', 'addons', MODULE]:
                continue
            routing = endpoint.routing
            if not rule.rule.startswith(('/my/procurement', '/onboarding')):
                continue  # a native address the app guards by override: its auth is Odoo's own
            seen += 1
            if routing['auth'] == 'public':
                self.assertIn('/onboarding', rule.rule, rule.rule)
            else:
                self.assertEqual(routing['auth'], 'user', rule.rule)
            if 'POST' in (routing.get('methods') or ()):
                self.assertTrue(routing.get('csrf', True), rule.rule)
        self.assertGreater(seen, 8)

    def test_nfr_0002_form_posts_without_the_csrf_token_are_refused(self):
        """NFR-SPM-0002: a post without Odoo's token changes nothing, on every portal form."""
        order = self._order(self.acme)
        event = self._event(self.acme, title='Csrf chairs')
        self._login(self.acme_user)
        addresses = [
            '/my/procurement/profile', '/my/procurement/documents/upload',
            f'/my/procurement/rfq/{event.id}/submit', f'/my/procurement/rfq/{event.id}/question',
            f'/my/procurement/orders/{order.id}/acknowledge', f'/my/procurement/orders/{order.id}/propose',
        ]
        for address in addresses:
            response = self.url_open(address, data={'question': 'x', 'name': 'Changed'}, allow_redirects=False)
            self.assertIn(response.status_code, (400, 403), address)
        self.assertFalse(order.spm_acknowledged_date)
        self.assertEqual(self.acme.name, 'Acme')

    def test_nfr_0002_elevated_rights_are_named_in_the_portal_code(self):
        """NFR-SPM-0002: every sudo() of the portal controllers and the access service carries its reason."""
        files = list((MODULE_DIR / 'controllers').glob('*.py')) + [MODULE_DIR / 'services' / 'portal_access.py']
        bare = []
        reason = re.compile(r'sudo:|Elevated')
        for path in files:
            lines = path.read_text().splitlines()
            for number, line in enumerate(lines):
                if re.search(r'\.sudo\(\)', line) \
                        and not reason.search(line) and not reason.search(lines[number - 1]):
                    bare.append(f'{path.name}:{number + 1}')
        self.assertEqual(bare, [], 'sudo() without its reason')
