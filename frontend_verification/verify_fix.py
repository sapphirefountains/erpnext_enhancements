import os
from playwright.sync_api import sync_playwright, expect

def test_purchase_links(page):
    # Load the mock page
    cwd = os.getcwd()
    file_path = f"file://{cwd}/frontend_verification/mock_po.html"
    page.goto(file_path)

    # 1. Initial State: Empty grid
    expect(page.locator("#grid-rows")).to_be_empty()

    # 2. Add an Item
    # This should trigger the 'item_code' handler, which calls render_links,
    # which fetches data (mocked), updates row.purchase_links, and calls refresh_field.
    # refresh_field re-renders the grid with the links.
    page.click("#add-row")

    # Check that a row was added
    expect(page.locator(".grid-row")).to_have_count(1)

    # Check that links are rendered (handled by update_row_html)
    # The mock returns a link for 'ITEM-001' with 'Test Supplier'
    link_selector = "a.btn.btn-xs.btn-default"

    # Wait for the async chain to complete and DOM to update
    # In the mock, frappe.call has a 50ms delay, then render_links uses .then(), then refresh_field rerenders.
    # Playwright's expect will retry until timeout.
    expect(page.locator(link_selector)).to_contain_text("Test Supplier")

    # 3. Check for the edit button (since we have a link)
    expect(page.locator(".btn-edit-link")).to_be_visible()

    # 4. Take screenshot
    page.screenshot(path="frontend_verification/purchase_links_verified.png")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            test_purchase_links(page)
            print("Verification passed!")
        except Exception as e:
            print(f"Verification failed: {e}")
            page.screenshot(path="frontend_verification/failure.png")
            exit(1)
        finally:
            browser.close()
