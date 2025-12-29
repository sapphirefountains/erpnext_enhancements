from playwright.sync_api import sync_playwright, expect
import os

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        cwd = os.getcwd()
        file_path = f"file://{cwd}/frontend_verification/mock_time_kiosk.html"
        print(f"Loading: {file_path}")

        page.goto(file_path)

        # 1. Verify Initial Load (Ready to Work)
        # It should be "Ready to Work" despite receiving {employee: ...}
        print("Verifying Initial State...")
        page.wait_for_selector("#tk-status-text")
        expect(page.locator("#tk-status-text")).to_have_text("Ready to Work")
        expect(page.locator("#tk-btn-clock-in")).to_be_visible()

        # Verify Link field mock exists
        page.wait_for_selector("input[data-fieldname='project']")

        # Select Project (simulate filling the link field)
        print("Selecting Project...")
        page.fill("input[data-fieldname='project']", "PROJ-001")

        # Enter Note
        page.fill("#tk-note-input", "Testing verify script")

        # Clock In
        print("Clocking In...")
        page.click("#tk-btn-clock-in")

        # Wait for "Clocked In" state
        page.wait_for_timeout(1000)

        print("Verifying Clocked In State...")
        expect(page.locator("#tk-status-text")).to_have_text("Clocked In")
        expect(page.locator("#tk-btn-clock-in")).not_to_be_visible()
        expect(page.locator("#tk-btn-clock-out")).to_be_visible()

        # Take Screenshot
        screenshot_path = f"{cwd}/frontend_verification/time_kiosk_verified.png"
        page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")

        browser.close()

if __name__ == "__main__":
    run()
