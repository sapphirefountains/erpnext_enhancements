import frappe

def reload():
    try:
        print("Reloading ERPNext Enhancements Settings...")
        frappe.reload_doc("erpnext_enhancements", "doctype", "erpnext_enhancements_settings")
        print("Done.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    reload()
