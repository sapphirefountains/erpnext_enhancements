import frappe

def reload():
    try:
        print("Reloading Auto Save User Config...")
        frappe.reload_doc("erpnext_enhancements", "doctype", "auto_save_user_config")
        print("Reloading ERPNext Enhancements Settings...")
        frappe.reload_doc("erpnext_enhancements", "doctype", "erpnext_enhancements_settings")
        print("Done.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    reload()
