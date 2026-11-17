import { Component, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { MultiRecordSelector } from "@web/core/record_selectors/multi_record_selector";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * Picks records of the rule's model for the rule test (FR-DGQ-0012). Only records the user may
 * read are offered: the picker searches as the user.
 */
export class RecordPicker extends Component {
    static template = "data_governance_quality_center_app.RecordPicker";
    static components = { MultiRecordSelector };
    props = useProps(standardFieldProps);

    get resModel() {
        return this.props.record.data.model_name;
    }

    get resIds() {
        return this.props.record.data[this.props.name] || [];
    }

    update(resIds) {
        this.props.record.update({ [this.props.name]: resIds });
    }
}

registry.category("fields").add("dgq_record_picker", {
    component: RecordPicker,
    displayName: _t("Record picker"),
    supportedTypes: ["json"],
});
