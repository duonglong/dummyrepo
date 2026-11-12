import { expect, test } from "@odoo/hoot";
import { waitUntil } from "@odoo/hoot-dom";
import { contains, defineModels, fields, models, mountView, patchWithCleanup } from "@web/../tests/web_test_helpers";
import { downloadFile } from "@web/core/network/download";

import "@smart_field_dependency_viewer/sfd_dependency_map/sfd_dependency_map";
import { dataFileContent, exportFileName } from "@smart_field_dependency_viewer/sfd_dependency_map/sfd_export";

const SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20" fill="#f00"/></svg>';
const MAP = {
    start: { model: "res.partner", field: "country_id", kind: "plain" },
    depth_limit: 5,
    own_dependencies: null,
    empty: false,
    dependents: [],
    nodes: [{ id: 0, model: "res.partner", field: "country_id", kind: "plain", level: 0, method: null, related: null }],
    links: [],
    marks: [],
    svg: SVG,
};

class Viewer extends models.Model {
    _name = "sfd.dependency.viewer";

    map_data = fields.Json();

    _records = [
        { id: 1, map_data: MAP },
        { id: 2, map_data: { start: {}, depth_limit: 5, empty: true, dependents: [], svg: "" } },
    ];
}

defineModels([Viewer]);

const ARCH = `<form><field name="map_data" widget="sfd_dependency_map"/></form>`;

/** Requirements: FR-SFD-0011, FR-SFD-0012 */
test("file names name the model and the field", () => {
    expect(exportFileName(MAP, "png")).toBe("res.partner-country_id-dependencies.png");
    expect(exportFileName(MAP, "json")).toBe("res.partner-country_id-dependencies.json");
});

/** Requirements: FR-SFD-0012 */
test("the data file holds the map without the drawing", () => {
    const data = JSON.parse(dataFileContent(MAP));
    expect(data.svg).toBe(undefined);
    expect(data.depth_limit).toBe(5);
    expect(data.nodes).toEqual(MAP.nodes);
});

/** Requirements: FR-SFD-0012 */
test("Export data downloads the JSON file", async () => {
    const downloads = [];
    patchWithCleanup(downloadFile, { _download: (data, name, type) => downloads.push({ data, name, type }) });
    await mountView({ type: "form", resModel: "sfd.dependency.viewer", resId: 1, arch: ARCH });
    await contains(".o_sfd_export_data").click();
    expect(downloads).toHaveLength(1);
    expect(downloads[0].name).toBe("res.partner-country_id-dependencies.json");
    expect(downloads[0].type).toBe("application/json");
});

/** Requirements: FR-SFD-0011 */
test("Export image downloads a PNG file", async () => {
    const downloads = [];
    patchWithCleanup(downloadFile, { _download: (data, name, type) => downloads.push({ data, name, type }) });
    await mountView({ type: "form", resModel: "sfd.dependency.viewer", resId: 1, arch: ARCH });
    await contains(".o_sfd_export_image").click();
    await waitUntil(() => downloads.length > 0);
    expect(downloads[0].name).toBe("res.partner-country_id-dependencies.png");
    expect(downloads[0].type).toBe("image/png");
    expect(downloads[0].data.type).toBe("image/png");
});

/** Requirements: FR-SFD-0007 */
test("both export buttons are disabled on an empty map", async () => {
    await mountView({ type: "form", resModel: "sfd.dependency.viewer", resId: 2, arch: ARCH });
    expect(".o_sfd_export_image").toHaveAttribute("disabled");
    expect(".o_sfd_export_data").toHaveAttribute("disabled");
});
