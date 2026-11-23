"""Validate jurisdictions.json: the Fountain Designer's cited table for the 48 contiguous states.

Stdlib only. Usage::

	python docs/fountain-designer/catalog/jurisdictions-validate.py [path/to/jurisdictions.json]

Fails (exit 1) on a missing or unexpected key, a wrong type, a value without a cite, a cite without a
URL behind a non-null value, a state missing from the 48, or a Utah row that disagrees with the engine's
defaults (``UTAH_ENGINE``). Prints the null count per state either way.
"""

import json
import os
import re
import sys

STATES = (
	"AL AZ AR CA CO CT DE FL GA ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND "
	"OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY"
).split()
BASIS = {"statewide", "local", "none"}
MAHC = {"adopted", "referenced", "none"}
PLAN_REVIEW = {"required", "not required", "local"}
SDC = {"A", "B", "C", "D", "D0", "D1", "D2", "E", "F"}
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Utah's row is the engine's Utah default since phase J (v1.772.0 removed derive.SITE_DEFAULTS): pinned here
# to the values its golden tests use. Hardness is 212 ppm, the upper bound of the SLC 2026 water report.
UTAH_ENGINE = {
	"elevation_ft": 4300,
	"air_temp_f": 95,
	"water_temp_f": 70,
	"frost_depth_in": 30,
	"hardness_ppm": 212,
}
NUM = (int, float)
CLIMATE_NUMS = (
	"elevation_ft",
	"air_temp_f",
	"heating_design_f",
	"water_temp_f",
	"frost_depth_in",
	"hardness_ppm",
)
CLIMATE_CITES = (*CLIMATE_NUMS, "seismic_default", "winterize")


class Checker:
	def __init__(self):
		self.errors = []
		self.nulls = {}

	def err(self, where, msg):
		self.errors.append(f"{where}: {msg}")

	def keys(self, where, obj, expected):
		if not isinstance(obj, dict):
			self.err(where, f"expected an object, got {type(obj).__name__}")
			return False
		missing = [k for k in expected if k not in obj]
		extra = [k for k in obj if k not in expected]
		if missing:
			self.err(where, f"missing keys {missing}")
		if extra:
			self.err(where, f"unexpected keys {extra}")
		return not missing

	def typed(self, where, value, types, st, nullable=True):
		"""Type-check one value; count it when null."""
		types = types if isinstance(types, tuple) else (types,)
		if value is None:
			if not nullable:
				self.err(where, "must not be null")
			else:
				self.nulls[st] = self.nulls.get(st, 0) + 1
			return
		if isinstance(value, bool) and bool not in types:
			self.err(where, f"expected {self.names(types)}, got bool")
		elif not isinstance(value, types):
			self.err(where, f"expected {self.names(types)}, got {type(value).__name__}")

	@staticmethod
	def names(types):
		return "/".join(t.__name__ for t in types)

	def cite(self, where, cite, values):
		"""A cite is a non-empty string. Behind any non-null value it must name a source URL."""
		if not isinstance(cite, str) or not cite.strip():
			self.err(where, "missing cite")
			return
		if any(v is not None for v in values) and "http" not in cite:
			self.err(where, "cite has no URL but the value it supports is not null")

	def enum(self, where, value, allowed):
		if value is not None and value not in allowed:
			self.err(where, f"{value!r} is not one of {sorted(allowed)}")


