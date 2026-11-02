"""Bench-free tests for the Fountain Designer's jurisdiction table (phase J, v1.772.0).

* the loader (``engine/data/jurisdictions.py``): get, codes, names, unknown codes, the schema every row
  keeps, a cite beside every value, and the package copy equal to the researched catalog;
* the rules keyed on the state's row, never on "UT": a fixture state with every flag opposite to
  Utah's (``ZZ``), one that regulates interactive features under its own rule (``YY``), and a code the
  table does not have (``XX``), through ``derive_site``, ``regulated_site``, the interactive, mist and
  winterization families, the electrical and structural reports, and the whole derive;
* ``workforce.sites.elevation_at`` against a stubbed ``requests``.

Plain pytest functions: their own ``python -m pytest`` step in ci.yml.

Run: python -m pytest erpnext_enhancements/tests/test_jurisdictions.py -q
"""

import copy
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering import derive
from erpnext_enhancements.water_engineering.engine import jurisdiction as juris
from erpnext_enhancements.water_engineering.engine import kits
from erpnext_enhancements.water_engineering.engine.data import jurisdictions
from erpnext_enhancements.water_engineering.engine.electrical import electrical_report
from erpnext_enhancements.water_engineering.engine.families import (
    interactive,
    mist,
    winterization,
)
from erpnext_enhancements.water_engineering.engine.structural import structural_takeoff

CATALOG = REPO_ROOT / "docs" / "fountain-designer" / "catalog" / "jurisdictions.json"
SECTIONS = {"name", "codes", "regulation", "climate", "licensing"}
CODE_KEYS = {"nec", "ibc", "irc", "ispsc", "mahc"}
REGULATION_KEYS = {"agency", "pool_rule", "pool_cite", "spa_regulated", "iwf_regulated", "iwf_rule", "iwf_cite",
                   "iwf_like_utah", "fogger_potable", "fogger_cite", "plan_review", "plan_review_cite", "notes"}
CLIMATE_KEYS = {"design_city", "elevation_ft", "air_temp_f", "heating_design_f", "water_temp_f", "frost_depth_in",
                "hardness_ppm", "winterize", "seismic_default", "cites"}

# Every flag opposite to Utah's: no spa or interactive rule, fog from any supply, no plan review, a warm low
# design city that never freezes, codes adopted locally or not at all, and editions other than the engine's.
ZZ = {
    "name": "Testland",
    "codes": {"nec": {"edition": 2020, "basis": "local", "effective": "2022-01-01", "amendments": "", "cite": "ZZ NEC cite"},
              "ibc": {"edition": 2018, "basis": "none", "cite": "ZZ IBC cite"},
              "irc": {"edition": 2018, "basis": "local", "cite": "ZZ IRC cite"},
              "ispsc": {"adopted": False, "edition": None, "basis": "none", "cite": "ZZ ISPSC cite"},
              "mahc": {"status": "adopted", "cite": "ZZ MAHC cite"}},
    "regulation": {"agency": "Testland Department of Health", "pool_rule": "TL 1-2", "pool_cite": "ZZ pool cite",
                   "spa_regulated": False, "iwf_regulated": False, "iwf_rule": None, "iwf_cite": None,
                   "iwf_like_utah": False, "fogger_potable": False, "fogger_cite": None, "plan_review": "not required",
                   "plan_review_cite": "ZZ plan cite", "notes": "Testland leaves splash pads to its cities."},
    "climate": {"design_city": "Testville", "elevation_ft": 50, "air_temp_f": 92, "heating_design_f": 45, "water_temp_f": 80,
                "frost_depth_in": 0, "hardness_ppm": 120, "winterize": False, "seismic_default": "A",
                "cites": {"elevation_ft": "ZZ elevation cite", "air_temp_f": "ZZ air cite", "water_temp_f": "ZZ water cite",
                          "frost_depth_in": "ZZ frost cite", "hardness_ppm": "ZZ hardness cite", "winterize": "ZZ winter cite",
                          "seismic_default": "ZZ seismic cite"}},
    "licensing": {"contractor": "a Testland pool contractor license", "electrical": "a state electrical license",
                  "permits": "the city issues building and electrical permits", "cites": ["ZZ licensing cite"]},
}
# Regulates interactive features, but under its own rule (the Utah checklist is advice there).
YY = copy.deepcopy(ZZ)
YY["name"] = "Ownrule"
YY["regulation"].update(iwf_regulated=True, iwf_rule="OR 9-9", iwf_cite="YY iwf cite", iwf_like_utah=False,
                        notes="Ownrule's spray-ground rule sets its own turnover.")


