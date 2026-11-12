import { Component, onWillStart, proxy, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { View } from "@web/views/view";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

/**
 * Client action: the draft layout's form for a chosen group, company and record, read-only.
 * Requirements: FR-FVF-0010, FR-FVF-0011
 */
export class FvfLayoutPreview extends Component {
    static template = "form_view_field_organizer.FvfLayoutPreview";
    static components = { View };
    props = useProps({ ...standardActionServiceProps });

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = proxy({ preview: null });
        onWillStart(async () => {
            const previewId = this.props.action.params.preview_id;
            this.state.preview = await this.orm.call("fvf.layout.preview", "get_preview_view", [[previewId]]);
        });
    }

    get viewProps() {
        const { arch, fields, relatedModels, res_model, res_id, company_id } = this.state.preview;
        return {
            type: "form",
            resModel: res_model,
            resId: res_id,
            arch,
            fields,
            relatedModels,
            context: { allowed_company_ids: [company_id] },
            display: { controlPanel: false },
        };
    }

    /** Back to the layout form, which stays a draft. */
    backToEditor() {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "fvf.layout",
            res_id: this.state.preview.layout_id,
            views: [[false, "form"]],
        });
    }

    async publish() {
        await this.orm.call("fvf.layout", "action_publish", [[this.state.preview.layout_id]]);
        return this.backToEditor();
    }
}

registry.category("actions").add("fvf_layout_preview", FvfLayoutPreview);
