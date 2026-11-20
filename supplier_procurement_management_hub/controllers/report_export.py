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
