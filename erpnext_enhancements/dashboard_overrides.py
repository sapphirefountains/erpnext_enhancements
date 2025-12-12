from frappe import _

def get_data(data):
    data["transactions"].append(
        {
            "label": _("Travel"),
            "items": ["Travel Trip"],
        }
    )
    return data
