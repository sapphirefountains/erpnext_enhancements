# Server Scripts migrated into the app for version control.
#
# Each module here corresponds to one or more Frappe "Server Script" records
# that previously lived only in the site database. They are wired up via
# doc_events / scheduler_events in hooks.py so that the behavior ships with the
# app instead of being a manual, un-versioned customization.
