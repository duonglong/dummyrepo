import { Component, proxy, t, useProps } from "@odoo/owl";

export class FvfSection extends Component {
    static template = "form_view_field_organizer.FvfSection";
    props = useProps({
        name: t.string(),
        title: t.string(),
        startClosed: t.boolean().optional(),
        slots: t.object().optional(),
    });

    setup() {
        // The fold state lives for one opening of the form; start_closed gives its first value.
        this.state = proxy({ closed: Boolean(this.props.startClosed) });
    }

    get bodyId() {
        return `fvf_section_body_${this.props.name}`;
    }

    toggle() {
        this.state.closed = !this.state.closed;
    }
}
