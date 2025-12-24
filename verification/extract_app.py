import re
import os

# Read the source file
with open("erpnext_enhancements/public/js/project_enhancements.js", "r") as f:
    content = f.read()

# Extract the config object passed to Vue.createApp
# Pattern: Vue.createApp({ ... })
# We need to capture everything between the first { and the last } related to that call.
# Since regex for nested brackets is hard, we can find the start index and count brackets.

start_marker = "const app = Vue.createApp({"
start_idx = content.find(start_marker)

if start_idx == -1:
    print("Could not find Vue.createApp")
    exit(1)

# Move past marker
start_content = start_idx + len(start_marker) - 1 # -1 to include the opening brace
bracket_count = 0
end_idx = -1

for i in range(start_content, len(content)):
    char = content[i]
    if char == '{':
        bracket_count += 1
    elif char == '}':
        bracket_count -= 1
        if bracket_count == 0:
            end_idx = i + 1
            break

if end_idx == -1:
    print("Could not find matching closing brace")
    exit(1)

app_config = content[start_content:end_idx]

# Replace `r.message` with `mockData`
app_config = app_config.replace("r.message", "mockData")
# Replace `frm.doc.__islocal` or other frappe specific calls if any in the data/methods?
# The `template` handles display. `methods` call `frappe.set_route`. We can mock that.

html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mock Procurement Tracker</title>
    <script src="vue.global.js"></script>
    <style>
        body {{ font-family: sans-serif; background: #1a1a1a; color: #ddd; padding: 20px; }}
        /* Simulate Frappe globals */
    </style>
</head>
<body>
    <div id="wrapper">
        <div id="procurement-tracker-app"></div>
    </div>

    <script>
        // Mock Frappe
        window.frappe = {{
            set_route: function() {{ console.log("frappe.set_route called", arguments); }},
            msgprint: function(msg) {{ alert(msg); }}
        }};

        // Mock Data
        const mockData = {{
            "Material Request": [
                {{
                    "item_code": "801-060",
                    "item_name": "TEE, STRAIGHT, SOC, PVC, 6\\" SCH80",
                    "mr": "MAT-MR-2025-00006-2",
                    "mr_status": "Ordered",
                    "rfq": "RFQ-2025-0001",
                    "rfq_status": "Submitted",
                    "po": "PO-2025-00018",
                    "po_status": "Draft",
                    "warehouse": null,
                    "ordered_qty": 10,
                    "received_qty": 0,
                    "completion_percentage": 0
                }},
                {{
                     "item_code": "P1000T",
                     "item_name": "STRUT CHANNEL, SLOTTED",
                     "mr": "MAT-MR-2025-00006-2",
                     "mr_status": "Ordered",
                     "po": "PO-2025-00017",
                     "po_status": "To Receive and Bill",
                     "ordered_qty": 80,
                     "received_qty": 0,
                     "completion_percentage": 0
                }}
            ],
            "Purchase Order": [
                 {{
                     "item_code": "DIRECT-ITEM",
                     "item_name": "Direct Purchase Item",
                     "po": "PO-2025-00099",
                     "po_status": "Closed",
                     "ordered_qty": 5,
                     "received_qty": 5,
                     "completion_percentage": 100
                 }}
            ]
        }};

        // App Config
        const appConfig = {app_config};

        const app = Vue.createApp(appConfig);
        app.mount('#procurement-tracker-app');
    </script>
</body>
</html>
"""

with open("verification/mock_index.html", "w") as f:
    f.write(html_template)

print("Created verification/mock_index.html")
