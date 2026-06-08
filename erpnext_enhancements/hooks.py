app_name = "erpnext_enhancements"
app_title = "ERPNext Enhancements"
app_publisher = "Sapphire Fountains"
app_description = "Customizations and enhancements to ERPNext."
app_email = "info@sapphirefountains.com"
app_license = "mit"

# include js, css files in header of desk.html
app_include_css = [
    "desk_enhancements.bundle.css",
    # global_enhancements
    "/assets/erpnext_enhancements/css/global_enhancements/triton_widget.css",
    # project_enhancements
    "/assets/erpnext_enhancements/css/project_enhancements/task_tree.css",
    "/assets/erpnext_enhancements/css/project_enhancements/frappe-gantt.css",
    # task_enhancements
    "/assets/erpnext_enhancements/css/task_enhancements/task_enhancements.css",
    # quickbooks_time_integration
    "/assets/erpnext_enhancements/css/quickbooks_time_integration/qb_time_integration.css",
]
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
    # global_enhancements
    "/assets/erpnext_enhancements/js/global_enhancements/quill_mentions.js",
    "/assets/erpnext_enhancements/js/global_enhancements/global_sidebar.js",
    "/assets/erpnext_enhancements/js/global_enhancements/unlink_and_delete.js",
    "/assets/erpnext_enhancements/js/global_enhancements/triton_widget.js",
    # project_enhancements
    "/assets/erpnext_enhancements/js/project_enhancements/lib/frappe-gantt.umd.js",
    "/assets/erpnext_enhancements/js/project_enhancements/task_tree_manager.js",
    "/assets/erpnext_enhancements/js/project_enhancements/dashboard_components/column_selector.js",
    "/assets/erpnext_enhancements/js/project_enhancements/gantt_zoom.js",
]

# include js, css files in header of web template
web_include_css = "/assets/erpnext_enhancements/css/login_enhancements.css"

doctype_js = {
    "Opportunity": [
        "public/js/opportunity.js",
        "public/js/crm_enhancements/opportunity.js",
        "public/js/global_enhancements/unified_tab_controller.js",
        "public/js/global_enhancements/disable_kanban_drag.js",
        "project_enhancements/doctype/opportunity/opportunity.js",
        "public/js/crm_enhancements/opportunity_migrated_scripts.js",
    ],
    "Communication": ["public/js/communication.js"],
    "Project": [
        "public/js/vue.global.js",
        "public/js/comments.js",
        "public/js/project_merge.js",
        "public/js/project_enhancements.js",
        "public/js/project.js",
        "public/js/global_enhancements/unified_tab_controller.js",
        "project_enhancements/doctype/project/project.js",
        "public/js/project_enhancements/project_form_script.js",
        "public/js/project_enhancements/project_brief.js",
        "public/js/project_migrated_scripts.js",
    ],
    "Master Project": ["public/js/global_enhancements/unified_tab_controller.js"],
    "Item": ["public/js/vue.global.js", "public/js/comments.js", "public/js/item_comments.js", "public/js/item.js"],
    "Process Document": ["public/js/process_document.js"],
    "Employee": ["public/js/vue.global.js", "public/js/comments.js", "public/js/employee.js"],
    "Account": ["public/js/vue.global.js", "public/js/comments.js", "public/js/account.js"],
    "Customer": [
        "public/js/vue.global.js",
        "public/js/comments.js",
        "public/js/customer.js",
        "public/js/global_enhancements/unified_tab_controller.js",
    ],
    "Timesheet": ["public/js/vue.global.js", "public/js/comments.js", "public/js/timesheet.js"],
    "Sales Order": [
        "public/js/vue.global.js",
        "public/js/comments.js",
        "public/js/sales_order_comments.js",
        "public/js/sales_order_enhancements.js"
    ],
    "Sales Invoice": ["public/js/vue.global.js", "public/js/comments.js", "public/js/sales_invoice_comments.js"],
    "Task": [
        "public/js/vue.global.js",
        "public/js/comments.js",
        "public/js/task_comments.js",
        "public/js/task_enhancements.js",
        "task_enhancements/doctype/task/task.js",
    ],
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
    "Supplier": [
        "public/js/vue.global.js",
        "public/js/comments.js",
        "public/js/supplier_comments.js",
        "public/js/global_enhancements/unified_tab_controller.js",
    ],
    "Supplier Quotation": ["public/js/vue.global.js", "public/js/comments.js", "public/js/supplier_quotation_comments.js"],
    "Quotation": ["public/js/vue.global.js", "public/js/comments.js", "public/js/quotation_comments.js"],
    "Lead": [
        "public/js/vue.global.js",
        "public/js/comments.js",
        "public/js/lead_comments.js",
        "public/js/lead.js",
        "public/js/global_enhancements/primary_contact.js",
    ],
    "Contact": [
        "public/js/vue.global.js",
        "public/js/comments.js",
        "public/js/contact_comments.js",
        "public/js/contact.js",
        "public/js/global_enhancements/unified_tab_controller.js",
    ],
    "Address": [
        "public/js/vue.global.js",
        "public/js/comments.js",
        "public/js/address_comments.js",
        "project_enhancements/doctype/address/address.js",
    ],
    "Prospect": ["public/js/vue.global.js", "public/js/comments.js", "public/js/prospect_comments.js"],
    # quickbooks_time_integration
    "QuickBooks Online Settings": "quickbooks_time_integration/doctype/quickbooks_online_settings/quickbooks_online_settings.js",
}

