/**
 * Custom "Comments App" — Vue 3 notes/comments UI for forms.
 *
 * Targets: any doctype form that has a `custom_comments_field` HTML field
 *   (a custom "Comments" tab). Mounted by the various form scripts and by
 *   comments_auto.js (see COMMENT_APP_DOCTYPES).
 * Loaded via: hooks.py `app_include_js` (global) AND per-doctype `doctype_js`
 *   entries — always paired with vue.global.js (the vendored Vue 3 build that
 *   defines window.Vue).
 *
 * Defines erpnext_enhancements.render_comments_app(frm, field_name), which
 * mounts a Vue app into the named HTML field. The app fetches/creates/edits/
 * deletes comments through the erpnext_enhancements.api.comments.* whitelisted
 * methods, supports file attachments (uploaded as standalone File docs then
 * linked via link_files_to_comment), and renders an avatar + content + actions
 * list. Styling lives in desk_enhancements.bundle.css (.project-comments-*).
 *
 * Threads: comments with `custom_parent_comment` render as replies indented
 * under their thread root (single-level, Slack-style — grouping happens in the
 * `threads` computed; the flat `comments` array stays the source of truth).
 * The Reply action reuses the composer pre-filled with a Quill mention blot of
 * the target comment's author — frappe core notifies mentioned users on
 * insert, so the tagged person gets the native bell/email notification.
 * Replies whose root was deleted render under a "(deleted note)" stub.
 *
 * Related files: comments_auto.js (auto-mounts this on many doctypes),
 * global_comments.js (patches the *native* timeline to add an attach button).
 */
frappe.provide("erpnext_enhancements");

