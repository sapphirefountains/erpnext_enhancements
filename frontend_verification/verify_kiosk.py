import os
from playwright.sync_api import sync_playwright, expect

def verify_kiosk():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1. Read the mock HTML
        with open('frontend_verification/mock_index.html', 'r') as f:
            html_content = f.read()

        # 2. Read the Time Kiosk JS
        # We need to adjust the path to where the file actually is in the repo
        js_path = 'erpnext_enhancements/erpnext_enhancements/page/time_kiosk/time_kiosk.js'
        with open(js_path, 'r') as f:
            js_content = f.read()

        # 3. Inject the JS into the HTML
        # We replace the placeholder comment
        final_html = html_content.replace(
            '// Placeholder for script injection by verification script',
            js_content + '\n\n' + 'init_time_kiosk($("#wrapper"));'
        )

        # 4. Save to a temporary file to load via file://
        temp_html_path = os.path.abspath('frontend_verification/temp_kiosk.html')
        with open(temp_html_path, 'w') as f:
            f.write(final_html)

        # 5. Load the page
        print(f"Loading {temp_html_path}")
        page.goto(f"file://{temp_html_path}")

        # 6. Verify Loading
        # The script should try to load Vue.
        # Since we are in a file:// environment, the request to '/assets/...' for local vue will fail (File not found).
        # Our robust loader should catch this error and try CDN.
        # So we expect the app to eventually load.

        # Wait for the app title (Vue rendered)
        try:
            # We expect "Ready to Work" text which appears when status is Idle
            expect(page.locator("text=Ready to Work")).to_be_visible(timeout=10000)
            print("Vue App Loaded Successfully via Fallback!")
        except Exception as e:
            print("Verification Failed: App did not load.")
            page.screenshot(path='frontend_verification/failed_state.png')
            raise e

        # 7. Take Screenshot
        page.screenshot(path='frontend_verification/verification.png')
        print("Screenshot saved to frontend_verification/verification.png")

        browser.close()

if __name__ == "__main__":
    verify_kiosk()
