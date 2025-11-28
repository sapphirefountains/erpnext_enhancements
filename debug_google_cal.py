import frappe
from frappe.integrations.doctype.google_calendar.google_calendar import get_google_calendar_object

def check_return_type():
    print("Checking return type of get_google_calendar_object...")
    # We need a valid Google Calendar doc to test this, which might be hard to get if we don't have one.
    # But maybe we can inspect the code object if we can't run it?
    # Or just print the function source if possible?
    import inspect
    try:
        print(inspect.getsource(get_google_calendar_object))
    except Exception as e:
        print(f"Could not get source: {e}")

if __name__ == "__main__":
    frappe.connect()
    check_return_type()
