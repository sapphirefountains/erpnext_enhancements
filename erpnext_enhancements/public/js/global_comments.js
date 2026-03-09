// global_comments.js

// 1. Monkey-patch the Timeline to inject the attachment button
frappe.provide('erpnext_enhancements.timeline_attachments');

erpnext_enhancements.timeline_attachments.init = function() {
    if (!frappe.ui.form.Timeline) return;

    const original_make = frappe.ui.form.Timeline.prototype.make;

    frappe.ui.form.Timeline.prototype.make = function() {
        // Call the original make to let standard Frappe build the DOM
        original_make.apply(this, arguments);

        // Inject our custom UI and logic
        this.setup_attachment_ui();
    };

    frappe.ui.form.Timeline.prototype.setup_attachment_ui = function() {
        // State to track pending and completed uploads
        this._pending_uploads = [];
        this._uploaded_files = []; // { file_id, file_url, file_name }

        // Find the timeline-message-box or timeline-actions area
        const $timeline_actions = this.wrapper.find('.timeline-message-box .actions');
        if ($timeline_actions.length === 0) return;

        // Ensure we don't inject multiple times
        if ($timeline_actions.find('.btn-attach-file').length > 0) return;

        // Inject the paperclip button next to the Comment button
        const $attach_btn = $(`
            <button class="btn btn-default btn-xs btn-attach-file" style="margin-right: 10px;" title="Attach File">
                <i class="fa fa-paperclip"></i>
            </button>
        `);

        // Inject a hidden file input
        const $file_input = $(`<input type="file" multiple style="display: none;">`);

        // Container for showing uploaded files before submission
        const $attachments_preview = $(`<div class="timeline-attachments-preview" style="margin-top: 10px; display: flex; flex-wrap: wrap; gap: 10px;"></div>`);

        // Place the elements
        $timeline_actions.prepend($attach_btn);
        $timeline_actions.append($file_input);
        this.wrapper.find('.timeline-message-box').append($attachments_preview);

        // Bind events
        $attach_btn.on('click', (e) => {
            e.preventDefault();
            $file_input.click();
        });

        $file_input.on('change', async (e) => {
            const files = Array.from(e.target.files);
            if (files.length === 0) return;

            // Reset input so the same file can be selected again if needed
            $file_input.val('');

            await this.handle_file_uploads(files, $attachments_preview);
        });

        // Intercept the submission
        this.intercept_submission();
    };

    frappe.ui.form.Timeline.prototype.handle_file_uploads = async function(files, $preview_container) {
        const $submit_btn = this.wrapper.find('.timeline-message-box .btn-primary');

        // Disable submit button while uploading
        $submit_btn.prop('disabled', true);

        const upload_promises = files.map(file => this.upload_single_file(file, $preview_container));

        this._pending_uploads.push(...upload_promises);

        try {
            await Promise.all(upload_promises);
        } catch (e) {
            console.error("Some file uploads failed", e);
        } finally {
            // Remove resolved promises from pending
            this._pending_uploads = [];
            // Re-enable submit button
            $submit_btn.prop('disabled', false);
        }
    };

    frappe.ui.form.Timeline.prototype.upload_single_file = async function(file, $preview_container) {
        return new Promise((resolve, reject) => {
            const file_id_temp = 'temp_' + frappe.utils.get_random(8);

            // Show loading UI for this file
            const $preview_item = $(`
                <div class="attachment-preview-item" data-temp-id="${file_id_temp}" style="padding: 5px; border: 1px solid #ddd; border-radius: 4px; display: flex; align-items: center; background: #f9f9f9;">
                    <i class="fa fa-spinner fa-spin text-muted" style="margin-right: 5px;"></i>
                    <span class="text-muted" style="font-size: 12px; max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${file.name}</span>
                </div>
            `);
            $preview_container.append($preview_item);

            let form_data = new FormData();
            form_data.append("file", file, file.name);
            form_data.append("is_private", 0);
            form_data.append("folder", "Home/Attachments");

            // Execute the upload via fetch
            fetch('/api/method/upload_file', {
                method: 'POST',
                headers: {
                    'X-Frappe-CSRF-Token': frappe.csrf_token
                },
                body: form_data
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error('Upload failed');
                }
                return response.json();
            })
            .then(data => {
                if (data.message) {
                    const uploaded_file = {
                        file_id: data.message.name,
                        file_url: data.message.file_url,
                        file_name: data.message.file_name
                    };
                    this._uploaded_files.push(uploaded_file);

                    // Update UI to show success and remove button
                    $preview_item.html(`
                        <i class="fa fa-paperclip text-muted" style="margin-right: 5px;"></i>
                        <a href="${uploaded_file.file_url}" target="_blank" style="font-size: 12px; max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-right: 5px;">${uploaded_file.file_name}</a>
                        <button class="btn btn-link btn-xs text-danger btn-remove-attachment" style="padding: 0;">
                            <i class="fa fa-times"></i>
                        </button>
                    `);

                    $preview_item.find('.btn-remove-attachment').on('click', (e) => {
                        e.preventDefault();
                        this.remove_uploaded_file(uploaded_file, $preview_item);
                    });

                    resolve(uploaded_file);
                } else {
                    throw new Error('Invalid response from server');
                }
            })
            .catch(error => {
                $preview_item.remove();
                frappe.show_alert({message: \`Failed to upload \${file.name}\`, indicator: 'red'});
                reject(error);
            });
        });
    };

    frappe.ui.form.Timeline.prototype.remove_uploaded_file = function(file_obj, $preview_item) {
        // Remove from state array
        this._uploaded_files = this._uploaded_files.filter(f => f.file_id !== file_obj.file_id);
        $preview_item.remove();

        // Delete the orphaned file from backend
        frappe.call({
            method: 'frappe.client.delete',
            args: {
                doctype: 'File',
                name: file_obj.file_id
            },
            callback: (r) => {
                // Ignore errors on deletion, garbage collection will handle it if it fails
            }
        });
    };

    frappe.ui.form.Timeline.prototype.intercept_submission = function() {
        const original_insert_comment = this.insert_comment;
        if (!original_insert_comment) return;

        this.insert_comment = function(comment, check_errors) {
            // If we have pending uploads, wait (though button should be disabled)
            if (this._pending_uploads && this._pending_uploads.length > 0) {
                frappe.msgprint("Please wait for file uploads to finish.");
                return;
            }

            // Append uploaded files to the comment payload if any exist
            if (this._uploaded_files && this._uploaded_files.length > 0) {
                let attachment_html = '<div class="timeline-attachments" style="margin-top: 15px; border-top: 1px solid #eee; padding-top: 10px;">';
                attachment_html += '<strong>Attachments:</strong><ul style="list-style-type: none; padding-left: 0; margin-top: 5px;">';

                for (let file of this._uploaded_files) {
                    // Escape file name to prevent XSS
                    let escaped_filename = "";
                    if (frappe.utils && frappe.utils.escape_html) {
                        escaped_filename = frappe.utils.escape_html(file.file_name);
                    } else {
                        escaped_filename = $('<div>').text(file.file_name).html();
                    }

                    attachment_html += \`
                        <li style="margin-bottom: 5px;">
                            <i class="fa fa-paperclip text-muted"></i>
                            <a href="${file.file_url}" target="_blank">${escaped_filename}</a>
                        </li>
                    \`;
                }
                attachment_html += '</ul></div>';

                // Append the HTML to the comment text
                comment += attachment_html;
            }

            // Capture the currently uploaded files to link them after submission
            const files_to_link = [...(this._uploaded_files || [])];

            // Call the original insert_comment which returns a Promise
            const result = original_insert_comment.call(this, comment, check_errors);

            // If the result is a promise, chain our linking logic to it
            if (result && typeof result.then === 'function') {
                result.then(comment_doc => {
                    // 1. Trigger backend linking
                    if (files_to_link.length > 0 && comment_doc && comment_doc.name) {
                        const file_ids = files_to_link.map(f => f.file_id);
                        frappe.call({
                            method: 'erpnext_enhancements.api.comments.link_files_to_comment',
                            args: {
                                file_ids: file_ids,
                                comment_id: comment_doc.name,
                                parent_doctype: comment_doc.reference_doctype,
                                parent_name: comment_doc.reference_name
                            }
                        });
                    }
                });
            } else {
                // Fallback if not a promise (older frappe versions might not return the promise directly,
                // though usually timeline.insert_comment returns frappe.call which is a Promise)
                console.warn("Timeline insert_comment did not return a Promise. Attachments may not link immediately.");
            }

            // Clear state and UI after successful submission initiation
            this._uploaded_files = [];
            if (this.wrapper) {
                this.wrapper.find('.timeline-attachments-preview').empty();
            }

            return result;
        };
    };
};

// Initialize when the document is ready
$(document).ready(function() {
    erpnext_enhancements.timeline_attachments.init();
});
