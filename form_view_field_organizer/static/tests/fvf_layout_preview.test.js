import { expect, test } from "@odoo/hoot";
import { animationFrame, click, queryOne } from "@odoo/hoot-dom";
import { defineModels, fields, getService, mountWithCleanup, models, onRpc } from "@web/../tests/web_test_helpers";
import { WebClient } from "@web/webclient/webclient";

import "@form_view_field_organizer/fvf_layout_preview/fvf_layout_preview";

class Partner extends models.Model {
    _name = "res.partner";

    name = fields.Char();

    _records = [{ id: 1, name: "Acme" }];
}

class FvfLayout extends models.Model {
    _name = "fvf.layout";

    name = fields.Char();

    _records = [{ id: 7, name: "Layout" }];
    _views = { form: `<form><field name="name"/></form>` };
    action_publish() {
        return true;
    }
}

defineModels([Partner, FvfLayout]);

/** Requirements: FR-FVF-0010, FR-FVF-0011 */
test("the preview action shows the banner and a read-only form", async () => {
    onRpc("fvf.layout.preview", "get_preview_view", () => ({
        arch: `<form edit="0" create="0" delete="0" duplicate="0"><field name="name"/></form>`,
        fields: { name: { name: "name", type: "char", string: "Name" } },
        relatedModels: { "res.partner": { fields: { name: { name: "name", type: "char", string: "Name" } } } },
        res_model: "res.partner",
        res_id: 1,
        layout_id: 7,
        company_id: 3,
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

/** Requirements: FR-FVF-0010 */
test("the form is read in the chosen company", async () => {
    onRpc("fvf.layout.preview", "get_preview_view", () => ({
        arch: `<form edit="0"><field name="name"/></form>`,
        fields: { name: { name: "name", type: "char", string: "Name" } },
        relatedModels: { "res.partner": { fields: { name: { name: "name", type: "char", string: "Name" } } } },
        res_model: "res.partner",
        res_id: 1,
        layout_id: 7,
        company_id: 3,
        banner: "Preview",
    }));
    onRpc("res.partner", "web_read", ({ kwargs }) => {
        expect.step(JSON.stringify(kwargs.context.allowed_company_ids));
    });
    await mountWithCleanup(WebClient);
    await getService("action").doAction({
        type: "ir.actions.client",
        tag: "fvf_layout_preview",
        params: { preview_id: 1 },
    });
    expect.verifySteps(["[3]"]);
});

/** Requirements: FR-FVF-0010 */
test("Publish publishes the layout and goes back to it", async () => {
    onRpc("fvf.layout.preview", "get_preview_view", () => ({
        arch: `<form edit="0"><field name="name"/></form>`,
        fields: { name: { name: "name", type: "char", string: "Name" } },
        relatedModels: { "res.partner": { fields: { name: { name: "name", type: "char", string: "Name" } } } },
        res_model: "res.partner",
        res_id: 1,
        layout_id: 7,
        company_id: 3,
        banner: "Preview",
    }));
    onRpc("fvf.layout", "action_publish", ({ args }) => {
        expect.step(`publish ${JSON.stringify(args)}`);
        return true;
    });
    await mountWithCleanup(WebClient);
    await getService("action").doAction({
        type: "ir.actions.client",
        tag: "fvf_layout_preview",
        params: { preview_id: 1 },
    });
    await click(".o_fvf_preview_publish");
    await animationFrame();
    expect.verifySteps(["publish [[7]]"]);
});
