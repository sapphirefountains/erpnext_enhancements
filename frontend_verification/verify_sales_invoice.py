from playwright.sync_api import sync_playwright
import os

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Get absolute path to the HTML file
        cwd = os.getcwd()
        file_path = f"file://{cwd}/frontend_verification/mock_sales_invoice.html"

        print(f"Navigating to {file_path}")
        page.goto(file_path)

        # Check if the result div contains the success message
        try:
            page.wait_for_selector("#result", state="visible")
            result_text = page.inner_text("#result")
            print(f"Result Text: {result_text}")

            if "Success: render_comments_app called for custom_comments_field" in result_text:
                print("Verification PASSED")
            else:
                print("Verification FAILED")
                exit(1)

            # Take a screenshot
            page.screenshot(path="frontend_verification/sales_invoice_verification.png")
            print("Screenshot saved to frontend_verification/sales_invoice_verification.png")

        except Exception as e:
            print(f"Error: {e}")
            page.screenshot(path="frontend_verification/error.png")
            exit(1)

        browser.close()

if __name__ == "__main__":
    run()
