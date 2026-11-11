import { registry } from "@web/core/registry";

const RECORD_FORM = ".o_form_view .o_field_widget[name=name] input:value(AM Link Partner)";
const CLEAR_DEFAULT_FILTER = { trigger: ".o_searchview .o_facet_remove", run: "click" };

/**
 * Requirements: UX-AM-001
 * From the list, the row button opens the business record.
 */
registry.category("web_tour.tours").add("am_open_record_row_tour", {
    steps: () => [
        CLEAR_DEFAULT_FILTER,
        {
            trigger: ".o_data_row:contains(link.pdf) button[name=action_open_record]",
            run: "click",
        },
        { trigger: RECORD_FORM },
    ],
});

/**
 * Requirements: UX-AM-001
 * On the attachment form the Open record button opens the business record and the
 * kind of record is plain text, not a link to a technical model record.
 */
registry.category("web_tour.tours").add("am_open_record_form_tour", {
    steps: () => [
        CLEAR_DEFAULT_FILTER,
        { trigger: ".o_data_row:contains(link.pdf) .o_data_cell[name=name]", run: "click" },
        {
            trigger: ".o_form_view .o_field_widget[name=res_model_id]",
            run() {
                if (document.querySelector(".o_field_widget[name=res_model_id] a")) {
                    throw new Error("Kind of Record is a link to a technical record");
                }
            },
        },
        { trigger: ".o_form_statusbar button[name=action_open_record]", run: "click" },
        { trigger: RECORD_FORM },
    ],
});

/**
 * Requirements: UX-AM-001, FR-AM-0006, FR-AM-0007
 * Preview and Download sit on the list row: Preview only on a PDF or image row, Download on
 * every row; the Preview button opens the viewer without leaving the list.
 */
registry.category("web_tour.tours").add("am_row_buttons_tour", {
    steps: () => [
        CLEAR_DEFAULT_FILTER,
        {
            trigger: ".o_data_row:contains(link.pdf) button[name=action_preview]",
            run: "click",
        },
        { trigger: ".o-FileViewer", run: "press Escape" },
        { trigger: ".o_data_row:contains(sheet.xlsx) button[name=action_download]" },
        {
            trigger: ".o_data_row:contains(sheet.xlsx)",
            run() {
                const sheet = [...document.querySelectorAll(".o_data_row")].find((row) =>
                    row.textContent.includes("sheet.xlsx")
                );
                if (sheet.querySelector("button[name=action_preview]")) {
                    throw new Error("A spreadsheet row offers Preview");
                }
            },
        },
    ],
});