@pytest.fixture()
def states(monkeypatch):
    table = jurisdictions.table()
    monkeypatch.setitem(table, "ZZ", copy.deepcopy(ZZ))
    monkeypatch.setitem(table, "YY", copy.deepcopy(YY))
    return table


def codes_of(checks):
    return {(c.code if hasattr(c, "code") else c["code"]): (c.severity if hasattr(c, "severity") else c["severity"])
            for c in checks}


def plaza(**inputs):
    rows = [{"label": "A", "gpm": 10, "count": 4, "orifice_in": 0.25}, {"label": "fog", "gpm": 1, "count": 1, "fogger": True}]
    return {"id": "f1", "family": "interactive", "inputs": dict({"play_features": rows, "inlet_count": 2}, **inputs)}


# ------------------------------------------------------------------------- the loader


def test_get_codes_and_names():
    ut = jurisdictions.get("UT")
    assert ut and ut["name"] == "Utah"
    assert jurisdictions.get("ut") is ut, "any case"
    assert jurisdictions.get(" UT ") is ut
    for blank in (None, "", "XX", "Utah"):
        assert jurisdictions.get(blank) is None, blank
    assert jurisdictions.codes()[0] == "UT"
    assert list(jurisdictions.names()) == jurisdictions.codes()
    assert jurisdictions.names()["UT"] == "Utah"
    assert jurisdictions.name("XX") == "XX" and jurisdictions.name("") == "the site's state"


def test_codes_are_utah_first_then_by_name(states):
    order = jurisdictions.codes()
    assert order[0] == "UT"
    names = [jurisdictions.names()[c] for c in order[1:]]
    assert names == sorted(names) and "Testland" in names


def test_the_file_keeps_the_shared_schema():
    data = json.loads(jurisdictions.PATH.read_text(encoding="utf-8"))
    assert data["schema"] == jurisdictions.SCHEMA and data.get("generated")
    assert data["jurisdictions"], "at least Utah"
    for code, row in data["jurisdictions"].items():
        assert len(code) == 2 and code == code.upper(), code
        assert SECTIONS <= set(row), code
        assert CODE_KEYS <= set(row["codes"]), code
        assert REGULATION_KEYS <= set(row["regulation"]), code
        assert CLIMATE_KEYS <= set(row["climate"]), code
        assert {"contractor", "electrical", "permits", "cites"} <= set(row["licensing"]), code


def test_every_value_on_file_is_cited():
    """Nikolas, 2026-11-01: ship every cited row. A value with no cite is left null."""
    for code, row in jurisdictions.table().items():
        for key in ("nec", "ibc", "irc", "ispsc"):
            c = row["codes"][key]
            if c.get("edition") is not None or c.get("adopted") is not None:
                assert c.get("cite"), f"{code} {key}"
        if row["codes"]["mahc"].get("status") is not None:
            assert row["codes"]["mahc"].get("cite"), f"{code} mahc"
        reg = row["regulation"]
        for flag, cite in (("iwf_regulated", "iwf_cite"), ("fogger_potable", "fogger_cite"), ("plan_review", "plan_review_cite")):
            if reg.get(flag):
                assert reg.get(cite), f"{code} {flag}"
        if reg.get("pool_rule") or reg.get("spa_regulated") is not None:
            assert reg.get("pool_cite"), f"{code} pool"
        climate = row["climate"]
        for key, value in climate.items():
            if key in ("design_city", "cites") or value is None:
                continue
            assert (climate.get("cites") or {}).get(key), f"{code} climate {key}"
        lic = row["licensing"]
        if any(lic.get(k) for k in ("contractor", "electrical", "permits")):
            assert lic.get("cites"), f"{code} licensing"


def test_the_package_copy_matches_the_researched_catalog():
    """The researched table lives in docs/fountain-designer/catalog/; the engine reads its copy in the package."""
    if not CATALOG.exists():
        pytest.skip("the researched catalog has not landed yet: the package holds the Utah seed")
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    package = json.loads(jurisdictions.PATH.read_text(encoding="utf-8"))
    assert package["jurisdictions"] == catalog["jurisdictions"]
    assert package["schema"] == catalog["schema"]


# ------------------------------------------------------------------------- derive_site


