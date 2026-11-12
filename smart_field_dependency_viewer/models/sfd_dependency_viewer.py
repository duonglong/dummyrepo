import logging
import time
from typing import Any

from odoo import _, api, fields, models, tools
from odoo.exceptions import AccessError, UserError

from ..engine import graph

_logger = logging.getLogger(__name__)


class SfdDependencyViewer(models.TransientModel):
    _name = "sfd.dependency.viewer"
    _description = "Field dependency viewer"

    model_id = fields.Many2one(
        "ir.model", string="Model", ondelete="cascade", domain=[("abstract", "=", False)])
    field_id = fields.Many2one(
        "ir.model.fields", string="Field", ondelete="cascade",
        domain="[('model_id', '=', model_id)]")
    map_data = fields.Json(string="Dependencies", compute="_compute_map_data")
    depth_limit = fields.Integer(string="Depth limit", compute="_compute_depth_limit")

    @api.depends_context("uid")
    def _compute_depth_limit(self) -> None:
        """Show the current person's depth limit.

        Requirements: FR-SFD-0010
        """
        limit = self.env.user._sfd_effective_depth_limit()
        for viewer in self:
            viewer.depth_limit = limit

    @api.depends("model_id", "field_id")
    def _compute_map_data(self) -> None:
        """Work out the map of the chosen field; nothing is stored.

        Requirements: FR-SFD-0001
        """
        for viewer in self:
            viewer.map_data = (
                self.get_dependency_map(viewer.model_id.model, viewer.field_id.name)
                if viewer.model_id and viewer.field_id else False)

    @api.onchange("model_id")
    def _onchange_model_id(self) -> None:
        """Clear the field when it does not belong to the chosen model.

        Requirements: FR-SFD-0001
        """
        if self.field_id.model_id != self.model_id:
            self.field_id = False

    @api.model
    def get_dependency_map(self, model_name: str, field_name: str) -> dict[str, Any]:
        """Return the dependency map payload of one field.

        Requirements: FR-SFD-0001, FR-SFD-0002, FR-SFD-0003, FR-SFD-0004,
        FR-SFD-0005, FR-SFD-0007, FR-SFD-0010, NFR-SEC-0001, NFR-SEC-0002
        """
        if not self.env.is_system():
            raise AccessError(_("Only settings administrators can open the dependency viewer."))
        registry = self.env.registry
        field = registry[model_name]._fields.get(field_name) if model_name in registry else None
        if field is None:
            raise UserError(_("Unknown model or field: %(model)s.%(field)s", model=model_name, field=field_name))
        started = time.monotonic()
        limit = self.env.user._sfd_effective_depth_limit()
        index = self._sfd_reverse_index()
        dependents = index.get((model_name, field_name), [])
        chain = graph.walk(index, (model_name, field_name), limit) if dependents else {"nodes": [], "links": []}
        payload = {
            "start": {
                "model": model_name,
                "field": field_name,
                "label": field.string or field_name,
                "kind": "related" if field.related else "computed" if field.compute else "plain",
            },
            "depth_limit": limit,
            "own_dependencies": graph.own_dependencies(registry, model_name, field_name),
            "empty": not dependents,
            "truncated": False,
            "dependents": [dict(row) for row in dependents],
            "nodes": chain["nodes"],
            "links": chain["links"],
            "marks": [],
            "svg": "",
        }
        _logger.info("sfd: map %s.%s: %s nodes in %.2fs", model_name, field_name,
                     len(payload["nodes"]), time.monotonic() - started)
        return payload

    @api.model
    @tools.ormcache()
    def _sfd_reverse_index(self) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Return the reverse dependency index, built once per registry load."""
        return graph.build_reverse_index(self.env.registry)
