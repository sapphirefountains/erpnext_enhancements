/**
 * Procurement purchase-links — supplier purchase URLs per item row.
 *
 * Targets: the "Purchase Order" and "Material Request" doctype forms (and their
 *   item child tables).
 * Loaded via: hooks.py `doctype_js["Purchase Order"]` and
 *   `doctype_js["Material Request"]` (both with vue.global.js + comments.js).
 *
 * For each item row, renders the stored per-supplier purchase links (fetched in
 * bulk via erpnext_enhancements.api.procurement.get_item_links to avoid N+1) into
 * the row's virtual `purchase_links` HTML field, plus an add/edit affordance that
 * saves through erpnext_enhancements.api.procurement.save_item_link. On a PO the
 * links are filtered to the PO's supplier; on a Material Request all suppliers
 * are shown. The add/edit buttons carry their values in data- attributes and one delegated
 * click handler reads them; edit_link stays exposed for anything that calls it directly.
 * Supplier, item code and address are record values: escaped, and an address is a link only
 * when it is an http(s) address.
 */
frappe.provide('erpnext_enhancements.procurement');

// The address unchanged when it is an http(s) address, "" otherwise.
erpnext_enhancements.procurement.safe_href = function(url) {
    const value = String(url == null ? '' : url).trim();
    return /^https?:\/\//i.test(value) ? value : '';
};

// One delegated handler for the add/edit buttons, bound once per page load. It listens in the
// capture phase, so it runs before the grid's own row handlers, as an inline handler would.
erpnext_enhancements.procurement.bind_link_buttons = function() {
    if (erpnext_enhancements.procurement._link_buttons_bound) return;
    erpnext_enhancements.procurement._link_buttons_bound = true;
    document.addEventListener('click', function(e) {
        const btn = e.target && e.target.closest ? e.target.closest('.purchase-links .btn-purchase-link') : null;
        if (!btn) return;
        e.preventDefault();
        erpnext_enhancements.procurement.edit_link(
            btn.getAttribute('data-item-code') || '',
            btn.getAttribute('data-supplier') || '',
            btn.getAttribute('data-url') || ''
        );
    }, true);
};

erpnext_enhancements.procurement.PurchaseLinks = class PurchaseLinks {
    constructor(frm) {
        this.frm = frm;
        this.cache = {};
        // Removed init() call that attached the dangerous listener
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
        erpnext_enhancements.procurement.bind_link_buttons();
        const esc = frappe.utils.escape_html;
        const links = this.cache[row.item_code] || [];
        let html = '<div class="purchase-links" style="display:flex; flex-wrap:wrap; gap:4px;">';

        // 1. Render Supplier Buttons
        links.forEach(link => {
            // Friendly Name: Supplier Name
            const href = erpnext_enhancements.procurement.safe_href(link.url);
            const tag = href ? 'a' : 'span';
            const link_attrs = href ? ` href="${esc(href)}" target="_blank" rel="noopener"` : '';
            html += `<${tag}${link_attrs} class="btn btn-xs btn-default" style="font-size: 10px; padding: 2px 6px;">
                ${esc(link.supplier)} ↗
            </${tag}>`;
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
                 html += `<button class="btn btn-xs btn-default btn-edit-link btn-purchase-link" data-item-code="${esc(row.item_code)}" data-supplier="${esc(this.frm.doc.supplier)}" data-url="${esc(has_link.url)}" title="Edit Link">✎</button>`;
                 show_add = false;
            }
        }

        if (show_add && this.frm.doc.supplier) {
             html += `<button class="btn btn-xs btn-primary btn-purchase-link" data-item-code="${esc(row.item_code)}" data-supplier="${esc(this.frm.doc.supplier)}" data-url="" title="Add Link for ${esc(this.frm.doc.supplier)}">+</button>`;
        }

        html += '</div>';

        // Directly set the rendered HTML to the row (Virtual field)
        row.purchase_links = html;
    }
};

// Opens the "Update Purchase Link" prompt; called by the delegated button handler above.
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

const setup_purchase_links = (frm) => {
    if (!frm.purchase_links) {
        frm.purchase_links = new erpnext_enhancements.procurement.PurchaseLinks(frm);
    }
    frm.purchase_links.render_links();
};

const trigger_render = (frm) => {
    if (frm.purchase_links) frm.purchase_links.render_links();
};

// Attach to forms
frappe.ui.form.on('Purchase Order', {
    refresh: setup_purchase_links,
    supplier: trigger_render
});

frappe.ui.form.on('Purchase Order Item', {
    item_code: trigger_render,
    items_remove: trigger_render
});

frappe.ui.form.on('Material Request', {
    refresh: setup_purchase_links
});

frappe.ui.form.on('Material Request Item', {
    item_code: trigger_render,
    items_remove: trigger_render
});