def test_climate_defaults_come_from_the_states_row(states):
    site, result = derive.derive_site({"jurisdiction": "ZZ", "venue_type": "Decorative Fountain"}, {"kwh_rate": 0.12})
    assert (site["elevation_ft"], site["air_temp_f"], site["water_temp_f"]) == (50, 92, 80)
    assert (site["frost_depth_in"], site["hardness_ppm"], site["seismic_sdc"], site["winterize"]) == (0, 120, "A", False)
    assert site["kwh_rate"] == 0.12
    assert site["sources"]["elevation_ft"] == "default" and site["sources"]["kwh_rate"] == "house_rule"
    assert result["inputs"]["frost_depth_in"]["ref"] == "ZZ frost cite"
    assert "ZZ elevation cite" in result["citations"] and "ZZ seismic cite" in result["citations"]
    # the house rates are Salt Lake City's: a site elsewhere is told to type its own
    assert result["warnings"] == ["The power and water rates are the house rules, sourced for Salt Lake City: type "
                                  "the site's own utility rates for Testland, or the running costs are Utah's."]
    _, utah = derive.derive_site({"jurisdiction": "UT"}, {"kwh_rate": 0.12})
    assert not utah["warnings"]
    _, typed = derive.derive_site({"jurisdiction": "ZZ", "kwh_rate": 0.2, "water_rate_per_ccf": 5}, {"kwh_rate": 0.12})
    assert not typed["warnings"]


def test_a_new_state_replaces_the_old_states_defaults_but_never_a_typed_value(states):
    ut, _ = derive.derive_site({"jurisdiction": "UT", "venue_type": "Decorative Fountain"}, {})
    assert ut["frost_depth_in"] == 30 and ut["winterize"] is True
    typed = dict(ut, jurisdiction="ZZ", hardness_ppm=400)
    typed["sources"] = {k: v for k, v in ut["sources"].items() if k != "hardness_ppm"}  # the page drops a typed field's source
    zz, _ = derive.derive_site(typed, {})
    assert zz["frost_depth_in"] == 0 and zz["elevation_ft"] == 50 and zz["winterize"] is False
    assert zz["hardness_ppm"] == 400 and "hardness_ppm" not in zz["sources"]


def test_a_stored_model_without_sources_keeps_every_value(states):
    """Designs saved before phase J carry no ``sources``: every value in them is treated as typed."""
    old = {"jurisdiction": "ZZ", "elevation_ft": 4300, "frost_depth_in": 30}
    site, _ = derive.derive_site(old, {})
    assert (site["elevation_ft"], site["frost_depth_in"]) == (4300, 30)


def test_an_unknown_state_is_no_data_never_utah(states):
    ut, _ = derive.derive_site({"jurisdiction": "UT"}, {})
    site, result = derive.derive_site(dict(ut, jurisdiction="XX"), {})
    for key in ("elevation_ft", "air_temp_f", "water_temp_f", "frost_depth_in", "hardness_ppm", "seismic_sdc", "winterize"):
        assert site.get(key) is None, key
    assert "XX" in result["warnings"][0] and "type the site" in result["warnings"][0].lower()
    blank, result = derive.derive_site({"jurisdiction": ""}, {})
    assert blank.get("frost_depth_in") is None and "no jurisdiction picked" in result["warnings"][0]


def test_a_state_missing_a_value_warns_by_name(states, monkeypatch):
    row = copy.deepcopy(ZZ)
    row["climate"]["frost_depth_in"] = None
    monkeypatch.setitem(jurisdictions.table(), "ZZ", row)
    site, result = derive.derive_site({"jurisdiction": "ZZ"}, {})
    assert site.get("frost_depth_in") is None
    assert result["warnings"] == ["Testland has no frost depth on file for the design city: type it on the Site step."]


def test_lookups_beat_the_state_and_persist_until_a_new_lookup(states):
    lookup = {"lat": 33.4, "lng": -112.0, "elevation_ft": 1086.0, "seismic_sdc": "B", "sds": 0.18, "sd1": 0.09,
              "cites": {"elevation_ft": "Google elevation", "sds": "USGS"}}
    site, result = derive.derive_site({"jurisdiction": "ZZ"}, {}, lookup)
    assert (site["elevation_ft"], site["seismic_sdc"], site["sds"]) == (1086.0, "B", 0.18)
    assert site["sources"]["elevation_ft"] == "lookup" and result["inputs"]["elevation_ft"]["ref"] == "Google elevation"
    again, result = derive.derive_site(site, {})  # the page's derive: no lookup
    assert again["elevation_ft"] == 1086.0 and again["sources"]["elevation_ft"] == "lookup"
    assert result["inputs"]["elevation_ft"]["source"] == "lookup"


