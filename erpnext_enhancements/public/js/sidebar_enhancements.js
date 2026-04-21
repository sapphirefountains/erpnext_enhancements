frappe.provide('erpnext_enhancements.sidebar');

$(document).on('app_ready', function() {
    if (frappe.ui && frappe.ui.form && frappe.ui.form.Sidebar) {
        // Override the attachment rendering in the form sidebar
        frappe.ui.form.Sidebar.prototype.refresh_attachments = function() {
            let me = this;
            let attachments = this.frm.get_docinfo().attachments || [];

            // Identify the attachment wrapper section in the sidebar
            let $section = this.sidebar.find('.sidebar-section[data-section="attachments"]');
            if (!$section.length) {
                // Fallback for different Frappe versions
                $section = this.sidebar.find('.attachments-actions').closest('.form-sidebar-items, .sidebar-section');
                if (!$section.length && this.attachments) {
                    $section = this.attachments.closest('.sidebar-section');
                }
            }

            if (!$section.length) return; // Safeguard if standard structure drastically changes

            // Clear existing section but rebuild standard wrapper structure
            $section.empty();

            // Rebuild header
            $section.html(`
                <div class="sidebar-section-header">
                    <div class="sidebar-label">
                        <svg class="icon icon-sm"><use href="#icon-attachment"></use></svg>
                        ${__('Attachments')}
                    </div>
                </div>
                <div class="sidebar-items attachments-items"></div>
                <div class="sidebar-actions mt-2">
                    <button class="btn btn-default btn-xs w-100" id="btn-open-file-manager" style="font-weight: 500;">
                        📂 ${__('Open File Manager')}
                    </button>
                </div>
            `);

            let $items = $section.find('.attachments-items');
            
            // Get last 5 attachments
            let recent_attachments = attachments.slice().reverse().slice(0, 5);

            if (recent_attachments.length) {
                recent_attachments.forEach(attachment => {
                    let file_name = attachment.file_name;
                    let file_url = attachment.file_url;
                    let size_str = attachment.file_size ? frappe.form.formatters.FileSize(attachment.file_size) : '';
                    let icon = frappe.utils.icon('attachment') || '<i class="fa fa-paperclip"></i>'; 

                    $items.append(`
                        <div class="sidebar-item" style="display: flex; align-items: center; justify-content: space-between; padding: 4px 0;">
                            <a href="${file_url}" target="_blank" class="text-muted ellipsis" style="display: flex; align-items: center; max-width: 80%; text-decoration: none;" title="${file_name}">
                                <span class="m-r-1" style="display: flex; align-items: center;">${icon}</span>
                                <span class="ellipsis" style="margin-left: 4px;">${file_name}</span>
                            </a>
                            ${size_str ? `<span class="text-muted text-xs">${size_str}</span>` : ''}
                        </div>
                    `);
                });
            } else {
                $items.append(`<div class="text-muted text-small">${__('No attachments')}</div>`);
            }

            // Bind 'Open File Manager' action
            $section.find('#btn-open-file-manager').on('click', () => {
                erpnext_enhancements.sidebar.open_file_manager(me.frm);
            });
        };
    }
});

