from odoo import api, models

from ..services import correction


class DataQualityRegistry(models.AbstractModel):
    """Documented extension points of the data quality center.

    An installed module adds its own validators, merge strategies or correction
    actions by inheriting this model and extending the list with ``super()``::

        class MyRegistry(models.AbstractModel):
            _inherit = 'data.quality.registry'

            def _dgq_validators(self):
                return super()._dgq_validators() + [{
                    'key': 'my_validator',
                    'label': 'My validator',
                    'handler': my_handler,  # callable(env, rule, records) -> {record id: reason}
                }]

    A descriptor is a dict with a unique ``key``, a ``label`` and a ``handler``
    callable. A rule stores only the key, so uninstalling the providing module
    leaves a key that no longer resolves; ``_dgq_find`` then answers ``None``.

    Requirements: NFR-DGQ-0010
    """
    _name = 'data.quality.registry'
    _description = 'Data quality extension registry'

    @api.model
    def _dgq_validators(self):
        """Custom validators: descriptors ``key``, ``label``, ``handler``."""
        return []

    @api.model
    def _dgq_merge_strategies(self):
        """Merge strategies per model: descriptors ``key``, ``label``, ``handler``.

        The handler contract is defined with the merge engine task.
        """
        return []

    @api.model
    def _dgq_correction_actions(self):
        """Correction actions: descriptors ``key``, ``label``, ``handler``, ``field_types``.

        The handler is ``handler(value, record, params)``: it returns the new value or raises
        ``DgqSkip`` with the reason. The app provides the built-in actions; a module adds its own
        by extending the list with ``super()``.

        Requirements: FR-DGQ-0046
        """
        return correction.builtin_actions(self.env)

    @api.model
    def _dgq_descriptors(self, kind):
        """Descriptors of a kind (validators, merge_strategies, correction_actions).

        Requirements: NFR-DGQ-0010
        """
        getter = {
            'validators': self._dgq_validators,
            'merge_strategies': self._dgq_merge_strategies,
            'correction_actions': self._dgq_correction_actions,
        }[kind]
        descriptors = getter()
        keys = [descriptor['key'] for descriptor in descriptors]
        if len(keys) != len(set(keys)):
            duplicates = sorted({key for key in keys if keys.count(key) > 1})
            raise ValueError(self.env._(
                "Two %(kind)s extensions share the same key: %(keys)s",
                kind=kind, keys=", ".join(duplicates),
            ))
        return descriptors

    @api.model
    def _dgq_find(self, kind, key):
        """The descriptor of a kind with this key, or ``None`` when not available."""
        return next((d for d in self._dgq_descriptors(kind) if d['key'] == key), None)

    @api.model
    def _dgq_selection(self, kind):
        """``(key, label)`` pairs of a kind, for a selection field."""
        return [(d['key'], d['label']) for d in self._dgq_descriptors(kind)]