def test_a_junk_seismic_category_is_ignored(states):
    site, _ = derive.derive_site({"jurisdiction": "ZZ"}, {}, {"seismic_sdc": "Z9"})
    assert site["seismic_sdc"] == "A" and site["sources"]["seismic_sdc"] == "default"


# ------------------------------------------------------------------------- regulated_site


@pytest.mark.parametrize("code, venue, regulated", [
    ("UT", "Interactive Water Feature", True), ("UT", "Commercial Spa", True), ("UT", "Commercial Pool", True),
    ("ZZ", "Interactive Water Feature", False), ("ZZ", "Commercial Spa", False), ("ZZ", "Commercial Pool", True),
    ("YY", "Interactive Water Feature", True),
    ("XX", "Interactive Water Feature", False), ("XX", "Commercial Spa", True), ("XX", "Wading Pool", True),
    ("ZZ", "Decorative Fountain", False),
])
def test_regulated_venues_key_on_the_row(states, code, venue, regulated):
    assert derive.regulated_site({"jurisdiction": code, "venue_type": venue}) is regulated


# ------------------------------------------------------------------------- families


def test_utah_runs_the_interactive_checklist_as_blockers_with_its_cite(states):
    c = interactive.checks(plaza(), site={"jurisdiction": "UT"})
    sev = codes_of(c)
    assert sev["IWF_INLETS"] == "blocker" and "IWF_STATE_RULES" not in sev
    inlets = next(x for x in c if x.code == "IWF_INLETS")
    assert inlets.citation == jurisdictions.get("UT")["regulation"]["iwf_cite"]


def test_a_state_that_does_not_regulate_gets_warnings_and_its_notes(states):
    c = interactive.checks(plaza(), site={"jurisdiction": "ZZ"})
    sev = codes_of(c)
    assert sev["IWF_INLETS"] == "warning" and sev["IWF_VELOCITY"] == "warning"
    note = next(x for x in c if x.code == "IWF_STATE_RULES")
    assert note.severity == "info" and "does not regulate" in note.title
    assert "leaves splash pads to its cities" in note.detail


def test_a_state_with_its_own_rule_gets_warnings_cited_with_its_rule(states):
    c = interactive.checks(plaza(), site={"jurisdiction": "YY"})
    assert codes_of(c)["IWF_INLETS"] == "warning"
    note = next(x for x in c if x.code == "IWF_STATE_RULES")
    assert "own rule (OR 9-9)" in note.title and note.citation == "YY iwf cite"
    assert next(x for x in c if x.code == "IWF_INLETS").citation == "YY iwf cite"
    secondary = next(x for x in c if x.code == "IWF_SECONDARY")
    assert secondary.citation.startswith("YY iwf cite; ") and "MAHC" in secondary.citation


def test_no_jurisdiction_data_is_advice_with_a_note(states):
    for site in ({"jurisdiction": "XX"}, None):
        c = interactive.checks(plaza(), site=site)
        assert codes_of(c)["IWF_INLETS"] == "warning"
        assert codes_of(c)["IWF_NO_JURISDICTION"] == "info"


def test_mist_potable_supply_keys_on_the_row(states):
    f = {"id": "m", "inputs": {"nozzle_count": 20, "supply": "reservoir", "line_rating_psi": 2000}}
    ut = next(x for x in mist.checks(f, site={"jurisdiction": "UT"}) if x.code == "MIST_NOT_POTABLE")
    assert ut.severity == "blocker" and ut.citation == jurisdictions.get("UT")["regulation"]["fogger_cite"]
    zz = next(x for x in mist.checks(f, site={"jurisdiction": "ZZ"}) if x.code == "MIST_NOT_POTABLE")
    assert zz.severity == "warning" and "Testland" in zz.detail