erpnext_enhancements.render_comments_app = function(frm, field_name) {
    try {
        if (typeof window.Vue === 'undefined') {
            console.error("Vue is not defined. Please check if vue.global.js is loaded.");
            return;
        }

        if (!frm.fields_dict[field_name]) {
            console.warn(`Field "${field_name}" not found. Skipping Comments App render.`);
            return;
        }

        const comments_wrapper = frm.fields_dict[field_name].wrapper;
        // Use a unique ID based on doc name to avoid collisions if multiple instances (unlikely in form view but good practice)
        // Actually Vue mounts on a DOM element.
        $(comments_wrapper).html('<div class="comments-app-mount-point"></div>');
        const mountPoint = $(comments_wrapper).find('.comments-app-mount-point')[0];

        const app = window.Vue.createApp({
            data() {
                return {
                    comments: [],
                    isLoading: true,
                    frappe: frappe
                };
            },
            created() {
                this.fetchComments();
            },
            methods: {
                get_palette(name) {
                    if (frappe.get_palette) {
                        return frappe.get_palette(name);
                    }
                    // Fallback implementation
                    const colors = [
                        '#ffc4c4', '#ffddc4', '#ffffc4', '#c4ffc4', '#c4ffff', '#c4c4ff', '#e8c4ff', '#ffc4e8',
                        '#e0e0e0', '#d0d0d0'
                    ];
                    let hash = 0;
                    for (let i = 0; i < name.length; i++) {
                        hash = name.charCodeAt(i) + ((hash << 5) - hash);
                    }
                    return colors[Math.abs(hash) % colors.length];
                },
                get_abbr(name) {
                    if (frappe.get_abbr) {
                        return frappe.get_abbr(name);
                    }
                    return name.substring(0, 2);
                },
                fetchComments() {
                    this.isLoading = true;
                    frappe.call({
                        method: "erpnext_enhancements.api.comments.get_comments",
                        args: {
                            reference_doctype: frm.doc.doctype,
                            reference_name: frm.doc.name
                        },
                        callback: (r) => {
                            const comments = r.message || [];
                            this.comments = comments;
                            this.isLoading = false;
                        },
                        error: (r) => {
                            this.isLoading = false;
                            console.error("Failed to fetch comments", r);
                            frappe.msgprint(__("Failed to load comments."));
                        }
                    });
                },
                showReplyDialog(comment) {
                    this.showAddCommentDialog(comment);
                },
                showAddCommentDialog(reply_to = null) {
                    let uploaded_files = [];

                    // Pre-fill a real Quill mention blot for the person being
                    // replied to. ControlTextEditor.set_input round-trips it via
                    // clipboard.convert, which rebuilds the registered mention
                    // embed from the data-* attributes; frappe core's
                    // Comment.after_insert -> notify_mentions then notifies the
                    // tagged user automatically. The trailing &nbsp; gives Quill
                    // a text node so the cursor lands after the mention.
                    const esc = (t) => frappe.utils.escape_html
                        ? frappe.utils.escape_html(t || '')
                        : $('<div>').text(t || '').html();
                    let prefill;
                    if (reply_to) {
                        const display = esc(reply_to.full_name || reply_to.owner);
                        prefill = `<p><span class="mention" data-id="${esc(reply_to.owner)}" data-value="${display}" data-denotation-char="@" data-is-group="false">${display}</span>&nbsp;</p>`;
                    }

                    let dialog = new frappe.ui.Dialog({
                        title: reply_to ? 'Reply' : 'New Note',
                        fields: [
                            {
                                label: 'Note',
                                fieldname: 'comment_text',
                                fieldtype: 'Text Editor',
                                default: prefill || undefined,
                                reqd: 1,
                                enable_mentions: true
                            },
                            {
                                fieldtype: 'HTML',
                                fieldname: 'attachment_preview'
                            }
                        ],
                        primary_action_label: 'Submit',
                        primary_action: (values) => {
                            if (!values.comment_text.trim() && uploaded_files.length === 0) {
                                frappe.msgprint('Comment cannot be empty.');
                                return;
                            }

                            let comment_text = values.comment_text || "";
                            if (uploaded_files.length > 0) {
                                let attachment_html = '<br><br><b>Attachments:</b><br><ul>';
                                for (let file of uploaded_files) {
                                    let fname = file.file_name || file.file_url || "Attachment";
                                    let escaped_filename = frappe.utils.escape_html ? frappe.utils.escape_html(fname) : $('<div>').text(fname).html();
                                    attachment_html += `<li><a href="${file.file_url}" target="_blank">${escaped_filename}</a></li>`;
                                }
                                attachment_html += '</ul>';
                                comment_text += attachment_html;
                            }

                            frappe.call({
                                method: "erpnext_enhancements.api.comments.add_comment",
                                args: {
                                    reference_doctype: frm.doc.doctype,
                                    reference_name: frm.doc.name,
                                    comment_text: comment_text,
                                    // Client resolves to the thread root too (the
                                    // server re-validates); frappe.call drops
                                    // undefined args, so top-level adds are
                                    // identical to before.
                                    parent_comment: reply_to
                                        ? (reply_to.custom_parent_comment || reply_to.name)
                                        : undefined,
                                },
                                callback: (r) => {
                                    if (r.message) {
                                        this.comments.unshift(r.message);

                                        // Link files in background
                                        if (uploaded_files.length > 0 && r.message.name) {
                                            frappe.call({
                                                method: 'erpnext_enhancements.api.comments.link_files_to_comment',
                                                args: {
                                                    file_ids: uploaded_files.map(f => f.file_id),
                                                    comment_id: r.message.name,
                                                    parent_doctype: frm.doc.doctype,
                                                    parent_name: frm.doc.name
                                                }
                                            });
                                        }

                                        dialog.hide();
                                    }
                                },
                            });
                        }
                    });

                    dialog.add_custom_action('Attach File', () => {
                        new frappe.ui.FileUploader({
                            doctype: null, // Avoid locking the current document or user profile
                            docname: null,
                            folder: 'Home/Attachments',
                            on_success: (file_doc) => {
                                uploaded_files.push({
                                    file_id: file_doc.name,
                                    file_url: file_doc.file_url,
                                    file_name: file_doc.file_name
                                });

                                // Update preview area
                                let preview_html = '<div style="margin-top: 10px;"><strong>Attached Files:</strong><ul style="list-style: none; padding-left: 0;">';
                                uploaded_files.forEach(f => {
                                    let escaped_filename = frappe.utils.escape_html ? frappe.utils.escape_html(f.file_name) : $('<div>').text(f.file_name).html();
                                    preview_html += `<li><i class="fa fa-paperclip"></i> <a href="${f.file_url}" target="_blank">${escaped_filename}</a></li>`;
                                });
                                preview_html += '</ul></div>';
                                dialog.get_field('attachment_preview').$wrapper.html(preview_html);
                            }
                        });
                    }, 'fa fa-paperclip');

                    dialog.show();

                    if (reply_to) {
                        // Drop the cursor after the pre-filled mention.
                        setTimeout(() => {
                            const ed = dialog.get_field('comment_text');
                            if (ed && ed.quill) {
                                ed.quill.setSelection(ed.quill.getLength(), 0);
                            }
                        }, 200);
                    }
                },
                showEditCommentDialog(comment) {
                    let uploaded_files = [];
                    let dialog = new frappe.ui.Dialog({
                        title: 'Edit Note',
                        fields: [
                            {
                                label: 'Note',
                                fieldname: 'comment_text',
                                fieldtype: 'Text Editor',
                                default: comment.content,
                                reqd: 1,
                                enable_mentions: true
                            },
                            {
                                fieldtype: 'HTML',
                                fieldname: 'attachment_preview'
                            }
                        ],
                        primary_action_label: 'Save',
                        primary_action: (values) => {
                            if (!values.comment_text.trim() && uploaded_files.length === 0) {
                                frappe.msgprint('Comment cannot be empty.');
                                return;
                            }

                            let comment_text = values.comment_text || "";
                            if (uploaded_files.length > 0) {
                                let attachment_html = '<br><br><b>Attachments:</b><br><ul>';
                                for (let file of uploaded_files) {
                                    let fname = file.file_name || file.file_url || "Attachment";
                                    let escaped_filename = frappe.utils.escape_html ? frappe.utils.escape_html(fname) : $('<div>').text(fname).html();
                                    attachment_html += `<li><a href="${file.file_url}" target="_blank">${escaped_filename}</a></li>`;
                                }
                                attachment_html += '</ul>';
                                comment_text += attachment_html;
                            }

                            frappe.call({
                                method: "erpnext_enhancements.api.comments.update_comment",
                                args: {
                                    comment_name: comment.name,
                                    comment_text: comment_text,
                                },
                                callback: (r) => {
                                    if (r.message && !r.message.error) {
                                        const updatedComment = r.message;
                                        const index = this.comments.findIndex(c => c.name === updatedComment.name);
                                        if (index !== -1) {
                                            this.comments.splice(index, 1, updatedComment);
                                        }

                                        // Link files in background
                                        if (uploaded_files.length > 0) {
                                            frappe.call({
                                                method: 'erpnext_enhancements.api.comments.link_files_to_comment',
                                                args: {
                                                    file_ids: uploaded_files.map(f => f.file_id),
                                                    comment_id: comment.name,
                                                    parent_doctype: frm.doc.doctype,
                                                    parent_name: frm.doc.name
                                                }
                                            });
                                        }

                                        dialog.hide();
                                    } else {
                                        frappe.msgprint('There was an error updating the comment.');
                                    }
                                },
                            });
                        }
                    });

                    dialog.add_custom_action('Attach File', () => {
                        new frappe.ui.FileUploader({
                            doctype: null, // Avoid locking the current document or user profile
                            docname: null,
                            folder: 'Home/Attachments',
                            on_success: (file_doc) => {
                                uploaded_files.push({
                                    file_id: file_doc.name,
                                    file_url: file_doc.file_url,
                                    file_name: file_doc.file_name
                                });

                                // Update preview area
                                let preview_html = '<div style="margin-top: 10px;"><strong>Attached Files:</strong><ul style="list-style: none; padding-left: 0;">';
                                uploaded_files.forEach(f => {
                                    let escaped_filename = frappe.utils.escape_html ? frappe.utils.escape_html(f.file_name) : $('<div>').text(f.file_name).html();
                                    preview_html += `<li><i class="fa fa-paperclip"></i> <a href="${f.file_url}" target="_blank">${escaped_filename}</a></li>`;
                                });
                                preview_html += '</ul></div>';
                                dialog.get_field('attachment_preview').$wrapper.html(preview_html);
                            }
                        });
                    }, 'fa fa-paperclip');

                    dialog.show();
                },
                formatDateTime(datetime) {
                    return frappe.datetime.str_to_user(datetime);
                },
                deleteComment(comment_name) {
                    frappe.confirm("Are you sure you want to delete this note?", () => {
                        frappe.call({
                            method: "erpnext_enhancements.api.comments.delete_comment",
                            args: { comment_name: comment_name },
                            callback: (r) => {
                                if (r.message && r.message.success) {
                                    this.comments = this.comments.filter(c => c.name !== comment_name);
                                }
                            }
                        });
                    });
                },
            },
            computed: {
                /**
                 * Groups the flat comments array into single-level threads:
                 * top-level notes newest-first (API order), replies oldest-first
                 * within each thread. Replies whose root no longer exists group
                 * under a deleted-stub thread sorted by their newest reply.
                 * frappe creation strings (YYYY-MM-DD HH:MM:SS.ffffff) compare
                 * lexicographically, so no Date parsing is needed.
                 */
                threads() {
                    const threads = [];
                    const byRoot = {};
                    for (const c of this.comments) {
                        if (!c.custom_parent_comment) {
                            const t = { key: c.name, parent: c, replies: [], deleted: false };
                            byRoot[c.name] = t;
                            threads.push(t);
                        }
                    }
                    for (const c of this.comments) {
                        if (!c.custom_parent_comment) continue;
                        let t = byRoot[c.custom_parent_comment];
                        if (!t) {
                            t = { key: 'deleted-' + c.custom_parent_comment, parent: null, replies: [], deleted: true };
                            byRoot[c.custom_parent_comment] = t;
                            threads.push(t);
                        }
                        t.replies.push(c);
                    }
                    for (const t of threads) {
                        t.replies.sort((a, b) => String(a.creation).localeCompare(String(b.creation)));
                    }
                    const sortKey = (t) => String(
                        t.parent ? t.parent.creation : t.replies[t.replies.length - 1].creation
                    );
                    threads.sort((a, b) => sortKey(b).localeCompare(sortKey(a)));
                    return threads;
                },
            },
            template: `
            <div class="project-comments-container">
                <div class="comments-header text-right mb-3" style="display: flex; justify-content: flex-end; padding-bottom: 10px;">
                    <button class="btn btn-default btn-sm" @click="showAddCommentDialog()">
                        <span style="font-size: 14px; margin-right: 4px;">+</span> New Note
                    </button>
                </div>
                <div v-if="isLoading" class="text-center p-4">Loading...</div>
                <div v-else-if="comments.length === 0" class="text-center text-muted p-4">
                    No notes yet.
                </div>
                <div v-else class="comments-list">
                    <div v-for="thread in threads" :key="thread.key" class="comment-thread">
                        <div v-if="thread.parent" class="comment-item d-flex py-3" style="display: flex; padding: 15px;">
                            <div class="comment-sidebar mr-4" style="width: 250px; min-width: 250px; margin-right: 20px;">
                                <div class="d-flex align-items-center mb-2" style="display: flex; align-items: center; margin-bottom: 5px;">
                                    <span class="avatar avatar-medium mr-2 comment-avatar" :title="thread.parent.full_name" style="margin-right: 10px; flex-shrink: 0;">
                                        <img :src="thread.parent.user_image" v-if="thread.parent.user_image">
                                        <div class="avatar-frame standard-image" v-else :style="{ backgroundColor: get_palette(thread.parent.full_name) }">
                                            {{ get_abbr(thread.parent.full_name) }}
                                        </div>
                                    </span>
                                    <div class="font-weight-bold text-truncate" :title="thread.parent.full_name" style="font-weight: 600;">{{ thread.parent.full_name }}</div>
                                </div>
                                <div class="text-muted small" style="color: var(--text-muted); font-size: 12px; margin-left: 45px;">
                                    {{ formatDateTime(thread.parent.creation) }}
                                </div>
                            </div>
                            <div class="comment-content flex-grow-1 text-break" v-html="thread.parent.content" style="flex: 1; margin-right: 15px;"></div>
                            <div class="comment-actions ml-3">
                                <button @click="showReplyDialog(thread.parent)" class="btn btn-link btn-xs text-muted" title="Reply">
                                    <i class="fa fa-reply"></i>
                                </button>
                                <button @click="showEditCommentDialog(thread.parent)" class="btn btn-link btn-xs text-muted" title="Edit Note">
                                    <i class="fa fa-pencil"></i>
                                </button>
                                <button @click="deleteComment(thread.parent.name)" class="btn btn-link btn-xs text-muted" title="Delete Note">
                                    <i class="fa fa-trash"></i>
                                </button>
                            </div>
                        </div>
                        <div v-else class="comment-deleted-stub">(deleted note)</div>
                        <div v-if="thread.replies.length" class="comment-replies">
                            <div v-for="reply in thread.replies" :key="reply.name" class="comment-reply-item">
                                <span class="avatar avatar-small comment-avatar" :title="reply.full_name">
                                    <img :src="reply.user_image" v-if="reply.user_image">
                                    <div class="avatar-frame standard-image" v-else :style="{ backgroundColor: get_palette(reply.full_name) }">
                                        {{ get_abbr(reply.full_name) }}
                                    </div>
                                </span>
                                <div class="comment-reply-main">
                                    <div class="comment-reply-header">
                                        <span class="commenter-name">{{ reply.full_name }}</span>
                                        <span class="comment-time">{{ formatDateTime(reply.creation) }}</span>
                                    </div>
                                    <div class="comment-content text-break" v-html="reply.content"></div>
                                </div>
                                <div class="comment-actions">
                                    <button @click="showReplyDialog(reply)" class="btn btn-link btn-xs text-muted" title="Reply">
                                        <i class="fa fa-reply"></i>
                                    </button>
                                    <button @click="showEditCommentDialog(reply)" class="btn btn-link btn-xs text-muted" title="Edit Note">
                                        <i class="fa fa-pencil"></i>
                                    </button>
                                    <button @click="deleteComment(reply.name)" class="btn btn-link btn-xs text-muted" title="Delete Note">
                                        <i class="fa fa-trash"></i>
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `,
        });
        app.mount(mountPoint);
    } catch (e) {
        console.error("Error in render_comments_section:", e);
    }
};
