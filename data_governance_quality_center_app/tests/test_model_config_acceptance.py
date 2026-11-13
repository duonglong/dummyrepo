from psycopg2 import IntegrityError

from odoo.exceptions import UserError
from odoo.tests import Form
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


class TestModelConfigAcceptance(TransactionCase):
    """Acceptance tests of T-002 (supported, excluded and restricted models and fields).

    Requirements: FR-DGQ-0075, BR-DGQ-0010.
    The access levels and the company walls of FR-DGQ-0070 / FR-DGQ-0071 are
    not covered here: their scenarios work on issues and rules (later tasks)
    and the spec names no group identifiers.
    """

    def _model(self, name):
        return self.env['ir.model']._get(name)

    def _config(self, name):
        return self.env['data.quality.model.config'].search(
            [('model_id', '=', self._model(name).id)])

    def test_contact_and_product_are_supported_after_install(self):
        """SC-DGQ-0075-1: a fresh installation supports Contact and Product."""
        partner = self._config('res.partner')
        product = self._config('product.template')
        self.assertEqual(len(partner), 1)
        self.assertEqual(partner.status, 'supported')
        self.assertEqual(len(product), 1)
        self.assertEqual(product.status, 'supported')

    def test_model_status_choices(self):
        """FR-DGQ-0075: a model is marked supported, excluded or restricted, nothing else."""
        selection = self.env['data.quality.model.config']._fields['status'].selection
        self.assertEqual({key for key, _label in selection},
                         {'supported', 'excluded', 'restricted'})

    def test_administrator_marks_contact_restricted(self):
        """SC-DGQ-0075-2 (mark step): marking Contact restricted is saved."""
        config = self._config('res.partner')
        with Form(config) as form:
            form.status = 'restricted'
        self.env.flush_all()
        self.assertEqual(self._config('res.partner').status, 'restricted')

    def test_mark_a_new_model_through_the_form(self):
        """FR-DGQ-0075: an administrator adds a mark for another model."""
        with Form(self.env['data.quality.model.config']) as form:
            form.model_id = self._model('res.currency')
            form.status = 'excluded'
        config = self._config('res.currency')
        self.assertEqual(len(config), 1)
        self.assertEqual(config.status, 'excluded')
        self.assertEqual(config.model_name, 'res.currency')

    def test_one_mark_per_model(self):
        """FR-DGQ-0075: a model carries one mark; a second line for it is refused."""
        Config = self.env['data.quality.model.config']
        Config.create({'model_id': self._model('res.country').id, 'status': 'supported'})
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.cr.savepoint():
            Config.create({'model_id': self._model('res.country').id, 'status': 'excluded'})

    def test_model_without_table_is_refused(self):
        """FR-DGQ-0075 (design: real table only): an abstract model cannot be marked."""
        abstract = self._model('mail.thread')
        self.assertTrue(abstract)
        with self.assertRaises(UserError):
            self.env['data.quality.model.config'].create(
                {'model_id': abstract.id, 'status': 'supported'})

    def test_field_status_choices(self):
        """FR-DGQ-0075: a field is marked excluded, sensitive or allowed, nothing else."""
        selection = self.env['data.quality.field.config']._fields['status'].selection
        self.assertEqual({key for key, _label in selection},
                         {'excluded', 'sensitive', 'allowed'})

    def test_administrator_marks_bank_accounts_allowed(self):
        """SC-DGQ-0075-3 (mark step): the Contact field Bank Accounts is marked allowed.

        That the field can then be chosen in a rule belongs to the rule task.
        """
        field = self.env['ir.model.fields']._get('res.partner', 'bank_ids')
        with Form(self.env['data.quality.field.config']) as form:
            form.field_id = field
            form.status = 'allowed'
        mark = self.env['data.quality.field.config'].search([('field_id', '=', field.id)])
        self.assertEqual(len(mark), 1)
        self.assertEqual(mark.status, 'allowed')
        self.assertEqual(mark.model_id, self._model('res.partner'))

    def test_one_mark_per_field(self):
        """FR-DGQ-0075: a field carries one mark; a second line for it is refused."""
        Config = self.env['data.quality.field.config']
        field = self.env['ir.model.fields']._get('res.partner', 'comment')
        Config.create({'field_id': field.id, 'status': 'excluded'})
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.cr.savepoint():
            Config.create({'field_id': field.id, 'status': 'allowed'})
