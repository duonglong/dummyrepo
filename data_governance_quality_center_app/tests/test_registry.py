from unittest.mock import patch

from odoo.tests import tagged

from .common import DGQTestCase


@tagged('post_install', '-at_install')
class TestRegistry(DGQTestCase):
    """Requirements: NFR-DGQ-0010"""

    def setUp(self):
        super().setUp()
        self.registry_model = self.env['data.quality.registry']

    def _extend(self, method, descriptors):
        """Extend one registry method the documented way: ``super()`` plus a list."""
        original = type(self.registry_model)

        def extended(this):
            return getattr(super(original, this), method)() + descriptors

        return patch.object(original, method, extended, create=True)

    def test_default_registry_is_empty(self):
        """No extension is registered until a module adds one; the app provides the correction actions."""
        for kind in ('validators', 'merge_strategies'):
            self.assertEqual(self.registry_model._dgq_descriptors(kind), [])
        keys = {descriptor['key'] for descriptor in self.registry_model._dgq_descriptors('correction_actions')}
        self.assertEqual(keys, {
            'default', 'trim', 'normalize_email', 'normalize_phone', 'text_case', 'country_state',
            'replace_link'})

    def test_validator_example(self):
        """A module adds a validator with super(); it is found, listed and callable."""
        handler = lambda env, rule, records: {r.id: 'bad' for r in records}  # noqa: E731
        descriptor = {'key': 'example', 'label': 'Example', 'handler': handler}
        with self._extend('_dgq_validators', [descriptor]):
            found = self.registry_model._dgq_find('validators', 'example')
            self.assertEqual(found['handler'](self.env, None, self.env.user), {self.env.user.id: 'bad'})
            self.assertEqual(self.registry_model._dgq_selection('validators'), [('example', 'Example')])
        self.assertIsNone(self.registry_model._dgq_find('validators', 'example'))

    def test_merge_strategy_and_correction_action_examples(self):
        """Merge strategies and correction actions use the same contract."""
        strategy = {'key': 'by_model', 'label': 'By model', 'handler': lambda *a: None}
        action = {'key': 'my_action', 'label': 'My action', 'handler': lambda *a: None}
        with self._extend('_dgq_merge_strategies', [strategy]), \
                self._extend('_dgq_correction_actions', [action]):
            self.assertEqual(self.registry_model._dgq_find('merge_strategies', 'by_model'), strategy)
            self.assertEqual(self.registry_model._dgq_find('correction_actions', 'my_action'), action)

    def test_duplicate_key_refused(self):
        """Two extensions of one kind with the same key are refused."""
        descriptor = {'key': 'same', 'label': 'A', 'handler': None}
        with self._extend('_dgq_validators', [descriptor, dict(descriptor, label='B')]):
            with self.assertRaisesRegex(ValueError, "validators.*same"):
                self.registry_model._dgq_descriptors('validators')

    def test_unknown_key_is_not_available(self):
        """A key left by an uninstalled module resolves to None, not an error."""
        self.assertIsNone(self.registry_model._dgq_find('validators', 'gone'))
