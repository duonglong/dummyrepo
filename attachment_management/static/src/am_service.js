import { registry } from "@web/core/registry";
import { FileModel } from "@web/core/file_viewer/file_model";

/**
 * Requirements: FR-AM-0006
 * Open an attachment in the native file viewer, on the screen the person is on.
 */
async function previewAttachment(env, action) {
    const [data] = await env.services.orm.read(
        "ir.attachment",
        [action.params.attachment_id],
        ["name", "mimetype", "checksum", "type", "url"]
    );
    const file = Object.assign(new FileModel(), data);
    env.services.fileViewer().open(file);
}

registry.category("actions").add("attachment_management.preview", previewAttachment);
