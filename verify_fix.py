import os

def verify_file_content():
    file_path = 'erpnext_enhancements/erpnext_enhancements/page/time_kiosk/time_kiosk.js'

    if not os.path.exists(file_path):
        print(f"FAILED: File {file_path} not found.")
        return

    with open(file_path, 'r') as f:
        content = f.read()

    checks = [
        ("vue.global.js", "Dependency loading (vue.global.js)"),
        ("createApp", "Vue initialization (createApp)"),
        ("erpnext_enhancements.erpnext_enhancements.api.time_kiosk.log_time", "API Call: log_time"),
        ("erpnext_enhancements.erpnext_enhancements.api.time_kiosk.get_projects", "API Call: get_projects"),
        ("navigator.geolocation", "Geolocation Logic"),
        ("#time-kiosk-app", "Mount point ID")
    ]

    print(f"Verifying {file_path}...\n")
    all_passed = True
    for key, desc in checks:
        if key in content:
            print(f"[PASS] {desc}")
        else:
            print(f"[FAIL] {desc} - Key '{key}' not found.")
            all_passed = False

    if all_passed:
        print("\nSUCCESS: All architectural requirements met.")
    else:
        print("\nFAILURE: Some requirements are missing.")
        exit(1)

if __name__ == "__main__":
    verify_file_content()
