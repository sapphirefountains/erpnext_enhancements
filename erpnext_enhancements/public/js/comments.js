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
                showAddCommentDialog() {
                    let dialog = new frappe.ui.Dialog({
                        title: 'New Note',
                        fields: [
                            {
                                label: 'Note',
                                fieldname: 'comment_text',
                                fieldtype: 'Text Editor',
                                reqd: 1
                            }
                        ],
                        primary_action_label: 'Submit',
                        primary_action: (values) => {
                            if (!values.comment_text.trim()) {
                                frappe.msgprint('Comment cannot be empty.');
                                return;
                            }
                            frappe.call({
                                method: "erpnext_enhancements.api.comments.add_comment",
                                args: {
                                    reference_doctype: frm.doc.doctype,
                                    reference_name: frm.doc.name,
                                    comment_text: values.comment_text,
                                },
                                callback: (r) => {
                                    if (r.message) {
                                        this.comments.unshift(r.message);
                                        dialog.hide();
                                    }
                                },
                            });
                        }
                    });
                    dialog.show();
                },
                showEditCommentDialog(comment) {
                    let dialog = new frappe.ui.Dialog({
                        title: 'Edit Note',
                        fields: [
                            {
                                label: 'Note',
                                fieldname: 'comment_text',
                                fieldtype: 'Text Editor',
                                default: comment.content,
                                reqd: 1
                            }
                        ],
                        primary_action_label: 'Save',
                        primary_action: (values) => {
                            if (!values.comment_text.trim()) {
                                frappe.msgprint('Comment cannot be empty.');
                                return;
                            }
                            frappe.call({
                                method: "erpnext_enhancements.api.comments.update_comment",
                                args: {
                                    comment_name: comment.name,
                                    comment_text: values.comment_text,
                                },
                                callback: (r) => {
                                    if (r.message && !r.message.error) {
                                        const updatedComment = r.message;
                                        const index = this.comments.findIndex(c => c.name === updatedComment.name);
                                        if (index !== -1) {
                                            this.comments.splice(index, 1, updatedComment);
                                        }
                                        dialog.hide();
                                    } else {
                                        frappe.msgprint('There was an error updating the comment.');
                                    }
                                },
                            });
                        }
                    });
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
            template: `
            <div class="project-comments-container">
                <div class="comments-header text-right mb-3" style="display: flex; justify-content: flex-end; padding-bottom: 10px;">
                    <button class="btn btn-default btn-sm" @click="showAddCommentDialog">
                        <span style="font-size: 14px; margin-right: 4px;">+</span> New Note
                    </button>
                </div>
                <div v-if="isLoading" class="text-center p-4">Loading...</div>
                <div v-else-if="comments.length === 0" class="text-center text-muted p-4">
                    No notes yet.
                </div>
                <div v-else class="comments-list">
                    <div v-for="comment in comments" :key="comment.name" class="comment-item d-flex border-bottom py-3" style="display: flex; padding: 15px; border-bottom: 1px solid #eee;">
                        <div class="comment-sidebar mr-4" style="width: 250px; min-width: 250px; margin-right: 20px;">
                            <div class="d-flex align-items-center mb-2" style="display: flex; align-items: center; margin-bottom: 5px;">
                                <span class="avatar avatar-medium mr-2" :title="comment.full_name" style="margin-right: 10px;">
                                    <img :src="comment.user_image" v-if="comment.user_image">
                                    <div class="avatar-frame standard-image" v-else :style="{ backgroundColor: get_palette(comment.full_name) }">
                                        {{ get_abbr(comment.full_name) }}
                                    </div>
                                </span>
                                <div class="font-weight-bold text-truncate" :title="comment.full_name" style="font-weight: 600;">{{ comment.full_name }}</div>
                            </div>
                            <div class="text-muted small" style="color: #888; font-size: 12px; margin-left: 45px;">
                                {{ formatDateTime(comment.creation) }}
                            </div>
                        </div>
                        <div class="comment-content flex-grow-1 text-break" v-html="comment.content" style="flex: 1; margin-right: 15px;"></div>
                        <div class="comment-actions ml-3">
                            <button @click="showEditCommentDialog(comment)" class="btn btn-link btn-xs text-muted" title="Edit Note">
                                <i class="fa fa-pencil"></i>
                            </button>
                            <button @click="deleteComment(comment.name)" class="btn btn-link btn-xs text-muted" title="Delete Note">
                                <i class="fa fa-trash"></i>
                            </button>
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