erpnext_enhancements.sidebar.open_file_manager = function(frm) {
    if (!frm || !frm.doc) return;

    const dialog = new frappe.ui.Dialog({
        title: __('File Manager'),
        size: 'extra-large',
        fields: [
            {
                fieldname: 'vue_wrapper',
                fieldtype: 'HTML'
            }
        ]
    });

    dialog.show();

    // Ensure Vue 3 is globally available as per context
    if (typeof Vue === 'undefined') {
        dialog.fields_dict.vue_wrapper.$wrapper.html(`<div class="alert alert-danger">${__('Vue 3 is not available.')}</div>`);
        return;
    }

    const app = Vue.createApp({
        template: `
            <div class="file-manager-container" style="min-height: 400px; display: flex; flex-direction: column;">
                <!-- Toolbar -->
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                    <h5 class="m-0">Attachments for {{ doctype }} ({{ docname }})</h5>
                    <button class="btn btn-primary btn-sm" @click="trigger_upload">
                        <i class="fa fa-upload"></i> ${__('Upload File')}
                    </button>
                </div>

                <!-- Loading State -->
                <div v-if="loading" class="text-muted text-center" style="padding: 50px 0;">
                    <i class="fa fa-spinner fa-spin mb-2" style="font-size: 3rem;"></i>
                    <p>${__('Loading files...')}</p>
                </div>

                <!-- Grid -->
                <div v-else-if="files.length > 0" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px;">
                    <div v-for="file in files" :key="file.name" class="file-card border rounded p-2" style="display: flex; flex-direction: column; align-items: center; justify-content: space-between; position: relative; background: #fff;">
                        <!-- Preview -->
                        <div class="file-preview" style="height: 120px; display: flex; align-items: center; justify-content: center; margin-bottom: 10px; width: 100%; background: var(--bg-light-gray, #f4f5f6); border-radius: 4px;">
                            <img v-if="is_image(file.file_name)" :src="file.file_url" style="max-height: 100%; max-width: 100%; object-fit: contain; border-radius: 4px;" />
                            <i v-else class="fa fa-file text-muted" style="font-size: 4rem;"></i>
                        </div>
                        
                        <!-- File Info -->
                        <div class="file-info text-center" style="width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-bottom: 5px;" :title="file.file_name">
                            <strong class="text-sm">{{ file.file_name }}</strong>
                            <div class="text-muted small" style="font-size: 0.8rem;">{{ format_size(file.file_size) }}</div>
                        </div>
                        
                        <!-- Actions -->
                        <div class="file-actions" style="display: flex; gap: 5px; justify-content: center; width: 100%;">
                            <button class="btn btn-xs btn-default w-100" @click="download_file(file)" title="Download">
                                <i class="fa fa-download"></i>
                            </button>
                            <button class="btn btn-xs btn-danger w-100" @click="delete_file(file)" title="Delete">
                                <i class="fa fa-trash"></i>
                            </button>
                        </div>
                    </div>
                </div>

                <!-- Empty State -->
                <div v-else class="text-muted text-center" style="padding: 50px 0;">
                    <i class="fa fa-folder-open mb-2" style="font-size: 3rem;"></i>
                    <p>${__("No files attached yet. Click 'Upload File' or drag and drop here.")}</p>
                </div>
            </div>
        `,
        data() {
            return {
                files: [],
                doctype: frm.doctype,
                docname: frm.docname,
                loading: false
            };
        },
        mounted() {
            this.fetch_files();
            this.setup_drag_drop();
        },
        methods: {
            fetch_files() {
                this.loading = true;
                frappe.call({
                    method: 'frappe.client.get_list',
                    args: {
                        doctype: 'File',
                        filters: {
                            attached_to_doctype: this.doctype,
                            attached_to_name: this.docname
                        },
                        fields: ['name', 'file_name', 'file_url', 'file_size', 'is_private'],
                        order_by: 'creation desc'
                    },
                    callback: (r) => {
                        this.files = r.message || [];
                        this.loading = false;
                    }
                });
            },
            is_image(filename) {
                if (!filename) return false;
                const ext = filename.split('.').pop().toLowerCase();
                return ['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp', 'bmp'].includes(ext);
            },
            format_size(size) {
                return size ? frappe.form.formatters.FileSize(size) : '0 KB';
            },
            download_file(file) {
                const url = file.file_url || \`/api/method/frappe.utils.file_manager.download_file?file_name=\${encodeURIComponent(file.name)}\`;
                window.open(url, '_blank');
            },
            delete_file(file) {
                frappe.confirm(__('Are you sure you want to delete this file?'), () => {
                    frappe.call({
                        method: 'frappe.client.delete',
                        args: {
                            doctype: 'File',
                            name: file.name
                        },
                        callback: (r) => {
                            if (!r.exc) {
                                frappe.show_alert({message: __('File Deleted'), indicator: 'green'});
                                this.fetch_files();
                                frm.reload_docinfo(); // Refresh sidebar info
                            }
                        }
                    });
                });
            },
            trigger_upload() {
                new frappe.ui.FileUploader({
                    doctype: this.doctype,
                    docname: this.docname,
                    on_success: (file_doc) => {
                        this.fetch_files();
                        frm.reload_docinfo();
                    }
                });
            },
            setup_drag_drop() {
                // Hook drag and drop to the whole dialog body
                const dialog_body = dialog.get_fields_dict().vue_wrapper.$wrapper[0].closest('.modal-content');
                
                dialog_body.addEventListener('dragover', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    dialog_body.style.backgroundColor = 'var(--highlight-color, #f4f5f6)';
                });
                
                dialog_body.addEventListener('dragleave', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    dialog_body.style.backgroundColor = '';
                });
                
                dialog_body.addEventListener('drop', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    dialog_body.style.backgroundColor = '';
                    
                    if (e.dataTransfer && e.dataTransfer.files.length) {
                        new frappe.ui.FileUploader({
                            doctype: this.doctype,
                            docname: this.docname,
                            files: e.dataTransfer.files,
                            on_success: (file_doc) => {
                                this.fetch_files();
                                frm.reload_docinfo();
                            }
                        });
                    }
                });
            }
        }
    });

    const wrapper = dialog.fields_dict.vue_wrapper.$wrapper[0];
    app.mount(wrapper);

    dialog.onhide = () => {
        app.unmount();
    };
};
