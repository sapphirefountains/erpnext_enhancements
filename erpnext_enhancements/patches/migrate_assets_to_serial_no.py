import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def execute():
    """
    Data Migration: SF Water Feature Assets -> Serial No.
    This patch executes Phase 4 of the architectural overhaul.
    """
    
    # 1. Ensure the Generic Item exists for Water Features
    item_code = "Customer Water Feature"
    if not frappe.db.exists("Item", item_code):
        item = frappe.new_doc("Item")
        item.item_code = item_code
        item.item_name = "Customer Water Feature"
        item.item_group = "Products" # Or a more appropriate group
        item.is_stock_item = 0 # No stock needed for virtual tracking
        item.is_purchase_item = 0
        item.is_sales_item = 1
        item.uom = "Nos"
        item.insert(ignore_permissions=True)
        frappe.logger().info(f"Created generic item: {item_code}")

    # 2. Get all Water Feature Assets
    assets = frappe.get_all(
        "Asset",
        filters={"asset_category": "SF Water Feature"},
        fields=[
            "name", "asset_name", "customer", "custom_site_instructions",
            "custom_pump_make_model", "custom_filtration_media_type",
            "custom_water_volume", "custom_under_warranty",
            "custom_warranty_expiry_date"
        ]
    )

    if not assets:
        frappe.logger().info("No SF Water Feature assets found to migrate.")
        return

    asset_to_serial_map = {}

    for asset in assets:
        # 3. Create Serial No
        # We'll use the Asset Name as the Serial No ID if possible, or append a prefix
        serial_id = asset.name # Asset IDs are unique
        
        if not frappe.db.exists("Serial No", serial_id):
            sn = frappe.new_doc("Serial No")
            sn.item_code = item_code
            sn.serial_no = serial_id
            sn.customer = asset.customer
            sn.warranty_expiry_date = asset.custom_warranty_expiry_date
            
            # Map custom fields
            sn.custom_site_instructions = asset.custom_site_instructions
            sn.custom_pump_make_model = asset.custom_pump_make_model
            sn.custom_filtration_media_type = asset.custom_filtration_media_type
            sn.custom_water_volume = asset.custom_water_volume
            
            # Projects are usually linked via Asset Booking or Sales Order, 
            # but we'll try to find a relevant project if it exists.
            # (In this system, Sapphire Maintenance Records link Asset and Project)
            sn.insert(ignore_permissions=True)
            asset_to_serial_map[asset.name] = serial_id
            frappe.logger().info(f"Migrated Asset {asset.name} to Serial No {serial_id}")

    # 4. Update Historical Records
    for asset_name, serial_id in asset_to_serial_map.items():
        # Update Sapphire Maintenance Records
        # Note: field was renamed from 'asset' to 'serial_no' in Phase 2
        frappe.db.sql("""
            UPDATE `tabSapphire Maintenance Record`
            SET serial_no = %s
            WHERE asset = %s
        """, (serial_id, asset_name))

        # Update Sales Order Items
        # Note: field was renamed/added from 'custom_asset' to 'custom_serial_no' in Phase 1
        frappe.db.sql("""
            UPDATE `tabSales Order Item`
            SET custom_serial_no = %s
            WHERE custom_asset = %s
        """, (serial_id, asset_name))

    # 5. Optional: Archive or mark Assets as migrated
    # Since we can't easily delete Assets with history, we'll just set a status or comment.
    for asset_name in asset_to_serial_map.keys():
        frappe.db.set_value("Asset", asset_name, "status", "Scrapped")
        frappe.db.add_info_log(f"Asset {asset_name} has been migrated to Serial No system.")

    frappe.logger().info(f"Successfully migrated {len(asset_to_serial_map)} assets to Serial No.")
