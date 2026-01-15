frappe.provide('erpnext_enhancements.procurement');

erpnext_enhancements.procurement.PurchaseLinks = class PurchaseLinks {
    constructor(frm) {
        this.frm = frm;
        this.cache = {};
        this.init();
    }

    init() {
        // Refresh links when the form loads or items change
        this.frm.fields_dict['items'].grid.wrapper.on('change', () => this.render_links());
    }

    async load_links() {
        const items = this.frm.doc.items.map(i => i.item_code).filter(Boolean);
        if (items.length === 0) return;

        // On PO, we filter by the PO's supplier (Option A)
        // On Material Request, we pass null to get all suppliers
        const supplier_filter = this.frm.doc.doctype === 'Purchase Order' ? this.frm.doc.supplier : null;

        const r = await frappe.call({
            method: 'erpnext_enhancements.api.procurement.get_item_links',
            args: {
                item_codes: items,
                supplier: supplier_filter
            }
        });

        this.cache = r.message || {};
    }

    render_links() {
        if (!this.frm.doc.items) return;
        
        // We load links in bulk to avoid N+1 queries
        this.load_links().then(() => {
            this.frm.doc.items.forEach(row => {
                this.update_row_html(row);
            });
            this.frm.refresh_field('items');
        });
    }

    update_row_html(row) {
        const links = this.cache[row.item_code] || [];
        let html = '<div class="purchase-links" style="display:flex; flex-wrap:wrap; gap:4px;">';

        // 1. Render Supplier Buttons
        links.forEach(link => {
            // Friendly Name: Supplier Name
            html += `<a href="${link.url}" target="_blank" class="btn btn-xs btn-default" style="font-size: 10px; padding: 2px 6px;">
                ${link.supplier} ↗
            </a>`;
        });

        // 2. Render Add/Edit Button (Maintenance Option B)
        // Only show if we have a specific context (like a set Supplier on PO) 
        // OR if it's a Material Request (allow adding for any?)
        // For simplicity: On PO, if no link exists for THIS supplier, show [+].
        
        let show_add = true;
        
        if (this.frm.doc.doctype === 'Purchase Order' && this.frm.doc.supplier) {
            // If we already have a link for this supplier, maybe don't show add? 
            // Or show it to allow editing? Let's show a small edit icon if exists, or + if not.
            const has_link = links.find(l => l.supplier === this.frm.doc.supplier);
            
            if (has_link) {
                 html += `<button class="btn btn-xs btn-default btn-edit-link" onclick="erpnext_enhancements.procurement.edit_link('${row.item_code}', '${this.frm.doc.supplier}', '${has_link.url}')" title="Edit Link">✎</button>`;
                 show_add = false;
            }
        }

        if (show_add && this.frm.doc.supplier) {
             html += `<button class="btn btn-xs btn-primary" onclick="erpnext_enhancements.procurement.edit_link('${row.item_code}', '${this.frm.doc.supplier}', '')" title="Add Link for ${this.frm.doc.supplier}">+</button>`;
        }

        html += '</div>';

        // Directly set the rendered HTML to the row (Virtual field)
        row.purchase_links = html;
    }
};

// Global helper for the onclick events in HTML
erpnext_enhancements.procurement.edit_link = function(item_code, supplier, current_url) {
    if(!supplier || supplier === 'undefined') {
        frappe.msgprint("Please select a Supplier on the form first.");
        return;
    }

    frappe.prompt([
        {
            label: 'Supplier',
            fieldname: 'supplier',
            fieldtype: 'Link',
            options: 'Supplier',
            default: supplier,
            read_only: 1
        },
        {
            label: 'Purchase URL',
            fieldname: 'url',
            fieldtype: 'Data',
            default: current_url,
            reqd: 1
        }
    ], (values) => {
        frappe.call({
            method: 'erpnext_enhancements.api.procurement.save_item_link',
            args: {
                item_code: item_code,
                supplier: values.supplier,
                url: values.url
            },
            callback: (r) => {
                frappe.show_alert({message: 'Link Saved', indicator: 'green'});
                // Refresh the form to reload links
                cur_frm.trigger('refresh'); 
            }
        });
    }, 'Update Purchase Link');
};
// Attach to forms
frappe.ui.form.on('Purchase Order', {
    refresh: function(frm) {
        new erpnext_enhancements.procurement.PurchaseLinks(frm);
    }
});

frappe.ui.form.on('Material Request', {
    refresh: function(frm) {
        new erpnext_enhancements.procurement.PurchaseLinks(frm);
    }
});
