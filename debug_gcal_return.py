import frappe
from frappe.integrations.doctype.google_calendar.google_calendar import get_google_calendar_object

def debug_gcal_return():
    # Fetch a Google Calendar doc to test with.
    # We can try to find one enabled.
    doc_name = frappe.db.get_value("Google Calendar", {"enable": 1}, "name")
    if not doc_name:
        print("No enabled Google Calendar found to test.")
        return

    print(f"Testing with Google Calendar: {doc_name}")
    doc = frappe.get_doc("Google Calendar", doc_name)
    
    # Call the function
    ret = get_google_calendar_object(doc)
    
    print(f"Return type: {type(ret)}")
    print(f"Return value: {ret}")
    
    if isinstance(ret, tuple):
        print(f"Tuple length: {len(ret)}")
        for i, item in enumerate(ret):
            print(f"Item {i} type: {type(item)}")
            print(f"Item {i} str: {str(item)}")
            
    # Also check the doc's calendar_id field
    print(f"Doc google_calendar_id field: {doc.google_calendar_id}")

if __name__ == "__main__":
    frappe.connect()
    debug_gcal_return()
