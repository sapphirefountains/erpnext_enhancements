app_name = "erpnext_enhancements"
app_title = "ERPNext Enhancements"
app_publisher = "Sapphire Fountains"
app_description = "Customizations and enhancements to ERPNext."
app_email = "info@sapphirefountains.com"
app_license = "mit"

# include js, css files in header of desk.html
app_include_css = "/assets/erpnext_enhancements/css/desk_enhancements.css"
app_include_js = [
	"/assets/erpnext_enhancements/js/erpnext_enhancements.js",
    "/assets/erpnext_enhancements/js/kanban_patches.js",
    "/assets/erpnext_enhancements/js/kanban_customization.js",
	"/assets/erpnext_enhancements/js/global_comments.js",
	"/assets/erpnext_enhancements/js/crm_note_enhancements.js",
	"/assets/erpnext_enhancements/js/performance_fixes.js",
    "/assets/erpnext_enhancements/js/activity_log_numbering.js",
    "/assets/erpnext_enhancements/js/filter_help.js",
    "/assets/erpnext_enhancements/js/telephony_client.js",
]

# include js, css files in header of web template
web_include_css = "/assets/erpnext_enhancements/css/login_enhancements.css"

doctype_js = {
    "Opportunity": ["public/js/opportunity.js"],
    "Communication": ["public/js/communication.js"],
    "Project": ["public/js/vue.global.js", "public/js/comments.js", "public/js/project_merge.js", "public/js/project_enhancements.js", "public/js/project.js"],
    "Item": ["public/js/vue.global.js", "public/js/comments.js", "public/js/item_comments.js"],
    "Employee": ["public/js/vue.global.js", "public/js/comments.js", "public/js/employee.js"],
    "Account": ["public/js/vue.global.js", "public/js/comments.js", "public/js/account.js"],
    "Customer": ["public/js/vue.global.js", "public/js/comments.js", "public/js/customer.js"],
    "Timesheet": ["public/js/vue.global.js", "public/js/comments.js", "public/js/timesheet.js"],
    "Sales Order": [
        "public/js/vue.global.js",
        "public/js/comments.js",
        "public/js/sales_order_comments.js",
        "public/js/sales_order_enhancements.js"
    ],
    "Sales Invoice": ["public/js/vue.global.js", "public/js/comments.js", "public/js/sales_invoice_comments.js"],
    "Task": ["public/js/vue.global.js", "public/js/comments.js", "public/js/task_comments.js", "public/js/task_enhancements.js"],
    "Journal Entry": ["public/js/vue.global.js", "public/js/comments.js", "public/js/journal_entry_comments.js"],
    "Payment Entry": ["public/js/vue.global.js", "public/js/comments.js", "public/js/payment_entry_comments.js"],
    "Purchase Invoice": ["public/js/vue.global.js", "public/js/comments.js", "public/js/purchase_invoice_comments.js"],
    "Production Plan": ["public/js/vue.global.js", "public/js/comments.js", "public/js/production_plan_comments.js"],
    "Work Order": ["public/js/vue.global.js", "public/js/comments.js", "public/js/work_order_comments.js"],
    "Job Card": ["public/js/vue.global.js", "public/js/comments.js", "public/js/job_card_comments.js"],
    "Stock Entry": ["public/js/vue.global.js", "public/js/comments.js", "public/js/stock_entry_comments.js"],
    "Travel Trip": ["public/js/travel_trip.js"],
    "Purchase Order": ["public/js/vue.global.js", "public/js/comments.js", "public/js/procurement_links.js", "public/js/purchase_order_comments.js"],
    "Material Request": ["public/js/vue.global.js", "public/js/comments.js", "public/js/procurement_links.js", "public/js/material_request_comments.js"],
    "Purchase Receipt": ["public/js/vue.global.js", "public/js/comments.js", "public/js/purchase_receipt_comments.js"],
    "Delivery Note": ["public/js/vue.global.js", "public/js/comments.js", "public/js/delivery_note_comments.js"],
    "Serial No": ["public/js/vue.global.js", "public/js/comments.js", "public/js/serial_no_comments.js"],
    "Batch": ["public/js/vue.global.js", "public/js/comments.js", "public/js/batch_comments.js"],
    "Supplier": ["public/js/vue.global.js", "public/js/comments.js", "public/js/supplier_comments.js"],
    "Supplier Quotation": ["public/js/vue.global.js", "public/js/comments.js", "public/js/supplier_quotation_comments.js"],
    "Quotation": ["public/js/vue.global.js", "public/js/comments.js", "public/js/quotation_comments.js"],
    "Lead": ["public/js/vue.global.js", "public/js/comments.js", "public/js/lead_comments.js", "public/js/lead.js"],
    "Contact": ["public/js/vue.global.js", "public/js/comments.js", "public/js/contact_comments.js", "public/js/contact.js"],
    "Address": ["public/js/vue.global.js", "public/js/comments.js", "public/js/address_comments.js"],
    "Prospect": ["public/js/vue.global.js", "public/js/comments.js", "public/js/prospect_comments.js"]
}

