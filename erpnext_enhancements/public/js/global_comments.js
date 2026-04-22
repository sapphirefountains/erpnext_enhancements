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

        const try_inject = () => {
            if (!this.wrapper) return false;

            // Find the submit button anywhere in the wrapper
            let $submit_btn = this.wrapper.find('.btn-comment');
            if ($submit_btn.length === 0) {
                $submit_btn = this.wrapper.find('.btn-primary').filter((i, el) => $(el).text().trim().toLowerCase() === 'comment');
            }

            let $existing_attach_btn = this.wrapper.find('.btn-attach-file');

            // Re-align and force visibility if button exists
            if ($existing_attach_btn.length > 0) {
                $existing_attach_btn.removeClass('hidden').show().css({'display': 'inline-block'});

                if ($submit_btn.length > 0) {
                    if ($existing_attach_btn.next()[0] !== $submit_btn[0] && $existing_attach_btn.next().next()[0] !== $submit_btn[0]) {
                        $existing_attach_btn.insertBefore($submit_btn);
                        let $file_input = this.wrapper.find('input[type="file"].timeline-file-input');
                        if ($file_input.length) {
                            $file_input.insertBefore($submit_btn);
                        }
                    }
                }
                // Do not return true here! We must keep executing to ensure everything stays aligned
                // but we also don't want to inject *another* button.
            } else {
                // Button doesn't exist, we need to inject it
                let $comment_wrapper = this.wrapper.find('.comment-input-wrapper');

                if ($comment_wrapper.length > 0) {
                    if ($submit_btn.length > 0) {
                        this.inject_attachment_button($comment_wrapper, $submit_btn, true);
                    } else {
                        this.inject_attachment_button($comment_wrapper, null, true);
                    }
                } else {
                    let $timeline_actions = this.wrapper.find('.timeline-message-box .actions');
                    if ($timeline_actions.length === 0 && $submit_btn.length > 0) {
                        $timeline_actions = $submit_btn.parent();
                    }
                    if ($timeline_actions.length > 0) {
                        this.inject_attachment_button($timeline_actions, $submit_btn.length > 0 ? $submit_btn : null, false);
                    }
                }
            }

            // Always return true or nothing since we are continuously observing and patching
            return true;
        };

        // Attempt injection immediately
        try_inject();

        // We use a MutationObserver because the `.actions` div and text editor might be rendered dynamically
        // and also re-rendered when changing tabs or submitting comments.
        const observer = new MutationObserver((mutations) => {
            try_inject();
        });

        if (this.wrapper && this.wrapper[0]) {
            observer.observe(this.wrapper[0], { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style'] });
        }

        // Intercept the submission
        this.intercept_submission();
    };

    frappe.ui.form.Timeline.prototype.inject_attachment_button = function($container, $target_btn, is_new_layout) {
        // Inject the paperclip button next to the Comment button
        const $attach_btn = $(`
            <button class="btn btn-default btn-xs btn-attach-file" style="margin-right: 10px; display: inline-block !important;" title="Attach File">
                <i class="fa fa-paperclip"></i> Attach File
            </button>
        `);

        // Inject a hidden file input
        const $file_input = $(`<input type="file" class="timeline-file-input" multiple style="display: none;">`);

        // Container for showing uploaded files before submission
        let $attachments_preview = $(`<div class="timeline-attachments-preview" style="margin-top: 10px; display: flex; flex-wrap: wrap; gap: 10px; width: 100%;"></div>`);

        if (is_new_layout) {
            // New layout
            if ($target_btn && $target_btn.length > 0) {
                // If comment button is found, insert before it
                $attach_btn.insertBefore($target_btn);
                $file_input.insertBefore($target_btn);

                // Ensure it's never hidden by Frappe logic targeting parent classes
                $attach_btn.removeClass('hidden').show();
                $attach_btn.css({'display': 'inline-block'});

                // Usually in the new layout we want some margin to separate from other elements
                if ($target_btn.css('display') === 'none' || $target_btn.hasClass('hidden')) {
                    // Adjust margin if submit button is hidden initially
                    $attach_btn.css({'margin-left': '48px'});
                }

            } else {
                // If the comment button is not found, we append to the comment box wrapper
                $attach_btn.css({'margin-left': '48px'});
                $container.append($attach_btn);
                $container.append($file_input);
            }
            
            let $input_container = $container.find('.comment-input-container');
            if ($input_container.length > 0) {
                if ($input_container.find('.timeline-attachments-preview').length === 0) {
                    $input_container.append($attachments_preview);
                } else {
                    $attachments_preview = $input_container.find('.timeline-attachments-preview');
                }
            } else {
                if ($container.find('.timeline-attachments-preview').length === 0) {
                    $container.append($attachments_preview);
                } else {
                    $attachments_preview = $container.find('.timeline-attachments-preview');
                }
            }
        } else {
            // Place the elements (old layout)
            $container.prepend($attach_btn);
            $container.append($file_input);

            let $message_box = this.wrapper.find('.timeline-message-box');
            if ($message_box.length === 0) {
                // Fallback for newer Frappe versions or different DOM structure
                $message_box = $container.closest('.timeline-item, .comment-box, .timeline-message-box-wrapper').length > 0 ? 
                               $container.closest('.timeline-item, .comment-box, .timeline-message-box-wrapper') : 
                               $container.parent();
            }

            if ($message_box.find('.timeline-attachments-preview').length === 0) {
                $message_box.append($attachments_preview);
            } else {
                // Already added previously
                $attachments_preview = $message_box.find('.timeline-attachments-preview');
            }
        }

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
    };

    frappe.ui.form.Timeline.prototype.handle_file_uploads = async function(files, $preview_container) {
        let $submit_btn = this.wrapper.find('.btn-comment');
        if ($submit_btn.length === 0) {
            $submit_btn = this.wrapper.find('.timeline-message-box .btn-primary');
        }
        if ($submit_btn.length === 0) {
            // Fallback for newer Frappe versions
            $submit_btn = this.wrapper.find('.btn-primary').filter((i, el) => $(el).text().trim().toLowerCase() === 'comment');
        }

        // Disable submit button while uploading
        if ($submit_btn.length > 0) {
            $submit_btn.prop('disabled', true);
        }

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
            if ($submit_btn.length > 0) {
                $submit_btn.prop('disabled', false);
            }
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
                frappe.show_alert({message: `Failed to upload ${file.name}`, indicator: 'red'});
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
        const self = this;
        
        // We patch the methods on the instance to ensure we capture the correct context
        // and any instance-specific overrides.
        const methods_to_patch = ['insert_comment', 'add_comment'];
        
        methods_to_patch.forEach(method_name => {
            const original_method = this[method_name];
            if (!original_method || original_method._patched_by_erpnext_enhancements) return;

            this[method_name] = function(comment, check_errors) {
                // If we have pending uploads, wait
                if (self._pending_uploads && self._pending_uploads.length > 0) {
                    frappe.msgprint(__("Please wait for file uploads to finish."));
                    return;
                }

                // If we have uploaded files, inject them
                if (self._uploaded_files && self._uploaded_files.length > 0) {
                    let attachment_html = '<br><br><b>Attachments:</b><br><ul>';

                    for (let file of self._uploaded_files) {
                        let escaped_filename = frappe.utils.escape_html ? frappe.utils.escape_html(file.file_name) : $('<div>').text(file.file_name).html();
                        attachment_html += `<li><a href="${file.file_url}" target="_blank">${escaped_filename}</a></li>`;
                    }
                    attachment_html += '</ul>';

                    // Handle different argument types (string or object)
                    if (typeof comment === 'string') {
                        comment += attachment_html;
                    } else if (comment && typeof comment === 'object' && comment.content !== undefined) {
                        comment.content += attachment_html;
                    } else if (comment === undefined || comment === null) {
                        // If comment is missing, try to get it from the comment area
                        if (self.comment_area && self.comment_area.get_value) {
                            comment = self.comment_area.get_value() + attachment_html;
                        } else {
                            comment = attachment_html;
                        }
                    }

                    // If there's a comment area, also update its value to ensure 
                    // that if the original method reads from it directly, it gets the attachments.
                    if (self.comment_area && self.comment_area.set_value) {
                        let current_val = self.comment_area.get_value();
                        if (current_val.indexOf('<b>Attachments:</b>') === -1) {
                            self.comment_area.set_value(current_val + attachment_html);
                        }
                    }
                }

                // Capture the currently uploaded files to link them after submission
                const files_to_link = [...(self._uploaded_files || [])];

                // Call the original method
                const result = original_method.call(this, comment, check_errors);

                // If the result is a promise, chain our linking logic to it
                if (result && typeof result.then === 'function') {
                    result.then(comment_doc => {
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
                }

                // Clear state and UI
                if (files_to_link.length > 0) {
                    self._uploaded_files = [];
                    if (self.wrapper) {
                        self.wrapper.find('.timeline-attachments-preview').empty();
                    }
                }

                return result;
            };
            
            this[method_name]._patched_by_erpnext_enhancements = true;
        });
    };
};

// Initialize when the document is ready
$(document).ready(function() {
    erpnext_enhancements.timeline_attachments.init();
});
