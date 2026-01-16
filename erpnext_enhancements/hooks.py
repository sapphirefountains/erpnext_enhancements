app_name = "erpnext_enhancements"
app_title = "ERPNext Enhancements"
app_publisher = "Sapphire Fountains"
app_description = "Customizations and enhancements to ERPNext."
app_email = "info@sapphirefountains.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "erpnext_enhancements",
# 		"logo": "/assets/erpnext_enhancements/logo.png",
# 		"title": "ERPNext Enhancements",
# 		"route": "/erpnext_enhancements",
# 		"has_permission": "erpnext_enhancements.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
app_include_css = "/assets/erpnext_enhancements/css/desk_enhancements.css"
app_include_js = [
	"/assets/erpnext_enhancements/js/erpnext_enhancements.js",
	"/assets/erpnext_enhancements/js/performance_fixes.js",
]

# include js, css files in header of web template
web_include_css = "/assets/erpnext_enhancements/css/login_enhancements.css"
# web_include_js = "/assets/erpnext_enhancements/js/erpnext_enhancements.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "erpnext_enhancements/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
    "Project": ["public/js/vue.global.js", "public/js/project_merge.js", "public/js/project_enhancements.js"],
    "Travel Trip": ["public/js/travel_trip.js"]
}
doctype_js = {
    "Purchase Order": "public/js/procurement_links.js",
    "Material Request": "public/js/procurement_links.js"
}
doctype_list_js = {"ToDo": "public/js/todo_list.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "erpnext_enhancements/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "erpnext_enhancements.utils.jinja_methods",
# 	"filters": "erpnext_enhancements.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "erpnext_enhancements.install.before_install"
# after_install = "erpnext_enhancements.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "erpnext_enhancements.uninstall.before_uninstall"
# after_uninstall = "erpnext_enhancements.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "erpnext_enhancements.utils.before_app_install"
# after_app_install = "erpnext_enhancements.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "erpnext_enhancements.utils.before_app_uninstall"
# after_app_uninstall = "erpnext_enhancements.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "erpnext_enhancements.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Task": {
		"on_update": [
			"erpnext_enhancements.calendar_sync.sync_doctype_to_event",
			"erpnext_enhancements.tasks.generate_next_task",
		],
		"on_trash": "erpnext_enhancements.calendar_sync.delete_event_from_google",
	},
	"Project": {
		"on_update": "erpnext_enhancements.calendar_sync.sync_doctype_to_event",
		"on_trash": "erpnext_enhancements.calendar_sync.delete_event_from_google",
		"after_save": "erpnext_enhancements.project_enhancements.sync_attachments_from_opportunity",
	},
	"Event": {
		"on_update": "erpnext_enhancements.calendar_sync.sync_doctype_to_event",
		"on_trash": "erpnext_enhancements.calendar_sync.delete_event_from_google",
	},
	"ToDo": {
		"on_update": "erpnext_enhancements.calendar_sync.sync_doctype_to_event",
		"on_trash": "erpnext_enhancements.calendar_sync.delete_event_from_google",
	},
    # Triton Integration Hook
    "*": {
        "on_update": "erpnext_enhancements.integrations.triton_bridge.hook_on_update",
        "on_trash": "erpnext_enhancements.integrations.triton_bridge.hook_on_trash"
    }
}

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {"daily": ["erpnext_enhancements.project_enhancements.send_project_start_reminders"]}

fixtures = [
    {
        "dt": "Custom Field",
        "filters": [
            ["name", "in", [
                "Expense Claim-custom_travel_trip",
                "Project-custom_calendar_datetime_start",
                "Project-custom_calendar_datetime_end",
                "Project-google_calendar_events",
                "Project-custom_comments_tab",
                "Project-custom_comments_field",
                "ToDo-custom_calendar_datetime_start",
                "ToDo-custom_calendar_datetime_end",
                "ToDo-google_calendar_events",
                "Task-google_calendar_events",
                "Event-google_calendar_events"
            ]]
        ]
    },
    {"dt": "Workflow", "filters": [["document_type", "=", "Travel Trip"]]},
    {"dt": "Workflow State", "filters": [["name", "in", ["Draft", "Requested", "Approved", "Booking in Progress", "Ready for Travel", "In Progress", "Expense Review", "Closed"]]]},
    {"dt": "Workflow Action", "filters": [["workflow", "=", "Travel Trip Workflow"]]}
]

# Testing
# -------

# before_tests = "erpnext_enhancements.install.before_tests"

# Overriding Methods
# ------------------------------
#
override_whitelisted_methods = {
	"erpnext.crm.doctype.opportunity.opportunity.make_project": "erpnext_enhancements.opportunity_enhancements.make_project"
}
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
override_doctype_dashboards = {
    "Project": "erpnext_enhancements.project_enhancements.get_dashboard_data",
    "Employee": "erpnext_enhancements.dashboard_overrides.get_data"
}

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["erpnext_enhancements.utils.before_request"]
# after_request = ["erpnext_enhancements.utils.after_request"]

# Job Events
# ----------
# before_job = ["erpnext_enhancements.utils.before_job"]
# after_job = ["erpnext_enhancements.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"erpnext_enhancements.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }
