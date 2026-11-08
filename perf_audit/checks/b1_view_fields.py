"""B1: heavy non-stored fields shown in list and kanban views.

Requirements: FR-OPA-0016
"""
import logging

from lxml import etree

from odoo.exceptions import UserError

from .base import Check, register

_logger = logging.getLogger(__name__)
X2MANY_TYPES = ('one2many', 'many2many')


@register
class ViewFieldsCheck(Check):
    """Report non-stored computed or related fields shown at the top level of a primary list or kanban view.

    Requirements: FR-OPA-0016
    """

    code = 'B1'

    def run(self, ctx):
        """Return one finding per heavy field and view, severity by the B1 decision table.

        Requirements: FR-OPA-0016
        """
        env = ctx.env
        rows = ctx.probe.estimated_rows()
        # the audit reads every view and model whatever the rights of the person who started it
        views = env['ir.ui.view'].sudo().search([
            ('type', 'in', ('list', 'kanban')), ('mode', '=', 'primary'), ('model', 'in', list(env.registry))])
        findings = []
        skipped_views = []
        for view in views:
            Model = env[view.model].sudo()  # same reason: the view is read as the audit, not as the user
            for name, hidden in self._top_level_fields(Model, view, skipped_views):
                field = Model._fields.get(name)
                if field and (field.compute or field.related) and not field.store:
                    findings.append(self._finding(ctx, view, Model, field, hidden, rows.get(Model._table, 0)))
        if skipped_views:
            ctx.outcome['part_skip_reason'] = env._('Views whose final arch could not be read: %s', ', '.join(map(str, skipped_views)))
        return findings

    def _top_level_fields(self, Model, view, skipped_views):
        """(name, hidden by default) once per field, of the final arch that are not in a nested sub-view."""
        try:
            arch = Model.get_views([(view.id, view.type)])['views'][view.type]['arch']
        except (UserError, ValueError, KeyError):
            _logger.warning('B1 could not read the final arch of view %s', view.id, exc_info=True)
            skipped_views.append(view.id)
            return []
        root = etree.fromstring(arch)
        hidden = {}
        for node in root.xpath('.//field[not(ancestor::field)]'):
            # a field repeated in a view (kanban templates) is hidden only when every use is
            hidden[node.get('name')] = hidden.get(node.get('name'), True) and node.get('optional') == 'hide'
        return list(hidden.items())

    def _crosses_x2many(self, Model, field):
        """True when a dependency path of the field goes through a one2many or many2many."""
        for path in field.get_depends(Model)[0]:
            current = Model
            for step in path.split('.'):
                step_field = current._fields.get(step)
                if step_field is None:
                    break
                if step_field.type in X2MANY_TYPES:
                    return True
                current = current.env.get(step_field.comodel_name) if step_field.relational else None
                if current is None:
                    break
        return False

    def _finding(self, ctx, view, Model, field, hidden, rows):
        """The finding of one heavy field in one view."""
        big_table = ctx.thresholds['big_table']
        crosses = self._crosses_x2many(Model, field)
        if hidden:
            severity = 'low'
        elif crosses and rows > big_table:
            severity = 'high'
        else:
            severity = 'medium'
        return {
            'kind': 'heavy_field',
            'identity_key': 'B1:%s.%s:%s' % (Model._name, field.name, view.id),
            'severity': severity,
            'message': 'Non-stored field %s.%s is shown in view %s' % (Model._name, field.name, view.name),
            'table_name': Model._table,
            'row_estimate': rows,
            'model_id': ctx.env['ir.model']._get_id(Model._name),
            'field_id': ctx.env['ir.model.fields']._get(Model._name, field.name).id,
            'view_id': view.id,
            'threshold_label': 'big_table',
            'threshold_value': big_table,
            'found_value': rows,
            'evidence': {
                'hidden_by_default': hidden, 'depends_cross_x2many': crosses,
                'computed': bool(field.compute), 'related': field.related or False, 'view': view.name,
            },
            'code_suggestion': (
                "Set store=True (and index=True when it is filtered, grouped or sorted) on %s.%s, "
                "or add optional=\"hide\" to the field in view '%s'." % (Model._name, field.name, view.name)),
        }