doctype_list_js = {
    "Opportunity": "public/js/opportunity_list.js"
}
doctype_calendar_js = {
    "Asset Booking": "public/js/asset_booking_calendar.js"
}

doc_events = {
	"Task": {
		"on_update": [
			"erpnext_enhancements.tasks.generate_next_task",
		],
	},
	"Project": {
		"after_save": "erpnext_enhancements.project_enhancements.sync_attachments_from_opportunity",
	},
	"Communication": {
		"after_insert": "erpnext_enhancements.api.communication.after_insert_communication",
	},
	"Sapphire Maintenance Record": {
		"on_submit": "erpnext_enhancements.api.maintenance_scheduling.update_sales_order_next_visit"
	}
}

scheduler_events = {
    "daily": [
        "erpnext_enhancements.project_enhancements.send_project_start_reminders",
        "erpnext_enhancements.tasks.predictive_maintenance_scheduling"
    ]
}

fixtures = [
    {
        "dt": "Custom Field",
        "filters": [
            ["name", "in", [
                "Expense Claim-custom_travel_trip",
                "Project-custom_project_id",
                "Project-custom_comments_tab",
                "Project-custom_comments_field",
                "Task-custom_comments_tab",
                "Task-custom_comments_field",
                "Material Request-custom_project",
                "Request for Quotation-custom_project",
                "Customer-custom_comments_tab",
                "Customer-custom_comments_field",
                "Kanban Board-custom_swimlane_field",
                "Kanban Board Column-custom_wip_limit",
                "Employee-custom_comments_tab",
                "Employee-custom_comments_field",
                "Stock Entry-custom_comments_tab",
                "Stock Entry-custom_comments_field",
                "Delivery Note-custom_comments_tab",
                "Delivery Note-custom_comments_field",
                "Serial No-custom_comments_tab",
                "Serial No-custom_comments_field",
                "Batch-custom_comments_tab",
                "Batch-custom_comments_field",
                "Supplier-custom_comments_tab",
                "Supplier-custom_comments_field",
                "Supplier Quotation-custom_comments_tab",
                "Supplier Quotation-custom_comments_field",
                "Quotation-custom_comments_tab",
                "Quotation-custom_comments_field",
                "Purchase Order-custom_comments_tab",
                "Purchase Order-custom_comments_field",
                "Lead-custom_comments_tab",
                "Lead-custom_comments_field",
                "Contact-custom_comments_tab",
                "Contact-custom_comments_field",
                "Address-custom_comments_tab",
                "Address-custom_comments_field",
                "Task-custom_create_child_task_btn",
                "Asset-custom_current_event_location",
                "Asset-custom_map_placeholder",
                "Asset-custom_rental_status",
                "Asset-custom_pump_make_model",
                "Asset-custom_filtration_media_type",
                "Asset-custom_water_volume",
                "Asset-custom_site_instructions",
                "Sales Order Item-custom_asset",
                "Sales Order Item-custom_maintenance_frequency",
                "Sales Order Item-custom_last_visit_date",
                "Sales Order Item-custom_next_predictive_visit",
                "Sales Order-custom_display_labor_hours",
                "Sales Invoice-custom_maintenance_record"
            ]]
        ]
    },
    {"dt": "Workflow", "filters": [["document_type", "=", "Travel Trip"]]},
    {"dt": "Workflow State", "filters": [["name", "in", ["Draft", "Requested", "Approved", "Booking in Progress", "Ready for Travel", "In Progress", "Expense Review", "Closed", "Pending Review", "Final/Submitted"]]]},
    {"dt": "Workflow Action", "filters": [["workflow", "=", "Travel Trip Workflow"]]},
    {"dt": "Workflow Action Master", "filters": [["name", "in", ["Request Review", "Approve & Submit"]]]},
    {"dt": "Notification", "filters": [["name", "in", ["Maintenance Review Needed", "Maintenance Finalized"]]]},
    {"dt": "Print Format", "filters": [["name", "=", "Maintenance Record Print"]]}
]

override_whitelisted_methods = {
	"erpnext.crm.doctype.opportunity.opportunity.make_project": "erpnext_enhancements.opportunity_enhancements.make_project"
}

override_doctype_dashboards = {
    "Project": "erpnext_enhancements.project_enhancements.get_dashboard_data",
    "Employee": "erpnext_enhancements.dashboard_overrides.get_data"
}

ignore_links_on_delete = ["User Form Draft"]
