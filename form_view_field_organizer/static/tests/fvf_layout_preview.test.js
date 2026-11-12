import { expect, test } from "@odoo/hoot";
import { queryOne } from "@odoo/hoot-dom";
import { defineModels, fields, getService, mountWithCleanup, models, onRpc } from "@web/../tests/web_test_helpers";
import { WebClient } from "@web/webclient/webclient";

import "@form_view_field_organizer/fvf_layout_preview/fvf_layout_preview";

class Partner extends models.Model {
    _name = "res.partner";

    name = fields.Char();

    _records = [{ id: 1, name: "Acme" }];
}

defineModels([Partner]);

/** Requirements: FR-FVF-0010, FR-FVF-0011 */
test("the preview action shows the banner and a read-only form", async () => {
    onRpc("fvf.layout.preview", "get_preview_view", () => ({
        arch: `<form edit="0" create="0" delete="0" duplicate="0"><field name="name"/></form>`,
        fields: { name: { name: "name", type: "char", string: "Name" } },
        relatedModels: { "res.partner": { fields: { name: { name: "name", type: "char", string: "Name" } } } },
        res_model: "res.partner",
        res_id: 1,
        layout_id: 7,
        banner: "Preview (read-only) as Sales / User in BE Company - Acme",
    }));
    await mountWithCleanup(WebClient);
    await getService("action").doAction({
        type: "ir.actions.client",
        tag: "fvf_layout_preview",
        params: { preview_id: 1 },
    });
    expect(queryOne(".o_fvf_preview_text")).toHaveText("Preview (read-only) as Sales / User in BE Company - Acme");
    expect(".o_fvf_preview_back").toHaveCount(1);
    expect(".o_fvf_preview_publish").toHaveCount(1);
    expect(".o_form_view .o_field_widget[name=name]").toHaveCount(1);
    expect(".o_form_view input").toHaveCount(0);
});
