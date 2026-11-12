from odoo.tests.common import HttpCase, tagged

from .common import FvfCommon

# Page helpers shared by the browser scripts below (plain DOM, no tour framework).
BROWSER_HELPERS = """
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitFor(find, what, timeout = 20000) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
        const found = find();
        if (found) {
            return found;
        }
        await sleep(100);
    }
    throw new Error("Timed out waiting for " + what + " | page: " + document.body.innerText.replace(/\\s+/g, " ").slice(0, 300) + " | " + (document.querySelector(".o_notebook_headers") || {}).outerHTML);
}
const visible = (el) => el && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0;
const textOf = (el) => (el.textContent || "").replace(/\\s+/g, " ").trim();
function findByText(selector, text, root = document) {
    return [...root.querySelectorAll(selector)].find((el) => textOf(el) === text && visible(el));
}
// An icon shows when its element has a size and its font supplies a glyph.
function iconShows(icon) {
    const glyph = getComputedStyle(icon, "::before").content;
    const box = icon.getBoundingClientRect();
    return box.width > 0 && box.height > 0 && glyph && glyph !== "none" && glyph !== "normal" && glyph !== '""';
}
// Leave nothing unsaved: discard the dialog, then the form, as a person would.
async function discardEverything() {
    const dialogDiscard = document.querySelector(".modal .o_form_button_cancel");
    if (dialogDiscard) {
        dialogDiscard.click();
        await waitFor(() => !document.querySelector(".modal"), "the dialog to close");
    }
    const dirty = () => document.querySelector(".o_form_status_indicator_buttons:not(.invisible) .o_form_button_cancel");
    if (dirty()) {
        dirty().click();
        await waitFor(() => !dirty(), "the form to be discarded");
    }
}
async function pickFromInput(input, text) {
    input.focus();
    input.value = text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    return waitFor(
        () => document.querySelector(".o-autocomplete--dropdown-menu .dropdown-item"),
        "a suggestion for " + text
    );
}
async function openLineDialog(layoutId, label) {
    await waitFor(() => document.querySelector(".o_notebook_headers button[name=fields]"), "the Fields tab");
    document.querySelector(".o_notebook_headers button[name=fields]").click();
    const row = await waitFor(
        () => [...document.querySelectorAll(".o_fvf_line_row")].find(
            (r) => textOf(r.querySelector(".o_fvf_line_label")) === label && visible(r)),
        "the line " + label
    );
    row.click();
    return waitFor(() => document.querySelector(".modal .o_form_view"), "the line dialog");
}
"""


