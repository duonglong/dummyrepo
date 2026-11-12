import { expect, test } from "@odoo/hoot";
import { queryAllTexts, queryOne } from "@odoo/hoot-dom";
import { contains, defineModels, fields, models, mountView, onRpc } from "@web/../tests/web_test_helpers";

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

/** Requirements: FR-SFD-0010 */
test("a new depth limit is saved and the map redrawn", async () => {
    const calls = [];
    onRpc("res.users", "sfd_set_depth_limit", ({ args }) => {
        calls.push(args[1]);
        return true;
    });
    onRpc("sfd.dependency.viewer", "get_dependency_map", () => ({
        start: { model: "a", field: "quantity" },
        depth_limit: 2,
        own_dependencies: null,
        empty: true,
        dependents: [],
    }));
    await mountView({
        type: "form",
        resModel: "sfd.dependency.viewer",
        resId: 1,
        arch: `<form><field name="map_data" widget="sfd_dependency_map"/></form>`,
    });
    await contains(".o_sfd_depth_limit").edit("2");
    expect(calls).toEqual(["2"]);
    expect(".o_sfd_empty").toHaveCount(1);
});

/** Requirements: FR-SFD-0010 */
test("a refused depth limit is restored", async () => {
    onRpc("res.users", "sfd_set_depth_limit", () => {
        throw new Error("The depth limit must be a positive whole number.");
    });
    await mountView({
        type: "form",
        resModel: "sfd.dependency.viewer",
        resId: 1,
        arch: `<form><field name="map_data" widget="sfd_dependency_map"/></form>`,
    });
    await contains(".o_sfd_depth_limit").edit("0");
    expect(".o_sfd_depth_limit").toHaveValue(5);
    expect(".o_sfd_dependents").toHaveCount(1);
});
