from odoo.modules import Manifest
from odoo.tests.common import tagged

from .common import PerfAuditCase


@tagged('post_install', '-at_install')
class TestInstall(PerfAuditCase):
    """Requirements: NFR-OPA-0002 (runs with the rights Odoo already has)."""

    def test_module_is_installed(self):
        """NFR-OPA-0002: the module installs on the standard database."""
        module = self.env['ir.module.module'].sudo().search([('name', '=', 'perf_audit')])
        self.assertEqual(module.state, 'installed')

    def test_depends_only_on_base(self):
        """NFR-OPA-0002: no extra dependency is needed to run the audit."""
        self.assertEqual(Manifest.for_addon('perf_audit')['depends'], ['base'])

    def test_install_creates_no_extension(self):
        """NFR-OPA-0002: installing creates no database extension of its own."""
        created = {'pg_stat_statements', 'pgstattuple'} & self.installed_extensions()
        self.assertFalse(created)
