import ast
import os

from odoo.tests.common import TransactionCase, tagged

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models')


@tagged('post_install', '-at_install')
class TestModelLayout(TransactionCase):
    """Requirements: FR-DLP-003, FR-DLP-007"""

    def _inherited_models(self):
        """Map each models/*.py file to the models it extends."""
        layout = {}
        for name in sorted(os.listdir(MODELS_DIR)):
            if not name.endswith('.py') or name == '__init__.py':
                continue
            with open(os.path.join(MODELS_DIR, name), encoding='utf-8') as handle:
                tree = ast.parse(handle.read())
            found = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id in ('_inherit', '_name')
                        for t in node.targets):
                    if isinstance(node.value, ast.Constant):
                        found.add(node.value.value)
            layout[name] = found
        return layout

    def test_bug_b_0070_one_file_per_model(self):
        """Regression test for B-0070: one file per model, named after the model."""
        layout = self._inherited_models()
        owners = {}
        for name, models_found in layout.items():
            expected = {name[:-3].replace('_', '.')}
            self.assertEqual(
                models_found, expected,
                f"models/{name} must extend only the model it is named after")
            for model in models_found:
                owners.setdefault(model, []).append(name)
        for model, files in owners.items():
            self.assertEqual(len(files), 1, f"{model} is extended in {files}")
