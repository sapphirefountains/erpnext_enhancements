"""Regenerate <DocType>-main-field_order fixture values from layout specs.

See scripts/layout/README.md for the spec schema and lint rules. Pure Python,
no frappe import — runnable from Windows against the WSL bench's doctype JSONs.
"""

import argparse
import bisect
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
DEFAULT_BENCH_ROOT = r"\\wsl$\Ubuntu-26.04\home\nbbsh\frappe-bench"

PS_PATH = REPO_ROOT / "erpnext_enhancements" / "fixtures" / "property_setter.json"
CF_PATH = REPO_ROOT / "erpnext_enhancements" / "fixtures" / "custom_field.json"
REGISTRY_PATH = HERE / "code_owned_fields.json"
SPECS_DIR = HERE / "specs"

DOCTYPE_PATHS = {
    "Item": "apps/erpnext/erpnext/stock/doctype/item/item.json",
    "Material Request": "apps/erpnext/erpnext/stock/doctype/material_request/material_request.json",
    "Purchase Order": "apps/erpnext/erpnext/buying/doctype/purchase_order/purchase_order.json",
    "Purchase Receipt": "apps/erpnext/erpnext/stock/doctype/purchase_receipt/purchase_receipt.json",
    "Purchase Invoice": "apps/erpnext/erpnext/accounts/doctype/purchase_invoice/purchase_invoice.json",
    "Supplier Quotation": "apps/erpnext/erpnext/buying/doctype/supplier_quotation/supplier_quotation.json",
    "Request for Quotation": "apps/erpnext/erpnext/buying/doctype/request_for_quotation/request_for_quotation.json",
}

BREAKS = {"Section Break", "Column Break", "Tab Break"}


class LintError(Exception):
    pass


def load_json(path):
    with open(path, "rb") as f:
        return json.loads(f.read())


def dump_fixture(path, data):
    out = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=True) + "\n"
    with open(path, "wb") as f:
        f.write(out.encode("utf-8"))


class FieldInfo:
    def __init__(self, fieldname, fieldtype, reqd, hidden, source, show_dashboard=0):
        self.fieldname = fieldname
        self.fieldtype = fieldtype or "Data"
        self.reqd = int(reqd or 0)
        self.hidden = int(hidden or 0)
        self.source = source  # "standard" | "fixture" | "registry"
        self.show_dashboard = int(show_dashboard or 0)


def build_universe(doctype, bench_root, custom_fields, registry):
    """Ordered standard fields + custom/registry fields for one doctype."""
    dt_json = load_json(Path(bench_root) / DOCTYPE_PATHS[doctype])
    # the JSON's "fields" array is not in display order — "field_order" is
    defs = {f["fieldname"]: f for f in dt_json["fields"]}
    if set(dt_json["field_order"]) != set(defs):
        raise LintError(f"{doctype}: bench JSON field_order does not match fields array")
    standard = [
        FieldInfo(n, defs[n].get("fieldtype"), defs[n].get("reqd"), defs[n].get("hidden"),
                  "standard", defs[n].get("show_dashboard"))
        for n in dt_json["field_order"]
    ]
    extra = [
        FieldInfo(r["fieldname"], r.get("fieldtype"), r.get("reqd"), r.get("hidden"), "fixture")
        for r in custom_fields
        if r["dt"] == doctype
    ] + [
        FieldInfo(r["fieldname"], r.get("fieldtype"), r.get("reqd"), r.get("hidden"), "registry")
        for r in registry.get(doctype, [])
    ]
    universe = {f.fieldname: f for f in standard + extra}
    if len(universe) != len(standard) + len(extra):
        seen, dupes = set(), set()
        for f in standard + extra:
            (dupes if f.fieldname in seen else seen).add(f.fieldname)
        raise LintError(f"{doctype}: duplicate fieldnames across sources: {sorted(dupes)}")
    return standard, universe


def macro_ranges(standard):
    """For every break field, the fieldnames it spans in bench-default order."""
    names = [f.fieldname for f in standard]
    types = {f.fieldname: f.fieldtype for f in standard}
    ranges = {}
    for i, f in enumerate(standard):
        if f.fieldtype == "Section Break":
            end = {"Section Break", "Tab Break"}
        elif f.fieldtype == "Tab Break":
            end = {"Tab Break"}
        else:
            continue
        members = []
        for name in names[i + 1:]:
            if types[name] in end:
                break
            members.append(name)
        ranges[f.fieldname] = members
    return ranges


