console.log("[ERPNext Enhancements] Loading Sidebar Enhancements...");

frappe.provide('erpnext_enhancements.sidebar');

(function() {
    const patch_sidebar = () => {
        if (frappe.ui && frappe.ui.form && frappe.ui.form.Sidebar && !frappe.ui.form.Sidebar.prototype._refresh_attachments_patched) {
            console.log("[ERPNext Enhancements] Patching frappe.ui.form.Sidebar.prototype.refresh_attachments");
            
            const original_refresh = frappe.ui.form.Sidebar.prototype.refresh_attachments;
            
            frappe.ui.form.Sidebar.prototype.refresh_attachments = function() {
                // Call original if we want to maintain some base logic, 
                // but since we are overriding the UI entirely, we'll implement our own.
                
                if (!this.frm || !this.frm.doc) return;

                let attachments = (this.frm.get_docinfo ? this.frm.get_docinfo().attachments : this.frm.doc._attachments) || [];
                
                // Find or create the attachments section
                let $section = this.sidebar.find('.sidebar-section[data-section="attachments"]');
                if (!$section.length) {
                    $section = this.sidebar.find('.attachments-actions').closest('.sidebar-section');
                }

                if (!$section.length) {
                    // If still not found, Frappe might have a different structure or it's not rendered yet
                    // We can try to call the original to let it create the section, then we override it
                    original_refresh.apply(this, arguments);
                    $section = this.sidebar.find('.sidebar-section[data-section="attachments"]');
                    if (!$section.length) return;
                }

                // Clear and Rebuild
                $section.empty();
                $section.html(`
                    <div class="sidebar-section-header">
                        <div class="sidebar-label">
                            <svg class="icon icon-sm"><use href="#icon-attachment"></use></svg>
                            ${__('Attachments')}
                        </div>
                    </div>
                    <div class="sidebar-items attachments-items"></div>
                    <div class="sidebar-actions mt-2">
                        <button class="btn btn-default btn-xs w-100" id="btn-open-file-manager" style="font-weight: 500; display: flex; align-items: center; justify-content: center;">
                            <span class="m-r-1">📂</span> ${__('Open File Manager')}
                        </button>
                    </div>
                `);

                let $items = $section.find('.attachments-items');
                let recent = attachments.slice().reverse().slice(0, 5);

                if (recent.length) {
                    recent.forEach(at => {
                        const file_name = at.file_name || at.name;
                        const file_url = at.file_url;
                        const size = at.file_size ? frappe.form.formatters.FileSize(at.file_size) : '';
                        const icon = frappe.utils.icon('attachment', 'sm') || '📎';

                        $items.append(`
                            <div class="sidebar-item" style="display: flex; align-items: center; justify-content: space-between; padding: 4px 0; font-size: var(--text-xs);">
                                <a href="${file_url}" target="_blank" class="text-muted ellipsis" style="display: flex; align-items: center; max-width: 75%; text-decoration: none;" title="${file_name}">
                                    <span class="m-r-1">${icon}</span>
                                    <span class="ellipsis">${file_name}</span>
                                </a>
                                <span class="text-muted" style="font-size: 10px; flex-shrink: 0;">${size}</span>
                            </div>
                        `);
                    });
                } else {
                    $items.append(`<div class="text-muted p-2 text-center" style="font-size: var(--text-xs);">${__('No attachments')}</div>`);
                }

                $section.find('#btn-open-file-manager').on('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    erpnext_enhancements.sidebar.open_file_manager(this.frm);
                });
            };

            frappe.ui.form.Sidebar.prototype._refresh_attachments_patched = true;
        }
    };

    // Try patching immediately
    patch_sidebar();

    // Also hook into app_ready and page change to ensure it stays patched or catches late loads
    $(document).on('app_ready', patch_sidebar);
    $(document).on('page-change', patch_sidebar);
})();

