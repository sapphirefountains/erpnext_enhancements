import os
from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # Absolute path to the html file
        file_path = os.path.abspath("frontend_verification/verify_material_request_comments.html")
        page.goto(f"file://{file_path}")

        # Check for success message
        try:
            # Wait for text to appear
            page.wait_for_selector("#comments-app-container", timeout=5000)
            # Give it a tiny bit of time for JS to run and update text
            page.wait_for_function("document.getElementById('comments-app-container').innerText.length > 0")

            content = page.text_content("#comments-app-container")
            if "Comments App Rendered in custom_comments_field" in content:
                print("Verification Passed: Comments App was rendered.")
            else:
                print(f"Verification Failed: Unexpected content '{content}'")
        except Exception as e:
            print(f"Verification Failed: {e}")

        browser.close()

if __name__ == "__main__":
    run()