@tagged('post_install', '-at_install')
class TestScreenReview(HttpCase, FvfCommon):
    """Screen review findings of the layout editor and the organized form, seen as a user sees them.

    Requirements: FR-FVF-0003, FR-FVF-0004, FR-FVF-0006, FR-FVF-0007, FR-FVF-0016, FR-FVF-0017,
    FR-FVF-0018
    Each test opens the page in a headless browser as the administrator and judges the rendered
    result (sizes, glyphs, dialogs). Clicks are DOM clicks, not real pointer input: dragging of
    rows and the keyboard folding are left to the Hoot tests, and a screenshot is not compared.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.view = cls.env.ref('base.view_partner_form')
        cls.partner = cls.env['res.partner'].create({'name': 'Acme', 'vat': 'BE0123', 'ref': 'REF-1'})

    def _layout(self, section_fields=()):
        layout = self.env['fvf.layout'].create({
            'model_id': self.partner_model.id,
            'view_id': self.view.id,
        })
        if section_fields:
            section = layout.action_add_section('Accounting')
            for name in section_fields:
                layout.line_ids.filtered(lambda line: line.field_name == name).section_id = section
        return layout

    def _run(self, url, body):
        script = "(async () => { try {%s\n%s\nconsole.log('test successful'); } catch (error) { console.error(error.message || String(error)); } })()" % (
            BROWSER_HELPERS, body)
        self.browser_js(url, script, login='admin', timeout=120)

    def _organized_form(self, body):
        layout = self._layout(('vat', 'ref', 'website'))
        layout.action_publish()
        self._run('/odoo/action-base.action_partner_form/%d' % self.partner.id, body)

    def _editor(self, layout, body):
        self._run('/odoo/action-form_view_field_organizer.fvf_layout_action/%d' % layout.id, body)

    def test_bug_b_0188_section_fold_control_shows_a_sign(self):
        """B-0188: each section title has a visible open or closed sign, before and after folding.

        Cannot reach: the keyboard folding (Hoot tests) or the look next to native headers.
        """
        self._organized_form("""
            const toggle = await waitFor(() => document.querySelector(".o_fvf_section_toggle"), "a section title");
            let chevron = toggle.querySelector(".o_fvf_section_chevron");
            if (!iconShows(chevron)) {
                throw new Error("the open sign of the section is not visible");
            }
            toggle.click();
            await sleep(300);
            chevron = document.querySelector(".o_fvf_section_toggle .o_fvf_section_chevron");
            if (!iconShows(chevron)) {
                throw new Error("the closed sign of the section is not visible");
            }
        """)

    def test_bug_b_0188_line_drag_handle_and_missing_mark_show_a_sign(self):
        """B-0188 (same icons in the lines widget): the drag handle and the missing-field mark show a glyph.

        Cannot reach: the drag itself (Hoot tests).
        """
        layout = self._layout()
        self.env['fvf.layout.line'].create({'layout_id': layout.id, 'field_name': 'gone_field'})
        self._editor(layout, """
            await waitFor(() => document.querySelector(".o_notebook_headers button[name=fields]"), "the Fields tab");
            document.querySelector(".o_notebook_headers button[name=fields]").click();
            const handle = await waitFor(() => document.querySelector(".o_fvf_line_handle"), "a drag handle");
            if (!iconShows(handle)) {
                throw new Error("the drag handle shows no sign");
            }
            const mark = await waitFor(() => document.querySelector(".o_fvf_line_missing i"), "the missing mark");
            if (!iconShows(mark)) {
                throw new Error("the missing-field mark shows no sign");
            }
        """)

    def test_bug_b_0189_moved_field_keeps_its_label(self):
        """B-0189: in 'Accounting' the Tax ID value sits right of its own label, as on the original form.

        Cannot reach: the pairing of fields restricted to groups (those are wrapped by the engine).
        """
        self._organized_form("""
            const body = await waitFor(() => document.querySelector(".o_fvf_section_body"), "the section body");
            const input = await waitFor(() => body.querySelector("[name=vat] input"), "the Tax ID input");
            const label = body.querySelector("label[for='" + input.id + "']");
            if (!label) {
                throw new Error("the Tax ID value has no label in the section");
            }
            const labelBox = label.getBoundingClientRect();
            const inputBox = input.getBoundingClientRect();
            if (labelBox.right > inputBox.left + 1 || Math.abs(labelBox.top - inputBox.top) > 30) {
                throw new Error("the Tax ID label is not beside its value");
            }
        """)

    def test_bug_b_0191_field_lines_use_the_whole_tab_with_columns(self):
        """B-0191: the lines widget fills the Fields tab and shows a section column and column titles.

        Cannot reach: the drag of a row (Hoot tests) or a phone width.
        """
        layout = self._layout(('vat',))
        self._editor(layout, """
            await waitFor(() => document.querySelector(".o_notebook_headers button[name=fields]"), "the Fields tab");
            document.querySelector(".o_notebook_headers button[name=fields]").click();
            const widget = await waitFor(() => visible(document.querySelector(".o_fvf_layout_lines")) && document.querySelector(".o_fvf_layout_lines"), "the lines");
            const tab = widget.closest(".tab-pane").getBoundingClientRect();
            const share = widget.getBoundingClientRect().width / tab.width;
            if (share < 0.9) {
                throw new Error("the lines take only " + Math.round(share * 100) + "% of the tab");
            }
            const row = await waitFor(() => [...document.querySelectorAll(".o_fvf_line_row")].find(
                (r) => textOf(r.querySelector(".o_fvf_line_label")) === "Tax ID"), "the Tax ID line");
            const label = row.querySelector(".o_fvf_line_label").getBoundingClientRect();
            if (label.height > 40) {
                throw new Error("the field name wraps over several lines (" + label.height + " px)");
            }
            if (!row.querySelector(".o_fvf_line_section") || textOf(row.querySelector(".o_fvf_line_section")) !== "Accounting") {
                throw new Error("the line does not show its section");
            }
            for (const title of ["Field", "Section", "Hidden from", "Shows when"]) {
                if (!findByText(".o_fvf_line_header span", title, widget)) {
                    throw new Error("no column title " + title);
                }
            }
        """)

    def test_bug_b_0190_refusal_is_shown_in_the_line_dialog_not_in_another_dialog(self):
        """B-0190: hiding the required Name field shows the refusal inside the line dialog, with no second dialog.

        Cannot reach: the refusal of a condition on the field itself (next test) or the saved layout.
        """
        layout = self._layout()
        self._editor(layout, """
            const dialog = await openLineDialog(%d, "Name");
            const input = await waitFor(() => dialog.querySelector("[name=hidden_group_ids] input"), "the groups input");
            const item = await pickFromInput(input, "Role");
            item.click();
            await sleep(1500);
            if (document.querySelectorAll(".modal").length > 1) {
                throw new Error("a second dialog opened: " + textOf(document.querySelectorAll(".modal")[1]));
            }
            const notice = [...dialog.querySelectorAll(".alert-danger")].find((el) => visible(el));
            if (!notice || !textOf(notice).includes("A required field cannot be hidden.")) {
                throw new Error("no refusal beside the groups");
            }
            await discardEverything();
        """ % layout.id)

    def test_bug_b_0190_condition_on_own_field_is_refused_in_the_line_dialog(self):
        """B-0190: a condition on the field itself shows its refusal in the line dialog, with no second dialog.

        Cannot reach: the refusal of hiding a required field (previous test).
        """
        layout = self._layout()
        self._editor(layout, """
            const dialog = await openLineDialog(%d, "Phone");
            const add = await waitFor(() => dialog.querySelector(".o_field_x2many_list_row_add a, .o_field_x2many_list_row_add button"), "Add a line");
            add.click();
            const input = await waitFor(() => dialog.querySelector(".o_data_row [name=field_id] input"), "the condition field input");
            const item = await pickFromInput(input, "Phone");
            item.click();
            await sleep(1500);
            if (document.querySelectorAll(".modal").length > 1) {
                throw new Error("a second dialog opened: " + textOf(document.querySelectorAll(".modal")[1]));
            }
            const notice = [...dialog.querySelectorAll(".text-danger, .alert-danger")].find(
                (el) => visible(el) && textOf(el).includes("A field cannot be shown or hidden based on its own value."));
            if (!notice) {
                throw new Error("no refusal beside the condition");
            }
            await discardEverything();
        """ % layout.id)

    def test_bug_b_0192_groups_picker_is_visible_and_user_facing(self):
        """B-0192: the Hidden from picker is wide, has a placeholder and offers no technical group or Create entry.

        Cannot reach: groups of other modules that carry no scope in their name.
        """
        layout = self._layout()
        self._editor(layout, """
            const dialog = await openLineDialog(%d, "Phone");
            const input = await waitFor(() => dialog.querySelector("[name=hidden_group_ids] input"), "the groups input");
            const width = input.getBoundingClientRect().width;
            const sheet = dialog.querySelector(".o_form_sheet").getBoundingClientRect().width;
            if (width < sheet * 0.5) {
                throw new Error("the groups input is only " + Math.round(width) + " px wide");
            }
            if (!input.placeholder) {
                throw new Error("the groups input has no placeholder");
            }
            input.focus();
            input.click();
            await sleep(1500);
            const entries = [...document.querySelectorAll(".o-autocomplete--dropdown-menu .dropdown-item")].map(textOf);
            const bad = entries.filter((text) => /Is regular user|Bypass HTML|Default access|Create/.test(text));
            if (bad.length) {
                throw new Error("the picker offers " + bad.join(" | "));
            }
        """ % layout.id)

    def test_bug_b_0193_self_condition_refusal_is_fully_readable(self):
        """B-0193: the refusal of a condition on the field itself is shown whole, in a table using the dialog width.

        Cannot reach: a real pointer or keyboard entry of the field; the choice is made by a DOM click on
        the suggestion. Other refusals (required field) have their own test.
        """
        layout = self._layout()
        self._editor(layout, """
            const dialog = await openLineDialog(%d, "Phone");
            const add = await waitFor(() => dialog.querySelector(".o_field_x2many_list_row_add a, .o_field_x2many_list_row_add button"), "Add a line");
            add.click();
            const input = await waitFor(() => dialog.querySelector(".o_data_row [name=field_id] input"), "the condition field input");
            const item = await pickFromInput(input, "Phone");
            item.click();
            await sleep(1500);
            const message = "A field cannot be shown or hidden based on its own value.";
            const notice = [...dialog.querySelectorAll(".o_data_row [name=refusal_notice], .alert-danger")].find(
                (el) => visible(el) && textOf(el).includes(message));
            if (!notice) {
                throw new Error("no refusal beside the condition");
            }
            if (notice.scrollWidth > notice.clientWidth + 1) {
                throw new Error("the refusal is cut off: " + notice.clientWidth + " px shown of " + notice.scrollWidth);
            }
            const table = dialog.querySelector(".o_field_x2many_list .o_list_renderer").getBoundingClientRect().width;
            const content = dialog.querySelector(".alert-info").getBoundingClientRect().width;
            if (table < content * 0.95) {
                throw new Error("the condition table uses only " + Math.round(100 * table / content) + "%% of the dialog width");
            }
            await discardEverything();
        """ % layout.id)
