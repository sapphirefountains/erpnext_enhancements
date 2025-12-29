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

        page.goto(file_path)

        # 1. Verify Initial Load (Ready to Work)
        print("Verifying Initial State...")
        page.wait_for_selector("#tk-status-text")
        expect(page.locator("#tk-status-text")).to_have_text("Ready to Work")
        expect(page.locator("#tk-btn-clock-in")).to_be_visible()
        expect(page.locator("#tk-project-select")).to_be_visible()

        # Wait for projects to load (mock delay 100ms)
        page.wait_for_timeout(500)

        # Select Project
        print("Selecting Project...")
        page.select_option("#tk-project-select", "PROJ-001")

        # Enter Note
        page.fill("#tk-note-input", "Testing verify script")

        # Clock In
        print("Clocking In...")
        page.click("#tk-btn-clock-in")

        # Wait for "Clocked In" state
        # The mock API takes 500ms, then fetchStatus takes 100ms.
        page.wait_for_timeout(1000)

        print("Verifying Clocked In State...")
        expect(page.locator("#tk-status-text")).to_have_text("Clocked In")
        expect(page.locator("#tk-btn-clock-in")).not_to_be_visible()
        expect(page.locator("#tk-btn-clock-out")).to_be_visible()
        expect(page.locator("#tk-active-project-name")).to_have_text("Website Redesign")

        # Take Screenshot
        screenshot_path = f"{cwd}/frontend_verification/time_kiosk_verified.png"
        page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")

        browser.close()

if __name__ == "__main__":
    run()
