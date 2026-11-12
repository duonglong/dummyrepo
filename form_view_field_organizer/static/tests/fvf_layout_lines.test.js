import { expect, test } from "@odoo/hoot";
import { queryAllTexts, queryAll } from "@odoo/hoot-dom";
import {
    contains,
    defineModels,
    fields,
    models,
    mountView,
} from "@web/../tests/web_test_helpers";
import "@form_view_field_organizer/fvf_layout_lines/fvf_layout_lines";

class FvfGroup extends models.Model {
    _name = "fvf.group";
    name = fields.Char();
}

class FvfLayoutSection extends models.Model {
    _name = "fvf.layout.section";
    name = fields.Char();
    sequence = fields.Integer();
    layout_id = fields.Many2one({ relation: "fvf.layout" });
    _records = [
        { id: 1, name: "Accounting", sequence: 1 },
        { id: 2, name: "Contact", sequence: 2 },
    ];
}

class FvfLayoutLine extends models.Model {
    _name = "fvf.layout.line";
    field_label = fields.Char();
    sequence = fields.Integer();
    missing = fields.Boolean();
    condition_summary = fields.Char();
    section_id = fields.Many2one({ relation: "fvf.layout.section" });
    hidden_group_ids = fields.Many2many({ relation: "fvf.group" });
    layout_id = fields.Many2one({ relation: "fvf.layout" });
    _records = [
        { id: 1, field_label: "Tax ID", sequence: 1, section_id: 1 },
        { id: 2, field_label: "Bank account", sequence: 2, section_id: 1 },
        { id: 3, field_label: "Phone", sequence: 1, section_id: 2 },
        { id: 4, field_label: "Fax", sequence: 1, section_id: false },
        { id: 5, field_label: "Gone", sequence: 2, section_id: false, missing: true },
    ];
}

class FvfLayout extends models.Model {
    _name = "fvf.layout";
    name = fields.Char();
    state = fields.Selection({
        selection: [["draft", "Draft"], ["published", "Published"]],
        default: "draft",
    });
    section_ids = fields.One2many({ relation: "fvf.layout.section", relation_field: "layout_id" });
    line_ids = fields.One2many({ relation: "fvf.layout.line", relation_field: "layout_id" });
    _records = [
        { id: 1, name: "L", state: "draft", section_ids: [1, 2], line_ids: [1, 2, 3, 4, 5] },
        { id: 2, name: "P", state: "published", section_ids: [1, 2], line_ids: [1, 2, 3, 4, 5] },
    ];
}

defineModels([FvfGroup, FvfLayoutSection, FvfLayoutLine, FvfLayout]);

const arch = `
    <form>
        <field name="state" invisible="1"/>
        <field name="section_ids" invisible="1">
            <list><field name="name"/><field name="sequence"/></list>
        </field>
        <field name="line_ids" widget="fvf_layout_lines" readonly="state != 'draft'">
            <list>
                <field name="field_label"/><field name="sequence"/><field name="missing"/>
                <field name="condition_summary"/><field name="section_id"/>
                <field name="hidden_group_ids" widget="many2many_tags"/>
            </list>
        </field>
    </form>`;

const rowLabels = (block) => queryAllTexts(`.o_fvf_line_block:eq(${block}) .o_fvf_line_label`);

test("blocks: original place first, then the sections", async () => {
    await mountView({ type: "form", resModel: "fvf.layout", resId: 1, arch });
    expect(queryAll(".o_fvf_line_block")).toHaveCount(3);
    expect(rowLabels(0)).toEqual(["Fax", "Gone"]);
    expect(rowLabels(1)).toEqual(["Tax ID", "Bank account"]);
    expect(rowLabels(2)).toEqual(["Phone"]);
});

test("drag moves a row within a block", async () => {
    await mountView({ type: "form", resModel: "fvf.layout", resId: 1, arch });
    await contains(".o_fvf_line_block:eq(1) .o_fvf_line_row:eq(1) .o_fvf_line_handle").dragAndDrop(
        ".o_fvf_line_block:eq(1) .o_fvf_line_row:eq(0)"
    );
    expect(rowLabels(1)).toEqual(["Bank account", "Tax ID"]);
});

test("drag moves a row to another block", async () => {
    await mountView({ type: "form", resModel: "fvf.layout", resId: 1, arch });
    await contains(".o_fvf_line_block:eq(2) .o_fvf_line_row:eq(0) .o_fvf_line_handle").dragAndDrop(
        ".o_fvf_line_block:eq(1) .o_fvf_line_row:eq(0)"
    );
    expect(rowLabels(1)).toEqual(["Phone", "Tax ID", "Bank account"]);
    expect(rowLabels(2)).toEqual([]);
});

test("drop into the original-place block clears the section", async () => {
    await mountView({ type: "form", resModel: "fvf.layout", resId: 1, arch });
    await contains(".o_fvf_line_block:eq(2) .o_fvf_line_row:eq(0) .o_fvf_line_handle").dragAndDrop(
        ".o_fvf_line_block:eq(0) .o_fvf_line_row:eq(0)"
    );
    expect(rowLabels(0)).toEqual(["Phone", "Fax", "Gone"]);
    expect(rowLabels(2)).toEqual([]);
});

test("a missing field row shows the text mark", async () => {
    await mountView({ type: "form", resModel: "fvf.layout", resId: 1, arch });
    expect(".o_fvf_line_missing").toHaveCount(1);
    expect(".o_fvf_line_missing").toHaveText("Missing field");
});

test("a published layout shows no drag handle", async () => {
    await mountView({ type: "form", resModel: "fvf.layout", resId: 2, arch });
    expect(".o_fvf_line_row").toHaveCount(5);
    expect(".o_fvf_line_handle").toHaveCount(0);
});
