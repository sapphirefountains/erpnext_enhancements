import frappe

def execute():
    project = "PROJ-0005"
    
    print(f"--- Debugging Project: {project} ---")
    
    # 1. Fetch all MR Items linked to this project
    items_linked = frappe.db.sql("""
        SELECT name, parent, item_code, project 
        FROM `tabMaterial Request Item` 
        WHERE project = %s
    """, (project,), as_dict=True)
    
    print(f"Items explicitly linked to {project}: {len(items_linked)}")
    for item in items_linked:
        print(f" - {item.parent} | {item.item_code} | {item.name}")
        
    # 2. Check if there are MRs that have SOME items linked but not others
    # Get list of MRs from above
    mr_names = list(set([d.parent for d in items_linked]))
    
    if mr_names:
        print(f"\n--- Checking MRs: {mr_names} ---")
        for mr in mr_names:
            all_items = frappe.db.sql("""
                SELECT name, item_code, project 
                FROM `tabMaterial Request Item` 
                WHERE parent = %s
            """, (mr,), as_dict=True)
            
            print(f"MR: {mr} has {len(all_items)} items:")
            for item in all_items:
                linked_str = "LINKED" if item.project == project else f"NOT LINKED (Project: {item.project})"
                print(f"   - {item.item_code}: {linked_str}")
    else:
        print("No MRs found for this project.")

execute()