def expand_layout(doctype, spec, standard, universe):
    layout = spec["layout"]
    ranges = macro_ranges(standard)

    explicit = [e for e in layout if isinstance(e, str)]
    macros = []  # (break_fieldname, kind)
    for e in layout:
        if isinstance(e, dict):
            (kind, name), = e.items()
            if kind not in ("section", "tab"):
                raise LintError(f"{doctype}: unknown macro kind {kind!r}")
            if name not in universe:
                raise LintError(f"{doctype}: macro target {name!r} not a known field")
            if name not in ranges:
                raise LintError(
                    f"{doctype}: macro target {name!r} is not a standard "
                    f"{'Section' if kind == 'section' else 'Tab'} Break (custom breaks "
                    "must be placed explicitly with explicit members)"
                )
            expected = "Section Break" if kind == "section" else "Tab Break"
            if universe[name].fieldtype != expected:
                raise LintError(f"{doctype}: macro {{{kind}: {name}}} targets a {universe[name].fieldtype}")
            macros.append(name)

    unknown = [n for n in explicit if n not in universe]
    if unknown:
        raise LintError(f"{doctype}: layout references unknown fields: {unknown}")

    # Assign every range member to its narrowest claiming macro.
    claimed_by = {}
    for m in macros:
        for member in ranges[m]:
            prev = claimed_by.get(member)
            if prev is None or len(ranges[m]) < len(ranges[prev]):
                claimed_by[member] = m
        claimed_by[m] = m  # a macro'd break belongs to itself, not an enclosing tab

    explicit_set = set(explicit)
    order = []
    for e in layout:
        if isinstance(e, str):
            order.append(e)
        else:
            (kind, name), = e.items()
            order.append(name)
            order.extend(
                member for member in ranges[name]
                if member not in explicit_set and claimed_by.get(member) == name
            )
    return order


def lint(doctype, order, universe, spec):
    errors = []

    # 1. exact permutation
    dupes = sorted({n for n in order if order.count(n) > 1})
    missing = sorted(set(universe) - set(order))
    extra = sorted(set(order) - set(universe))
    if dupes:
        errors.append(f"duplicated in layout: {dupes}")
    if missing:
        errors.append(f"missing from layout: {missing}")
    if extra:
        errors.append(f"not in field universe: {extra}")
    if errors:
        raise LintError(f"{doctype}:\n  " + "\n  ".join(errors))

    ft = {n: universe[n].fieldtype for n in order}
    hidden = {n: universe[n].hidden for n in order}

    # 2. column breaks need a preceding leaf or section in their tab segment
    seen_section = seen_leaf = False
    for n in order:
        t = ft[n]
        if t == "Tab Break":
            seen_section = seen_leaf = False
        elif t == "Section Break":
            seen_section = True
        elif t == "Column Break":
            if not (seen_section or seen_leaf):
                errors.append(f"Column Break {n!r} starts its tab segment")
        else:
            seen_leaf = True

    # 3. visible tabs need >=1 visible leaf
    tab = None
    tab_has_leaf = {}
    for n in order:
        if ft[n] == "Tab Break":
            tab = n
            tab_has_leaf.setdefault(n, False)
        elif tab is not None and ft[n] not in BREAKS and not hidden[n]:
            tab_has_leaf[tab] = True
    for t, has in tab_has_leaf.items():
        if not has and not hidden[t] and not universe[t].show_dashboard:
            errors.append(f"visible Tab Break {t!r} has no visible leaf fields")

    # 4. reqd fields visible and before the first Tab Break
    whitelist = set(spec.get("reqd_after_first_tab_ok", []))
    first_tab = next((i for i, n in enumerate(order) if ft[n] == "Tab Break"), len(order))
    for i, n in enumerate(order):
        if not universe[n].reqd or n in whitelist:
            continue
        if hidden[n]:
            errors.append(f"required field {n!r} is hidden")
        if i > first_tab:
            errors.append(f"required field {n!r} is after the first Tab Break")

    if errors:
        raise LintError(f"{doctype}:\n  " + "\n  ".join(errors))