def check_codes(c, st, codes):
	w = f"{st}.codes"
	if not c.keys(w, codes, ("nec", "ibc", "irc", "ispsc", "mahc")):
		return
	nec = codes["nec"]
	if c.keys(f"{w}.nec", nec, ("edition", "basis", "effective", "amendments", "cite")):
		c.typed(f"{w}.nec.edition", nec["edition"], int, st)
		c.typed(f"{w}.nec.basis", nec["basis"], str, st)
		c.enum(f"{w}.nec.basis", nec["basis"], BASIS)
		c.typed(f"{w}.nec.effective", nec["effective"], str, st)
		if isinstance(nec["effective"], str) and not ISO_DATE.match(nec["effective"]):
			c.err(f"{w}.nec.effective", f"{nec['effective']!r} is not YYYY-MM-DD")
		c.typed(f"{w}.nec.amendments", nec["amendments"], str, st, nullable=False)
		c.cite(f"{w}.nec.cite", nec["cite"], (nec["edition"], nec["basis"], nec["effective"]))
	for code in ("ibc", "irc"):
		d = codes[code]
		if c.keys(f"{w}.{code}", d, ("edition", "basis", "cite")):
			c.typed(f"{w}.{code}.edition", d["edition"], int, st)
			c.typed(f"{w}.{code}.basis", d["basis"], str, st)
			c.enum(f"{w}.{code}.basis", d["basis"], BASIS)
			c.cite(f"{w}.{code}.cite", d["cite"], (d["edition"], d["basis"]))
	isp = codes["ispsc"]
	if c.keys(f"{w}.ispsc", isp, ("adopted", "edition", "basis", "cite")):
		c.typed(f"{w}.ispsc.adopted", isp["adopted"], bool, st)
		c.typed(f"{w}.ispsc.edition", isp["edition"], int, st)
		c.typed(f"{w}.ispsc.basis", isp["basis"], str, st)
		c.enum(f"{w}.ispsc.basis", isp["basis"], BASIS)
		if isp["adopted"] is False and isp["edition"] is not None:
			c.err(f"{w}.ispsc", "edition given but adopted is false")
		c.cite(f"{w}.ispsc.cite", isp["cite"], (isp["adopted"], isp["edition"]))
	mahc = codes["mahc"]
	if c.keys(f"{w}.mahc", mahc, ("status", "cite")):
		c.typed(f"{w}.mahc.status", mahc["status"], str, st)
		c.enum(f"{w}.mahc.status", mahc["status"], MAHC)
		c.cite(f"{w}.mahc.cite", mahc["cite"], (mahc["status"],))


def check_regulation(c, st, reg):
	w = f"{st}.regulation"
	expected = (
		"agency",
		"pool_rule",
		"pool_cite",
		"spa_regulated",
		"iwf_regulated",
		"iwf_rule",
		"iwf_cite",
		"iwf_like_utah",
		"fogger_potable",
		"fogger_cite",
		"plan_review",
		"plan_review_cite",
		"notes",
	)
	if not c.keys(w, reg, expected):
		return
	c.typed(f"{w}.agency", reg["agency"], str, st)
	c.typed(f"{w}.pool_rule", reg["pool_rule"], str, st)
	c.typed(f"{w}.spa_regulated", reg["spa_regulated"], bool, st)
	c.cite(f"{w}.pool_cite", reg["pool_cite"], (reg["agency"], reg["pool_rule"], reg["spa_regulated"]))
	c.typed(f"{w}.iwf_regulated", reg["iwf_regulated"], bool, st)
	c.typed(f"{w}.iwf_rule", reg["iwf_rule"], str, st)
	c.typed(f"{w}.iwf_like_utah", reg["iwf_like_utah"], bool, st, nullable=False)
	c.cite(f"{w}.iwf_cite", reg["iwf_cite"], (reg["iwf_regulated"], reg["iwf_rule"]))
	if reg["iwf_like_utah"] and not reg["iwf_regulated"]:
		c.err(w, "iwf_like_utah is true but iwf_regulated is not")
	c.typed(f"{w}.fogger_potable", reg["fogger_potable"], bool, st)
	c.cite(f"{w}.fogger_cite", reg["fogger_cite"], (reg["fogger_potable"],))
	c.typed(f"{w}.plan_review", reg["plan_review"], str, st)
	c.enum(f"{w}.plan_review", reg["plan_review"], PLAN_REVIEW)
	c.cite(f"{w}.plan_review_cite", reg["plan_review_cite"], (reg["plan_review"],))
	c.typed(f"{w}.notes", reg["notes"], str, st, nullable=False)


