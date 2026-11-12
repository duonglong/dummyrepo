import { expect, test } from "@odoo/hoot";
import { queryAllTexts, queryOne } from "@odoo/hoot-dom";
import { defineModels, fields, models, mountView } from "@web/../tests/web_test_helpers";

import "@smart_field_dependency_viewer/sfd_dependency_map/sfd_dependency_map";

class Viewer extends models.Model {
    _name = "sfd.dependency.viewer";

    map_data = fields.Json();

    _records = [
        {
            id: 1,
            map_data: {
                start: { model: "a", field: "quantity" },
                depth_limit: 5,
                own_dependencies: null,
                empty: false,
                dependents: [
                    {
                        model: "a",
                        field: "subtotal",
                        label: "Subtotal",
                        kind: "computed",
                        stored: true,
                        path: "quantity",
                        method: "_compute_subtotal",
                        related: null,
                    },
                ],
            },
        },
        {
            id: 2,
            map_data: { start: {}, depth_limit: 5, empty: true, dependents: [] },
        },
    ];
}

defineModels([Viewer]);

/** Requirements: FR-SFD-0004, FR-SFD-0007 */
test("dependents table shows the path and the method", async () => {
    await mountView({
        type: "form",
        resModel: "sfd.dependency.viewer",
        resId: 1,
        arch: `<form><field name="map_data" widget="sfd_dependency_map"/></form>`,
    });
    expect(queryAllTexts(".o_sfd_dependents tbody td")).toInclude("quantity");
    expect(queryAllTexts(".o_sfd_dependents tbody td")).toInclude("_compute_subtotal");
});

/** Requirements: FR-SFD-0007 */
test("empty map shows the message and no table", async () => {
    await mountView({
        type: "form",
        resModel: "sfd.dependency.viewer",
        resId: 2,
        arch: `<form><field name="map_data" widget="sfd_dependency_map"/></form>`,
    });
    expect(queryOne(".o_sfd_empty")).toHaveText("No computed or related field depends on this field.");
    expect(".o_sfd_dependents").toHaveCount(0);
});