def test_the_dry_climate_hint_keys_on_the_climate(states):
    f = {"id": "m", "inputs": {"nozzle_count": 20, "orifice_in": "0.008", "line_rating_psi": 2000}}

    def hint(site):
        return "MIST_DRY_CLIMATE" in codes_of(mist.checks(f, site=site))

    assert hint({"jurisdiction": "UT"}), "Salt Lake City: 4,300 ft and a 95 F design day"
    assert not hint({"jurisdiction": "ZZ"}), "a low design city with no longitude: not called dry"
    assert hint({"jurisdiction": "ZZ", "lng": -112.0}), "west of the 100th meridian"
    assert not hint({"jurisdiction": "UT", "lng": -80.0}), "east of it"
    assert not hint({"jurisdiction": "UT", "rh_pct": 70}), "a humid site says so"
    assert not hint({"jurisdiction": "XX"}), "nothing to go on"
    assert juris.dry_climate({"jurisdiction": "XX"}) is None


def test_a_state_that_does_not_freeze_gets_no_winterization(states):
    model = {"site": {"jurisdiction": "ZZ", "frost_depth_in": 0, "winterize": False}, "features": [], "network": {
        "pumps": [{"id": "p1", "location": "room"}], "segments": [{"id": "s1", "buried_depth_in": 6}]}}
    checks = winterization.checks(model)
    assert codes_of(checks) == {"WINTER_NOT_NEEDED": "info"}
    assert "Testland" in checks[0].detail
    assert winterization.calc(model).data["needed"] is False
    # the state's flag decides when the site leaves it blank; a site's own tick wins
    assert juris.winterize({"jurisdiction": "ZZ"}) is False
    assert juris.winterize({"jurisdiction": "ZZ", "winterize": True}) is True
    assert juris.winterize({"jurisdiction": "XX", "frost_depth_in": 12}) is True


def test_no_frost_depth_is_said_not_assumed(states):
    model = {"site": {"jurisdiction": "XX"}, "features": [], "network": {"segments": [{"id": "s1", "buried_depth_in": 6}]}}
    assert winterization.calc(model).data["frost_depth_in"] is None
    assert "WINTER_FROST_UNKNOWN" in codes_of(winterization.checks(model))


# ------------------------------------------------------------------------- reports


def test_the_electrical_report_cites_the_states_nec(states):
    model = kits.apply_kit("lobby-sheet-wall-8ft")
    model["site"]["jurisdiction"] = "ZZ"
    rep = electrical_report(model)
    assert rep["edition"] == "NFPA 70 (NEC) 2023", "the tables' edition"
    assert rep["state_edition"] == "NEC 2020 (local, effective 2022-01-01)" and rep["code_cite"] == "ZZ NEC cite"
    assert "Testland" in rep["verify"] and "authority having jurisdiction" in rep["verify"]
    assert any("Testland enforces NEC 2020" in w for w in rep["warnings"])
    assert any("Confirm the NEC edition" in w and "adopted city by city" in w for w in rep["warnings"])


def test_the_ahj_note_names_each_codes_own_reason(states):
    note = juris.ahj_note({"jurisdiction": "ZZ"}, jurisdictions.get("ZZ"))
    assert note.startswith("Confirm the NEC, IBC and IRC editions with the authority having jurisdiction. Testland, ")
    assert "NEC and IRC: adopted city by city" in note and "IBC: no statewide code" in note
    assert "ISPSC" not in note, "a code the state does not adopt is nothing to confirm"
    utah = juris.ahj_note({"jurisdiction": "UT"}, jurisdictions.get("UT"))
    if utah:  # the seed has no editions; the researched table may
        assert "city by city" not in utah or jurisdictions.get("UT")["codes"]["nec"]["basis"] == "local"


def test_the_structural_takeoff_cites_the_states_ibc_and_ispsc(states):
    model = kits.apply_kit("lobby-sheet-wall-8ft")
    model["site"].update(jurisdiction="ZZ", venue_type="Commercial Pool")
    t = structural_takeoff(model)
    assert t["codes"]["ibc"]["edition"] == "IBC 2018 (none)" and t["codes"]["ispsc"]["edition"] == "ISPSC not adopted"
    check = next(c for c in t["checks"] if c["code"] == "STRUCT_CODE_EDITION")
    assert check["severity"] == "info" and "IBC" in check["detail"]
    fountain = structural_takeoff(dict(model, site=dict(model["site"], venue_type="Decorative Fountain")))
    assert set(fountain["codes"]) == {"ibc"}, "the ISPSC only for a pool, spa or interactive venue"


# ------------------------------------------------------------------------- the whole derive


