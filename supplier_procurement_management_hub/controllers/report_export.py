import json

from odoo.http import request, route

from odoo.addons.web.controllers.export import ExcelExport


class SpmExcelExport(ExcelExport):
    """The XLSX export of a report list, which a caller may send with only the names of its columns.

    Requirements: FR-SPM-0104
    The file holds what the native export holds for the domain it is given and the rights of the
    caller; only the missing column types are read from the model, so numbers get their format.
    """

    @route()
    def web_export_xlsx(self, data):
        """Fill the type of every column that came without one, then export as usual."""
        try:
            payload = json.loads(data)
            model = request.env.get(payload.get('model'))
            columns = payload.get('fields') or []
        except (ValueError, AttributeError, TypeError):
            return super().web_export_xlsx(data)
        if model is not None and columns:
            types = model.fields_get(attributes=['type'])
            for column in columns:
                if isinstance(column, dict) and not column.get('type'):
                    column['type'] = types.get(str(column.get('name', '')).split('/')[0], {}).get('type', 'char')
            data = json.dumps(payload)
        return super().web_export_xlsx(data)

    def base(self, data):
        """Remember the rows the export covers, so a grouped file can read its group totals from the model."""
        params = json.loads(data)
        ids = params.get('ids')
        request.spm_export_scope = [('id', 'in', ids)] if ids else params.get('domain') or []
        return super().base(data)

    def from_group_data(self, fields, columns_headers, groups):
        """Write each group's totals as the model answers them, never as a sum of the exported cells.

        Requirements: FR-SPM-0102, FR-SPM-0103, FR-SPM-0104
        The native export adds up the cells of a group in Python, which totals savings of different
        baseline kinds and adds up per-row rates. For a report on ``procurement.report.guard`` the
        totals of every group level are read once per level with the grouping of that level, so the
        file holds the figures of the screen (an empty cell where the screen leaves it empty).
        """
        model = groups._model
        if hasattr(model, '_spm_guarded_aggregates'):
            self._spm_set_group_totals(groups, getattr(request, 'spm_export_scope', []))
        return super().from_group_data(fields, columns_headers, groups)

    def _spm_set_group_totals(self, root, scope):
        """Put the model's aggregates on every node of the group tree."""
        model, groupby = root._model, root._groupby
        names = root._get_aggregated_field_names()
        specs = [f'{name}:{model._fields[name].aggregator}' for name in names]
        if not specs:
            return
        for depth in range(len(groupby) + 1):
            rows = {
                tuple(row.get(spec) for spec in groupby[:depth]): row
                for row in model.formatted_read_group(scope, groupby[:depth], specs)
            }
            for path, node in self._spm_nodes(root, depth):
                row = rows.get(path, {})
                node.__dict__['aggregated_values'] = {
                    name: (None if row.get(spec) is False else row.get(spec)) for name, spec in zip(names, specs)}

    def _spm_nodes(self, node, depth, path=()):
        """Yield ``(path of group values, node)`` for the nodes ``depth`` levels below ``node``."""
        if not depth:
            yield path, node
            return
        for key, child in node.children.items():
            yield from self._spm_nodes(child, depth - 1, path + (key,))
