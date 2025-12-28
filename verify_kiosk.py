from playwright.sync_api import sync_playwright, expect
import os

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Load the mock HTML file
        # We assume the script is run from the repo root
        file_path = os.path.abspath("frontend_verification/mock_index.html")
        page.goto(f"file://{file_path}")

        # Wait for the script to load and Vue to mount
        # We look for the "Ready to Work" text or "Clocked In"
        # The default mock status is Idle -> "Ready to Work"

        try:
            # Wait for the loading state to disappear or the main text to appear
            page.wait_for_selector("text=Ready to Work", timeout=5000)
            print("Successfully verified 'Ready to Work' text.")

            # Check for the Project Dropdown
            page.wait_for_selector("select.form-control", timeout=2000)
            print("Successfully verified Project Dropdown.")

            # Take screenshot
            os.makedirs("frontend_verification", exist_ok=True)
            screenshot_path = os.path.abspath("frontend_verification/kiosk_verified.png")
            page.screenshot(path=screenshot_path)
            print(f"Screenshot saved to {screenshot_path}")

        except Exception as e:
            print(f"Verification Failed: {e}")
            page.screenshot(path="frontend_verification/kiosk_failed.png")
            exit(1)
        finally:
            browser.close()

if __name__ == "__main__":
    run()