def test_the_derive_runs_on_the_opposite_state(states):
    model = kits.apply_kit("plaza-interactive-12-jets")
    model["site"]["jurisdiction"] = "ZZ"
    out = derive.derive_model(model)
    site = out["model"]["site"]
    assert site["regulated"] is False, "Testland does not regulate interactive features"
    assert (site["frost_depth_in"], site["winterize"], site["seismic_sdc"]) == (0, False, "A")
    j = out["results"]["jurisdiction"]["value"]
    assert j["known"] and j["name"] == "Testland" and j["regulated_venues"] == ["public pools"]
    assert j["editions"] == ["NEC 2020", "IBC 2018", "IRC 2018", "ISPSC not adopted"]
    assert j["confirm_ahj"] and j["ahj_local"] and "Testland" in j["ahj_note"]
    assert j["licensing"].startswith("Contractor: a Testland pool contractor license")
    issues = {i["code"]: i for i in out["issues"]}
    assert issues["JURISDICTION_CONFIRM_AHJ"]["severity"] == "warning"
    assert issues["IWF_STATE_RULES"]["severity"] == "info"
    assert out["outputs"]["winterization"]["needed"] is False
    assert "WINTER_NOT_NEEDED" in issues
    assert out["paper"]["plan_check"]["nec_state_edition"].startswith("NEC 2020")
    json.dumps(out, allow_nan=False)


def test_the_derive_on_an_unknown_state_has_no_utah_numbers(states):
    model = kits.apply_kit("lobby-sheet-wall-8ft")
    model["site"]["jurisdiction"] = "XX"
    out = derive.derive_model(model)
    site = out["model"]["site"]
    assert site.get("frost_depth_in") is None and site.get("elevation_ft") is None
    assert {i["code"] for i in out["issues"]} >= {"JURISDICTION_NO_DATA"}
    assert out["results"]["jurisdiction"]["value"]["known"] is False


def test_utah_still_derives_as_before(states):
    out = derive.derive_model(kits.apply_kit("plaza-interactive-12-jets"))
    site = out["model"]["site"]
    assert site["regulated"] is True and site["frost_depth_in"] == 30 and site["elevation_ft"] == 4300
    assert site["seismic_sdc"] == "D" and site["winterize"] is True
    assert out["results"]["jurisdiction"]["value"]["iwf_like_utah"] is True
    assert out["outputs"]["winterization"]["needed"] is True


# ------------------------------------------------------------------------- Google elevation


@pytest.fixture()
def sites(monkeypatch):
    frappe = types.ModuleType("frappe")
    utils = types.ModuleType("frappe.utils")
    utils.flt = lambda v, *a: float(v or 0)
    utils.cint = lambda v, *a: int(v or 0)
    frappe.utils = utils
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.setitem(sys.modules, "frappe.utils", utils)
    name = "erpnext_enhancements.workforce.sites"
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    # recorded, so the stub-bound copy leaves sys.modules after the test even when none was loaded before
    monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(module, "_geocoding_api_key", lambda: "KEY")
    requests = types.ModuleType("requests")
    monkeypatch.setitem(sys.modules, "requests", requests)
    module._requests = requests
    return module


class _Reply:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload


def test_elevation_at_converts_metres_to_feet(sites):
    seen = []

    def get(url, params=None, timeout=None):
        seen.append((url, params, timeout))
        return _Reply({"status": "OK", "results": [{"elevation": 1288.1, "location": {}}]})

    sites._requests.get = get
    feet, problem = sites.elevation_at(40.76, -111.89)
    assert problem is None and feet == pytest.approx(1288.1 / 0.3048, abs=0.05)
    url, params, timeout = seen[0]
    assert url == sites.ELEVATION_URL and params == {"locations": "40.76,-111.89", "key": "KEY"} and timeout


def test_elevation_at_never_raises_and_never_shows_the_key(sites, monkeypatch):
    sites._requests.get = lambda *a, **k: _Reply({"status": "REQUEST_DENIED", "error_message": "API not enabled"})
    assert sites.elevation_at(40.7, -111.9) == (None, "REQUEST_DENIED API not enabled")

    def boom(*a, **k):
        raise ConnectionError("https://maps.googleapis.com/...&key=KEY")

    sites._requests.get = boom
    feet, problem = sites.elevation_at(40.7, -111.9)
    assert feet is None and problem == "ConnectionError" and "KEY" not in problem
    assert sites.elevation_at(None, None) == (None, "no point")
    monkeypatch.setattr(sites, "_geocoding_api_key", lambda: "")
    assert sites.elevation_at(40.7, -111.9) == (None, "no key")