def setter_record(doc_type, prop, value, field_name=None, property_type="Check"):
    return {
        "default_value": None,
        "doc_type": doc_type,
        "docstatus": 0,
        "doctype": "Property Setter",
        "doctype_or_field": "DocField" if field_name else "DocType",
        "field_name": field_name,
        "is_system_generated": 0,
        "module": None,
        "name": f"{doc_type}-{field_name or 'main'}-{prop}",
        "property": prop,
        "property_type": property_type,
        "row_name": None,
        "value": value,
    }


def apply_to_fixtures(results, check_only):
    ps = load_json(PS_PATH)
    by_name = {r["name"]: r for r in ps}
    names_sorted = [r["name"] for r in ps]
    if names_sorted != sorted(names_sorted):
        raise LintError("property_setter.json is not name-sorted")

    changes = []
    for doctype, (order, setters) in results.items():
        name = f"{doctype}-main-field_order"
        value = json.dumps(order)
        rec = by_name.get(name)
        if rec is None:
            raise LintError(f"{name} not found in property_setter.json (expected to exist)")
        if rec["value"] != value:
            changes.append(f"update {name} ({len(order)} fields)")
            rec["value"] = value

        for s in setters:
            new = setter_record(doctype, s["property"], s["value"],
                                field_name=s.get("field"),
                                property_type=s.get("property_type", "Check"))
            existing = by_name.get(new["name"])
            if existing is None:
                pos = bisect.bisect_left(names_sorted, new["name"])
                ps.insert(pos, new)
                names_sorted.insert(pos, new["name"])
                by_name[new["name"]] = new
                changes.append(f"add    {new['name']} = {new['value']!r}")
            elif (existing["value"], existing["property_type"]) != (new["value"], new["property_type"]):
                changes.append(f"update {new['name']} -> {new['value']!r}")
                existing["value"] = new["value"]
                existing["property_type"] = new["property_type"]

    if not check_only and changes:
        dump_fixture(PS_PATH, ps)
    return changes


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bench-root", default=DEFAULT_BENCH_ROOT)
    ap.add_argument("--only", action="append", help="limit to doctype(s)")
    ap.add_argument("--check", action="store_true", help="lint only, write nothing")
    ap.add_argument("--dump", action="store_true", help="print each generated array")
    args = ap.parse_args()

    custom_fields = load_json(CF_PATH)
    registry = load_json(REGISTRY_PATH)

    spec_files = sorted(SPECS_DIR.glob("*.json"))
    if not spec_files:
        print("no spec files found", file=sys.stderr)
        return 1

    results, failed = {}, False
    for path in spec_files:
        spec = load_json(path)
        doctype = spec["doctype"]
        if args.only and doctype not in args.only:
            continue
        try:
            standard, universe = build_universe(doctype, args.bench_root, custom_fields, registry)
            order = expand_layout(doctype, spec, standard, universe)
            lint(doctype, order, universe, spec)
        except LintError as e:
            print(f"FAIL {e}", file=sys.stderr)
            failed = True
            continue
        results[doctype] = (order, spec.get("setters", []))
        tabs = [n for n in order if universe[n].fieldtype == "Tab Break" and not universe[n].hidden]
        first_tab_len = next((i for i, n in enumerate(order) if universe[n].fieldtype == "Tab Break"), len(order))
        print(f"OK   {doctype}: {len(order)} fields, first tab {first_tab_len}, tabs: {', '.join(tabs)}")
        if args.dump:
            for n in order:
                f = universe[n]
                marks = "".join([" [reqd]" if f.reqd else "", " [hid]" if f.hidden else ""])
                print(f"   {n} | {f.fieldtype}{marks}")

    if failed:
        return 1
    if not results:
        print("nothing matched --only", file=sys.stderr)
        return 1

    changes = apply_to_fixtures(results, args.check)
    verb = "would change" if args.check else "changed"
    print(f"\n{verb} {len(changes)} fixture record(s):")
    for c in changes:
        print(" ", c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