doctype_list_js = {
    "Opportunity": [
        "public/js/opportunity_list.js",
        "public/js/crm_enhancements/opportunity_list.js",
        "public/js/crm_enhancements/opportunity_kanban_totals.js",
    ],
    "Supplier": "public/js/global_enhancements/supplier_list.js",
    "Task": "public/js/project_enhancements/task_gantt.js",
    "File": "public/js/global_enhancements/file_list.js",
    "Item": "public/js/item_list.js",
}
doctype_calendar_js = {
    "Asset Booking": "public/js/asset_booking_calendar.js"
}
doctype_css = {
    "Opportunity": "public/css/global_enhancements/horizontal_scroll.css",
}

# Custom fields created/synced automatically on migrate (from crm_enhancements)
custom_fields = {
    "Project": [
        {
            "fieldname": "custom_drive_folder_id",
            "label": "Drive Folder ID",
            "fieldtype": "Data",
            "hidden": 1,
            "insert_after": "project_name",
        }
    ]
}

# Override standard doctype classes (from task_enhancements)
override_doctype_class = {
    "Task": "erpnext_enhancements.task_enhancements.doctype.task.task.Task",
}

doc_events = {
    "Task": {
        "before_save": "erpnext_enhancements.script_migrations.task.calculate_project_elapsed_time",
        "after_insert": "erpnext_enhancements.script_migrations.task.sync_task_to_google_calendar",
        "on_update": [
            "erpnext_enhancements.tasks.generate_next_task",
            "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.publish_realtime_update",
        ],
    },
    "Project": {
        "before_save": "erpnext_enhancements.script_migrations.project.remove_open_status",
        "after_save": "erpnext_enhancements.project_enhancements.sync_attachments_from_opportunity",
        "on_update": [
            "erpnext_enhancements.sync_contact.sync_from_main_doc",
            "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.publish_realtime_update",
        ],
        "on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
    },
    "Master Project": {
        "on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
    },
    "Address": {
        "before_save": "erpnext_enhancements.script_migrations.address.set_full_address",
        "on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
    },
    "Communication": {
        "after_insert": "erpnext_enhancements.api.communication.after_insert_communication",
    },
    "Sapphire Maintenance Record": {
        "on_submit": "erpnext_enhancements.api.maintenance_scheduling.update_sales_order_next_visit",
    },
    "Opportunity": {
        "before_save": [
            "erpnext_enhancements.crm_enhancements.api.sync_opportunity_tags",
            "erpnext_enhancements.script_migrations.opportunity.stamp_won_date",
            "erpnext_enhancements.script_migrations.opportunity.validate_ranks_on_won",
            "erpnext_enhancements.script_migrations.opportunity.update_lead_status",
        ],
        "on_update": "erpnext_enhancements.sync_contact.sync_from_main_doc",
        "on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
    },
    "Contact": {
        "on_update": "erpnext_enhancements.sync_contact.sync_from_contact",
        "on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
    },
    "Supplier": {
        "on_update": "erpnext_enhancements.sync_contact.sync_from_main_doc",
        "validate": "erpnext_enhancements.supplier_query.sync_supplier_groups",
        "on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
    },
    "Customer": {
        "before_save": "erpnext_enhancements.script_migrations.customer.set_last_activity",
        "on_update": "erpnext_enhancements.sync_contact.sync_from_main_doc",
        "on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
    },
    "*": {
        "after_save": "erpnext_enhancements.utils.triton_sync.global_triton_sync",
    },
}

scheduler_events = {
    "daily": [
        "erpnext_enhancements.project_enhancements.send_project_start_reminders",
        "erpnext_enhancements.tasks.predictive_maintenance_scheduling",
        "erpnext_enhancements.script_migrations.customer.customer_inactivity_reminder",
        "erpnext_enhancements.script_migrations.project.update_elapsed_time_daily",
    ],
    "hourly": [
        "erpnext_enhancements.quickbooks_time_integration.quickbooks_online.tasks.refresh_token_if_needed",
        "erpnext_enhancements.quickbooks_time_integration.quickbooks_online.tasks.cdc_poll",
        "erpnext_enhancements.quickbooks_time_integration.quickbooks_online.tasks.retry_failed_syncs",
    ],
}

# Run after each `bench migrate` (from global_enhancements)
after_migrate = [
    "erpnext_enhancements.setup.custom_fields.create_primary_contact_fields",
    "erpnext_enhancements.setup.supplier_groups.create_supplier_group_customizations",
]

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
                "Serial No-custom_pump_make_model",
                "Serial No-custom_filtration_media_type",
                "Serial No-custom_water_volume",
                "Serial No-custom_site_instructions",
                "Serial No-custom_project",
                "Sales Order Item-custom_serial_no",
                "Sales Order Item-custom_maintenance_frequency",
                "Sales Order Item-custom_last_visit_date",
                "Sales Order Item-custom_next_predictive_visit",
                "Sales Order-custom_display_labor_hours",
                "Sales Invoice-custom_maintenance_record"
            ]]
        ]
    },
    # project_enhancements: all Custom Fields on the Project doctype
    {"dt": "Custom Field", "filters": [["dt", "=", "Project"]]},
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

portal_menu_items = [
    {"title": "Maintenance Records", "route": "/maintenance-records", "role": "Customer"}
]
