from odoo.tests.common import TransactionCase


class PerfAuditCase(TransactionCase):
    """Base of the module tests: a system administrator and a plain internal user."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.audit_admin = cls.env['res.users'].create({
            'name': 'Perf Audit Administrator',
            'login': 'perf_audit_admin',
            'group_ids': [(6, 0, [cls.env.ref('base.group_system').id])],
        })
        cls.audit_user = cls.env['res.users'].create({
            'name': 'Perf Audit Plain User',
            'login': 'perf_audit_user',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    def installed_extensions(self):
        """Names of the PostgreSQL extensions of the test database."""
        self.env.cr.execute('SELECT extname FROM pg_extension')
        return {name for (name,) in self.env.cr.fetchall()}
