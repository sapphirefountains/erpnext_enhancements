from playwright.sync_api import sync_playwright, expect
import os

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Load the mock HTML file
        # We need absolute path
        cwd = os.getcwd()
        file_path = f"file://{cwd}/frontend_verification/mock_time_kiosk.html"
        print(f"Loading: {file_path}")

        # Capture console logs to verify manual CSS loading
        page.on("console", lambda msg: print(f"PAGE LOG: {msg.text}"))

        page.goto(file_path)

        # 1. Verify Initial Load (Ready to Work)
        print("Verifying Initial State...")
        page.wait_for_selector("#tk-status-text")
        expect(page.locator("#tk-status-text")).to_have_text("Ready to Work")

        # Verify CSS Link Injection
        # We check if a link with id 'time-kiosk-css' exists in the head
        print("Verifying CSS Injection...")
        css_link = page.locator("head link#time-kiosk-css")
        expect(css_link).to_have_count(1)
        expect(css_link).to_have_attribute("href", "/assets/erpnext_enhancements/css/time-kiosk.css")

        # Take Screenshot of the fix
        screenshot_path = f"{cwd}/frontend_verification/time_kiosk_css_fix.png"
        page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")

        browser.close()

if __name__ == "__main__":
    run()
