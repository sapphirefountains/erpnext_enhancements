const item = {
    "item_code": "801-060",
    "item_name": "TEE, STRAIGHT, SOC, PVC, 6\" SCH80",
    "mr": "MAT-MR-2025-00006-2",
    "warehouse": null,
    "ordered_qty": 10
};

const testCases = [
    { term: "TEE", expected: true },
    { term: "PVC", expected: true },
    { term: "TEE PVC", expected: true }, // The key case: fuzzy match
    { term: "PVC TEE", expected: true }, // Order shouldn't matter
    { term: "SCH80 6\"", expected: true },
    { term: "TEE ABS", expected: false }, // ABS is not in the string
    { term: "XYZ", expected: false }
];

console.log("Testing Fuzzy Search Logic:");
testCases.forEach(tc => {
    // Logic to be implemented in Vue
    const globalSearchTerm = tc.term;
    const searchTokens = globalSearchTerm.toLowerCase().split(/\s+/).filter(t => t);

    // Check if ALL tokens are present in ANY of the values (or the concatenated string of values)
    // The previous implementation was: Object.values(item).some(...)
    // But if we want "TEE" (in name) and "MR-..." (in mr) to match, we should probably check against the whole row data combined?
    // Wait, the user requirement is likely about finding a row that contains all terms.
    // If I search "TEE MR-2025", and TEE is in item_name and MR-2025 is in mr field, it SHOULD match.
    // So the strategy: Concatenate all values into one searchable string.

    const itemString = Object.values(item).join(' ').toLowerCase();
    const isMatch = searchTokens.every(token => itemString.includes(token));

    const result = isMatch === tc.expected ? "PASS" : "FAIL";
    console.log(`[${result}] Term: "${tc.term}", Expected: ${tc.expected}, Got: ${isMatch}`);
});
