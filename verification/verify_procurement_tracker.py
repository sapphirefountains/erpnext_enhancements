from playwright.sync_api import sync_playwright, expect

def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()

    # Load the mock HTML file
    # We use file:// protocol. Assuming cwd is root.
    import os
    file_path = f"file://{os.getcwd()}/verification/mock_index.html"
    print(f"Loading {file_path}")
    page.goto(file_path)

    # 1. Verify Default State (Collapsed)
    # Check that group content is NOT visible
    # We have Material Request and Purchase Order groups.
    print("Verifying default collapsed state...")

    # The group content has class 'group-content' and is rendered with v-if="!collapsedGroups[doctype]"
    # So if collapsed, these elements should NOT exist in the DOM.
    expect(page.locator(".group-content")).to_have_count(0)

    # 2. Expand a Group
    print("Expanding 'Material Request' group...")
    # Click the header for Material Request
    page.get_by_text("Material Request").click()

    # Now one group content should be visible
    expect(page.locator(".group-content")).to_have_count(1)

    # Verify Doc Chain rendering
    # Look for "MR:" label
    expect(page.locator("text=MR:").first).to_be_visible()

    # Verify Status Colors
    # "Ordered" should have 'status-submitted' (Blue) based on my logic?
    # Wait, my logic: "ordered" -> status-submitted. "Draft" -> status-draft.
    # Check class existence
    ordered_badge = page.locator(".status-badge", has_text="Ordered").first
    expect(ordered_badge).to_have_class(re.compile(r"status-submitted"))

    draft_badge = page.locator(".status-badge", has_text="Draft").first
    expect(draft_badge).to_have_class(re.compile(r"status-draft"))

    # 3. Fuzzy Search Test
    print("Testing Fuzzy Search...")
    search_input = page.locator("input[placeholder='Search all documents...']")
    search_input.fill("TEE PVC") # Should match the first item

    # Wait for Vue to react
    page.wait_for_timeout(500)

    # Material Request group should still be expanded (or auto-expanded)
    # The first row should be visible. The second row (STRUT CHANNEL) should be hidden?
    # No, filter is per group.
    # The first item matches "TEE PVC". The second item "STRUT CHANNEL" does NOT match.
    # So only 1 row should be in the table body (plus header).

    # Count rows in the first table
    # tbody tr
    visible_rows = page.locator(".group-content table tbody tr")
    # We expect 1 row + potentially "No matching records" if empty? No, filteredGroups returns array.
    # If filteredGroups has items, we iterate tr v-for.
    expect(visible_rows).to_have_count(1)

    # Verify Highlight
    # Check for <mark>TEE</mark> and <mark>PVC</mark> inside the HTML
    # We can check innerHTML of the cell
    first_cell = visible_rows.first.locator("td").first
    html_content = first_cell.inner_html()
    if "<mark>TEE</mark>" in html_content and "<mark>PVC</mark>" in html_content:
        print("Highlight verified.")
    else:
        print(f"Highlight check failed. Content: {html_content}")

    # Take Screenshot
    print("Taking screenshot...")
    page.screenshot(path="verification/procurement_tracker_verified.png")

    browser.close()

import re
with sync_playwright() as playwright:
    run(playwright)
