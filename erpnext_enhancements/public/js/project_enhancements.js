frappe.ui.form.on('Project', {
    refresh: function (frm) {
        if (!frm.doc.__islocal) {
            frm.trigger('render_procurement_tracker');
        }
    },

    render_procurement_tracker: function (frm) {
        // Check if the custom field exists
        if (!frm.fields_dict['custom_material_request_feed']) {
            console.warn('Field "custom_material_request_feed" not found in Project DocType.');
            return;
        }

        frappe.call({
            method: 'erpnext_enhancements.project_enhancements.get_procurement_status',
            args: {
                project_name: frm.doc.name
            },
            callback: function (r) {
                if (r.message) {
                    const data = r.message;
                    let html = `
                        <style>
                            .procurement-table {
                                width: 100%;
                                border-collapse: collapse;
                                font-size: 12px;
                            }
                            .procurement-table th, .procurement-table td {
                                border: 1px solid #d1d8dd;
                                padding: 8px;
                                text-align: left;
                            }
                            .procurement-table th {
                                background-color: #f8f9fa;
                                font-weight: bold;
                            }
                            .status-complete {
                                color: #28a745;
                                font-weight: bold;
                            }
                            .status-pending {
                                color: #fd7e14;
                                font-weight: bold;
                            }
                        </style>
                        <div class="table-responsive">
                            <table class="table table-bordered procurement-table">
                                <thead>
                                    <tr>
                                        <th>Item Details</th>
                                        <th>Doc Chain</th>
                                        <th>Warehouse</th>
                                        <th>Qty (Ord / Rec)</th>
                                        <th>Status</th>
                                    </tr>
                                </thead>
                                <tbody>
                    `;

                    if (data.length === 0) {
                        html += `<tr><td colspan="5" class="text-center text-muted">No procurement records found.</td></tr>`;
                    } else {
                        data.forEach(row => {
                            // Format Doc Chain
                            let chain = [];
                            if (row.mr) chain.push(`<a href="/app/material-request/${row.mr}">${row.mr}</a> <span class="text-muted">(${row.mr_status})</span>`);
                            if (row.rfq) chain.push(`<a href="/app/request-for-quotation/${row.rfq}">${row.rfq}</a> <span class="text-muted">(${row.rfq_status})</span>`);
                            if (row.sq) chain.push(`<a href="/app/supplier-quotation/${row.sq}">${row.sq}</a> <span class="text-muted">(${row.sq_status})</span>`);
                            if (row.po) chain.push(`<a href="/app/purchase-order/${row.po}">${row.po}</a> <span class="text-muted">(${row.po_status})</span>`);
                            if (row.pr) chain.push(`<a href="/app/purchase-receipt/${row.pr}">${row.pr}</a> <span class="text-muted">(${row.pr_status})</span>`);
                            if (row.pi) chain.push(`<a href="/app/purchase-invoice/${row.pi}">${row.pi}</a> <span class="text-muted">(${row.pi_status})</span>`);

                            const chain_html = chain.join('<br>&darr;<br>');

                            // Status Class
                            const status_class = row.completion_percentage >= 100 ? 'status-complete' : 'status-pending';

                            html += `
                                <tr>
                                    <td>
                                        <a href="/app/item/${row.item_code}"><strong>${row.item_code}</strong></a><br>
                                        <span class="text-muted">${row.item_name}</span>
                                    </td>
                                    <td>${chain_html}</td>
                                    <td>${row.warehouse || '-'}</td>
                                    <td>${row.ordered_qty} / ${row.received_qty}</td>
                                    <td class="${status_class}">
                                        ${row.completion_percentage}% Received
                                    </td>
                                </tr >
                        `;
                        });
                    }

                    html += `
                                </tbody >
                            </table >
                        </div >
                        `;

                    frm.set_df_property('custom_material_request_feed', 'options', html);
                }
            }
        });
    },

    custom_btn_material_request: function (frm) {
        if (frm.is_new()) {
            frappe.msgprint(__('Please save the Project before creating linked documents.'));
            return;
        }
        frappe.new_doc('Material Request', {
            project: frm.doc.name
        });
    },

    custom_btn_request_quote: function (frm) {
        if (frm.is_new()) {
            frappe.msgprint(__('Please save the Project before creating linked documents.'));
            return;
        }
        frappe.new_doc('Request for Quotation', {
            project: frm.doc.name
        });
    },

    custom_btn_supplier_quotation: function (frm) {
        if (frm.is_new()) {
            frappe.msgprint(__('Please save the Project before creating linked documents.'));
            return;
        }
        frappe.new_doc('Supplier Quotation', {
            project: frm.doc.name
        });
    },

    custom_btn_purchase_order: function (frm) {
        if (frm.is_new()) {
            frappe.msgprint(__('Please save the Project before creating linked documents.'));
            return;
        }
        frappe.new_doc('Purchase Order', {
            project: frm.doc.name
        });
    },

    custom_btn_purchase_receipt: function (frm) {
        if (frm.is_new()) {
            frappe.msgprint(__('Please save the Project before creating linked documents.'));
            return;
        }
        frappe.new_doc('Purchase Receipt', {
            project: frm.doc.name
        });
    },

    custom_btn_purchase_invoice: function (frm) {
        if (frm.is_new()) {
            frappe.msgprint(__('Please save the Project before creating linked documents.'));
            return;
        }
        frappe.new_doc('Purchase Invoice', {
            project: frm.doc.name
        });
    }
});