def check_climate(c, st, cl):
	w = f"{st}.climate"
	if not c.keys(w, cl, ("design_city", *CLIMATE_NUMS, "winterize", "seismic_default", "cites")):
		return
	c.typed(f"{w}.design_city", cl["design_city"], str, st, nullable=False)
	for k in CLIMATE_NUMS:
		c.typed(f"{w}.{k}", cl[k], NUM, st)
	c.typed(f"{w}.winterize", cl["winterize"], bool, st)
	c.typed(f"{w}.seismic_default", cl["seismic_default"], str, st)
	c.enum(f"{w}.seismic_default", cl["seismic_default"], SDC)
	if c.keys(f"{w}.cites", cl["cites"], CLIMATE_CITES):
		for k in CLIMATE_CITES:
			c.cite(f"{w}.cites.{k}", cl["cites"][k], (cl[k],))
	heat = cl["heating_design_f"]
	if isinstance(heat, NUM) and isinstance(cl["winterize"], bool) and cl["winterize"] != (heat < 28):
		c.err(
			f"{w}.winterize",
			f"must be {heat < 28} for a heating design temperature of {heat} F (rule: below 28 F)",
		)


def check_licensing(c, st, lic):
	w = f"{st}.licensing"
	if not c.keys(w, lic, ("contractor", "electrical", "permits", "cites")):
		return
	for k in ("contractor", "electrical", "permits"):
		c.typed(f"{w}.{k}", lic[k], str, st, nullable=False)
		if isinstance(lic[k], str) and not lic[k].strip():
			c.err(f"{w}.{k}", "empty")
	cites = lic["cites"]
	if not isinstance(cites, list) or not cites:
		c.err(f"{w}.cites", "expected a non-empty list")
	else:
		for i, ci in enumerate(cites):
			c.cite(f"{w}.cites[{i}]", ci, (True,))


def validate(data):
	c = Checker()
	if not c.keys("(top)", data, ("schema", "generated", "jurisdictions")):
		return c
	if data["schema"] != 1:
		c.err("(top).schema", "must be 1")
	if not isinstance(data["generated"], str) or not ISO_DATE.match(data["generated"]):
		c.err("(top).generated", "must be a YYYY-MM-DD string")
	jur = data["jurisdictions"]
	if not isinstance(jur, dict):
		c.err("(top).jurisdictions", "expected an object keyed by USPS code")
		return c
	for st in STATES:
		if st not in jur:
			c.err(st, "state missing")
	for st in jur:
		if st not in STATES:
			c.err(st, "not one of the 48 contiguous states")
	for st in STATES:
		row = jur.get(st)
		if row is None:
			continue
		c.nulls.setdefault(st, 0)
		if not c.keys(st, row, ("name", "codes", "regulation", "climate", "licensing")):
			continue
		c.typed(f"{st}.name", row["name"], str, st, nullable=False)
		check_codes(c, st, row["codes"])
		check_regulation(c, st, row["regulation"])
		check_climate(c, st, row["climate"])
		check_licensing(c, st, row["licensing"])
	ut = (jur.get("UT") or {}).get("climate") or {}
	for k, v in UTAH_ENGINE.items():
		if ut.get(k) != v:
			c.err(f"UT.climate.{k}", f"{ut.get(k)!r} disagrees with the engine default {v}")
	return c


def main(argv):
	path = (
		argv[1]
		if len(argv) > 1
		else os.path.join(os.path.dirname(os.path.abspath(__file__)), "jurisdictions.json")
	)
	with open(path, encoding="utf-8") as fh:
		data = json.load(fh)
	c = validate(data)
	total = sum(c.nulls.values())
	print(f"{len(c.nulls)} states checked, {total} null values")
	print("  " + "  ".join(f"{st}:{n}" for st, n in sorted(c.nulls.items())))
	if c.errors:
		print(f"\n{len(c.errors)} problem(s):")
		for e in c.errors:
			print(f"  {e}")
		return 1
	print("OK")
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv))