erpnext_enhancements.sidebar.open_file_manager = function(frm) {
    console.log("[ERPNext Enhancements] Opening File Manager for", frm.doctype, frm.docname);
    
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

    if (typeof Vue === 'undefined') {
        dialog.fields_dict.vue_wrapper.$wrapper.html(`<div class="alert alert-danger">${__('Vue 3 is not available.')}</div>`);
        return;
    }

    const app = Vue.createApp({
        template: `
            <div class="file-manager-container" style="min-height: 500px; display: flex; flex-direction: column; font-family: var(--font-stack);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid var(--border-color);">
                    <h4 class="m-0" style="font-weight: 600;">{{ doctype }}: <span class="text-muted">{{ docname }}</span></h4>
                    <div style="display: flex; gap: 10px;">
                        <button class="btn btn-primary btn-sm" @click="trigger_upload">
                            <i class="fa fa-upload m-r-1"></i> ${__('Upload')}
                        </button>
                        <button class="btn btn-default btn-sm" @click="fetch_files">
                            <i class="fa fa-refresh"></i>
                        </button>
                    </div>
                </div>

                <div v-if="loading" class="text-center" style="padding: 100px 0;">
                    <div class="spinner-border text-primary" role="status"></div>
                    <p class="mt-3 text-muted">${__('Fetching documents...')}</p>
                </div>

                <div v-else-if="files.length > 0" 
                     class="file-grid" 
                     style="display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 20px; overflow-y: auto; padding: 5px;">
                    <div v-for="file in files" :key="file.name" 
                         class="file-card shadow-sm border rounded" 
                         style="background: #fff; transition: transform 0.2s; display: flex; flex-direction: column; overflow: hidden;">
                        
                        <div class="preview-box" style="height: 140px; background: #f8f9fa; display: flex; align-items: center; justify-content: center; position: relative; border-bottom: 1px solid #eee;">
                            <img v-if="is_image(file.file_name)" :src="file.file_url" style="width: 100%; height: 100%; object-fit: cover;" />
                            <div v-else class="text-center">
                                <i :class="get_icon_class(file.file_name)" style="font-size: 3rem; color: #adb5bd;"></i>
                                <div class="text-uppercase font-weight-bold mt-2" style="font-size: 10px; color: #6c757d;">{{ get_extension(file.file_name) }}</div>
                            </div>
                        </div>

                        <div class="p-2" style="flex-grow: 1; display: flex; flex-direction: column; justify-content: space-between;">
                            <div class="ellipsis font-weight-bold text-sm" :title="file.file_name">{{ file.file_name }}</div>
                            <div class="text-muted" style="font-size: 11px;">{{ format_size(file.file_size) }}</div>
                            
                            <div class="mt-2 d-flex" style="gap: 5px;">
                                <button class="btn btn-xs btn-default flex-fill" @click="download_file(file)">
                                    <i class="fa fa-download"></i>
                                </button>
                                <button class="btn btn-xs btn-danger flex-fill" @click="delete_file(file)">
                                    <i class="fa fa-trash"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>

                <div v-else class="text-center text-muted" style="padding: 100px 0; border: 2px dashed #ddd; border-radius: 8px;">
                    <i class="fa fa-cloud-upload" style="font-size: 4rem; opacity: 0.2;"></i>
                    <h5 class="mt-3">${__('No files found')}</h5>
                    <p>${__('Drag and drop files anywhere in this window to upload.')}</p>
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
                        fields: ['name', 'file_name', 'file_url', 'file_size'],
                        order_by: 'creation desc'
                    },
                    callback: (r) => {
                        this.files = r.message || [];
                        this.loading = false;
                    }
                });
            },
            is_image(filename) {
                const exts = ['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp'];
                return exts.includes(this.get_extension(filename));
            },
            get_extension(filename) {
                return filename ? filename.split('.').pop().toLowerCase() : '';
            },
            get_icon_class(filename) {
                const ext = this.get_extension(filename);
                if (ext === 'pdf') return 'fa fa-file-pdf-o';
                if (['doc', 'docx'].includes(ext)) return 'fa fa-file-word-o';
                if (['xls', 'xlsx', 'csv'].includes(ext)) return 'fa fa-file-excel-o';
                if (['zip', 'rar', '7z'].includes(ext)) return 'fa fa-file-archive-o';
                return 'fa fa-file-o';
            },
            format_size(size) {
                return frappe.form.formatters.FileSize(size);
            },
            download_file(file) {
                window.open(file.file_url, '_blank');
            },
            delete_file(file) {
                frappe.confirm(__('Delete this file?'), () => {
                    frappe.call({
                        method: 'frappe.client.delete',
                        args: { doctype: 'File', name: file.name },
                        callback: () => {
                            this.fetch_files();
                            frm.reload_docinfo();
                        }
                    });
                });
            },
            trigger_upload() {
                new frappe.ui.FileUploader({
                    doctype: this.doctype,
                    docname: this.docname,
                    on_success: () => {
                        this.fetch_files();
                        frm.reload_docinfo();
                    }
                });
            },
            setup_drag_drop() {
                const el = dialog.get_fields_dict().vue_wrapper.$wrapper[0].closest('.modal-content');
                el.addEventListener('dragover', (e) => { e.preventDefault(); el.style.boxShadow = '0 0 15px var(--primary-color)'; });
                el.addEventListener('dragleave', () => { el.style.boxShadow = ''; });
                el.addEventListener('drop', (e) => {
                    e.preventDefault();
                    el.style.boxShadow = '';
                    if (e.dataTransfer.files.length) {
                        new frappe.ui.FileUploader({
                            doctype: this.doctype,
                            docname: this.docname,
                            files: e.dataTransfer.files,
                            on_success: () => {
                                this.fetch_files();
                                frm.reload_docinfo();
                            }
                        });
                    }
                });
            }
        }
    });

    app.mount(dialog.fields_dict.vue_wrapper.$wrapper[0]);
    dialog.onhide = () => app.unmount();
};
