const item = {
    "item_code": "801-060",
    "item_name": "TEE, STRAIGHT, SOC, PVC, 6\" SCH80",
    "mr": "MAT-MR-2025-00006-2",
    "warehouse": null,
    "ordered_qty": 10
};

const terms = ["TEE", "tee", "PVC", "SCH80", "801", "060", "TEE PVC"];

terms.forEach(term => {
    const hasMatch = Object.values(item).some(val => String(val).toLowerCase().includes(term.toLowerCase()));
    console.log(`Term: "${term}", Match: ${hasMatch}`);
});
