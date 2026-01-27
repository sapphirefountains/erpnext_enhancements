import frappe
import json

def check_workspace():
    try:
        frappe.connect()
        home_exists = frappe.db.exists("Workspace", "Home")
        print(f"Workspace 'Home' exists: {home_exists}")

        # also list all workspaces to see what's available
        workspaces = frappe.get_all("Workspace", fields=["name", "title", "route", "is_standard"])
        print("Available Workspaces:")
        for w in workspaces:
            print(w)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        frappe.destroy()

if __name__ == "__main__":
    check_workspace()
