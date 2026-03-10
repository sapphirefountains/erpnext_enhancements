frappe.provide('erpnext_enhancements.crm_notes');

erpnext_enhancements.crm_notes.init = function() {
    if (!frappe.ui.Dialog) return;

    // We patch frappe.ui.Dialog to intercept when the "Add a Note" dialog is created.
    // By patching the constructor, we can catch it as it's being built.
    const original_Dialog = frappe.ui.Dialog;

    frappe.ui.Dialog = function(opts) {
        // Intercept initialization parameters if it's the target dialog
        if (opts && opts.title && (opts.title.includes('Add a Note') || opts.title.includes('Edit Note')) && opts.fields) {
            let has_note_field = opts.fields.some(f => f.fieldname === 'note');
            if (has_note_field) {
                // Add an HTML field for attachments preview if it doesn't exist
                if (!opts.fields.some(f => f.fieldname === 'attachment_preview')) {
                    opts.fields.push({
                        fieldtype: 'HTML',
                        fieldname: 'attachment_preview'
                    });
                }

                // Wrap the primary action
                const original_primary_action = opts.primary_action;
                if (original_primary_action) {
                    opts.primary_action = function(values) {
                        if (this.uploaded_files_for_crm_note && this.uploaded_files_for_crm_note.length > 0) {
                            let note_text = values.note || "";

                            let attachment_html = '<div class="timeline-attachments" style="margin-top: 15px; border-top: 1px solid #eee; padding-top: 10px;">';
                            attachment_html += '<strong>Attachments:</strong><ul style="list-style-type: none; padding-left: 0; margin-top: 5px;">';

                            for (let file of this.uploaded_files_for_crm_note) {
                                let escaped_filename = frappe.utils.escape_html ? frappe.utils.escape_html(file.file_name) : $('<div>').text(file.file_name).html();
                                attachment_html += `
                                    <li style="margin-bottom: 5px;">
                                        <i class="fa fa-paperclip text-muted"></i>
                                        <a href="${file.file_url}" target="_blank">${escaped_filename}</a>
                                    </li>
                                `;
                            }
                            attachment_html += '</ul></div>';

                            values.note = note_text + attachment_html;

                            if (this.fields_dict && this.fields_dict.note && this.fields_dict.note.set_value) {
                                this.fields_dict.note.set_value(values.note);
                            }
                        }

                        const result = original_primary_action.apply(this, [values]);

                        if (this.uploaded_files_for_crm_note && this.uploaded_files_for_crm_note.length > 0 && typeof cur_frm !== 'undefined' && cur_frm) {
                            frappe.call({
                                method: 'erpnext_enhancements.api.comments.link_files_to_comment',
                                args: {
                                    file_ids: this.uploaded_files_for_crm_note.map(f => f.file_id),
                                    comment_id: 'crm-note',
                                    parent_doctype: cur_frm.doc.doctype,
                                    parent_name: cur_frm.doc.name
                                }
                            });
                        }

                        return result;
                    };
                }
            }
        }

        // Call the original constructor
        const dialog = new original_Dialog(opts);

        // If it was our note dialog, add the custom action now that the dialog is instantiated
        if (opts && opts.title && (opts.title.includes('Add a Note') || opts.title.includes('Edit Note')) && opts.fields && opts.fields.some(f => f.fieldname === 'note')) {
            dialog.uploaded_files_for_crm_note = [];

            // Try to add the custom action safely
            if (typeof dialog.add_custom_action === 'function') {
                dialog.add_custom_action('Attach File', () => {
                    new frappe.ui.FileUploader({
                        doctype: typeof cur_frm !== 'undefined' && cur_frm ? cur_frm.doc.doctype : 'CRM Note',
                        docname: typeof cur_frm !== 'undefined' && cur_frm ? cur_frm.doc.name : 'new-crm-note',
                        folder: 'Home/Attachments',
                        on_success: (file_doc) => {
                            dialog.uploaded_files_for_crm_note.push({
                                file_id: file_doc.name,
                                file_url: file_doc.file_url,
                                file_name: file_doc.file_name
                            });

                            let preview_html = '<div style="margin-top: 10px;"><strong>Attached Files:</strong><ul style="list-style: none; padding-left: 0;">';
                            dialog.uploaded_files_for_crm_note.forEach(f => {
                                let escaped_filename = frappe.utils.escape_html ? frappe.utils.escape_html(f.file_name) : $('<div>').text(f.file_name).html();
                                preview_html += `<li><i class="fa fa-paperclip"></i> <a href="${f.file_url}" target="_blank">${escaped_filename}</a></li>`;
                            });
                            preview_html += '</ul></div>';

                            if (dialog.fields_dict && dialog.fields_dict.attachment_preview && dialog.get_field) {
                                let field = dialog.get_field('attachment_preview');
                                if (field && field.$wrapper) {
                                    field.$wrapper.html(preview_html);
                                }
                            }
                        }
                    });
                }, 'fa fa-paperclip');
            }
        }

        return dialog;
    };

    // Keep the prototype chain intact
    frappe.ui.Dialog.prototype = original_Dialog.prototype;
};

$(document).ready(function() {
    erpnext_enhancements.crm_notes.init();
});
