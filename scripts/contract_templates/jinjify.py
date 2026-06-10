"""Inject Jinja into the converted contract templates at every fill point.

Deterministic, assertion-checked string surgery over the machine-generated
HTML. Helpers available at render time (see project_contract._render_context):
fill(v,w), blank(w), cb(bool), money(v), dt(date), multiline(v,w,lines),
phases (dict by phase_key), service_options (dict by option_key), doc.
"""
import re

BASE = "erpnext_enhancements/templates/contracts/"


class T:
    def __init__(self, fname):
        self.path = BASE + fname
        self.src = open(self.path, encoding="utf-8").read()

    def rep(self, old, new, n=1):
        found = self.src.count(old)
        assert found == n, f"{self.path}: expected {n}x {old[:60]!r}, found {found}"
        self.src = self.src.replace(old, new)

    def rep_first(self, old, new, total):
        """Replace only the first occurrence; assert the total count (the
        later occurrences deliberately stay blank, e.g. the CC card form)."""
        found = self.src.count(old)
        assert found == total, f"{self.path}: expected {total}x {old[:60]!r}, found {found}"
        self.src = self.src.replace(old, new, 1)

    def rerep(self, pattern, new, n=1, flags=re.S):
        matches = list(re.finditer(pattern, self.src, flags))
        assert len(matches) == n, f"{self.path}: regex {pattern[:60]!r} matched {len(matches)} (want {n})"
        self.src = re.sub(pattern, new, self.src, flags=flags)

    def finish(self):
        # leftover blanks (signature blocks, exhibits, condition checklists)
        self.src = self.src.replace("${{ BLANK }}", "{{ money(None) }}")
        self.src = self.src.replace("{{ BLANK }}", "{{ blank(30) }}")
        open(self.path, "w", encoding="utf-8", newline="\n").write(self.src)
        print(f"{self.path}: done, {self.src.count('{{')} jinja tags")


def cell(label, value, bold=True):
    lab = f"<b>{label}</b>" if bold else label
    return (f"<td>{lab}</td><td>{{{{ BLANK }}}}</td>", f"<td>{lab}</td><td>{value}</td>")


# ============================================================ Owner Contract
t = T("owner_contract.html")
t.rep(*cell("Owner Name:", "{{ fill(doc.party_display, 50) }}"))
t.rep(*cell("Owner Address:", "{{ multiline(doc.billing_address, 50, 1) }}"))
t.rep(*cell("Phone:", "{{ fill(doc.contact_phone, 50) }}"))
t.rep(*cell("Email:", "{{ fill(doc.contact_email, 50) }}"))
t.rep(*cell("Project Site Address:", "{{ multiline(doc.site_address, 50, 1) }}"))
t.rep(*cell("Project Name / Description:", "{{ fill(doc.project_title, 50) }}"))
t.rep(*cell("Contract Date:", "{{ dt(doc.contract_date) }}"))
t.rep(
    "<td>[ ] Residential    [ ] Commercial    [ ] Municipal    [ ] Other: ___</td>",
    '<td>{{ cb(doc.property_type=="Residential") }} Residential    '
    '{{ cb(doc.property_type=="Commercial") }} Commercial    '
    '{{ cb(doc.property_type=="Municipal") }} Municipal    '
    '{{ cb(doc.property_type=="Other") }} Other: ___</td>',
)
t.rep(
    "<td>[ ] Outdoor    [ ] Indoor    [ ] Both</td>",
    '<td>{{ cb(doc.feature_location=="Outdoor") }} Outdoor    '
    '{{ cb(doc.feature_location=="Indoor") }} Indoor    '
    '{{ cb(doc.feature_location=="Both") }} Both</td>',
)

# three phase selection tables: checkbox + fee + retainer, scoped per table
# (bold runs are split in the source: <b>PHASE </b><b>1  —</b>...)
for key, marker in (("design", "1"), ("construction", "2"), ("maintenance", "3")):
    pat = (
        r'<table class="ct-table"><tr><td><b>\[ \]</b></td><td><b>PHASE </b><b>'
        + marker
        + r".*?</table>"
    )
    m = list(re.finditer(pat, t.src, re.S))
    assert len(m) == 1, f"phase table {marker}: {len(m)}"
    seg = m[0].group(0)
    new_seg = seg.replace(
        "<td><b>[ ]</b></td>",
        f'<td>{{% set _p = phases.get("{key}") %}}{{{{ cb(_p and _p.included) }}}}</td>',
        1,
    )
    new_seg = new_seg.replace("${{ BLANK }}", "{{ money(_p.fee if _p else None) }}", 1)
    new_seg = new_seg.replace("${{ BLANK }}", "{{ money(_p.retainer if _p else None) }}", 1)
    t.src = t.src.replace(seg, new_seg)

t.rep(
    "(all selected phases):  ${{ BLANK }}",
    "(all selected phases):  {{ money(doc.total_contract_value) }}",
)
t.rep(
    "(sum of selected retainers/deposits):  ${{ BLANK }}",
    "(sum of selected retainers/deposits):  {{ money(doc.total_due_at_signing) }}",
)
# money cells in the design fee table carry a leading $
for label, field in [
    ("Design Retainer (due at signing):", "doc.design_retainer"),
    ("Phase 1 – Concept Design Fee:", "doc.concept_design_fee"),
    ("Phase 2 – Design Development Fee:", "doc.design_development_fee"),
    ("Phase 3 – Construction Documents Fee:", "doc.construction_documents_fee"),
    ("Total Design Fee:", "doc.total_design_fee"),
]:
    t.rep(f"<td><b>{label}</b></td><td>${{{{ BLANK }}}}</td>", f"<td><b>{label}</b></td><td>{{{{ money({field}) }}}}</td>")
for label, field in [
    ("Concept Design:", "doc.concept_days"),
    ("Design Development:", "doc.design_development_days"),
    ("Construction Documents:", "doc.construction_documents_days"),
]:
    t.rerep(
        r"<td><b>" + re.escape(label) + r"</b></td><td>___ calendar days",
        f"<td><b>{label}</b></td><td>{{{{ fill({field}, 4) }}}} calendar days",
    )
# construction payment table -> milestone loop
t.rerep(
    r'<table class="ct-table"><tr><td><b>Construction Deposit \(due at signing\):.*?</table>',
    '<table class="ct-table">'
    "<tr><td><b>Milestone</b></td><td><b>Due Upon</b></td><td><b>%</b></td><td><b>Amount</b></td></tr>"
    "{% for m in doc.milestones %}"
    "<tr><td>{{ m.milestone }}</td><td>{{ fill(m.due_upon, 22) }}</td>"
    '<td>{{ m.percent or "" }}{% if m.percent %}%{% endif %}</td><td>{{ money(m.amount) }}</td></tr>'
    "{% endfor %}"
    "{% if not doc.milestones %}<tr><td>Construction Deposit (due at signing)</td><td>{{ blank(22) }}</td><td>{{ blank(4) }}</td><td>{{ money(None) }}</td></tr>"
    "<tr><td>Progress Payment #1</td><td>{{ blank(22) }}</td><td>{{ blank(4) }}</td><td>{{ money(None) }}</td></tr>"
    "<tr><td>Progress Payment #2</td><td>{{ blank(22) }}</td><td>{{ blank(4) }}</td><td>{{ money(None) }}</td></tr>"
    "<tr><td>Final Payment (due upon Substantial Completion)</td><td>{{ blank(22) }}</td><td>{{ blank(4) }}</td><td>{{ money(None) }}</td></tr>{% endif %}"
    "<tr><td><b>Total Construction Price</b></td><td></td><td></td><td><b>{{ money(doc.milestones_total) }}</b></td></tr>"
    "</table>",
)
t.rerep(
    r"<td><b>Construction Start Date:</b></td><td>Within ___ days",
    "<td><b>Construction Start Date:</b></td><td>Within {{ fill(doc.construction_start_days, 4) }} days",
)
t.rep(*cell("Anticipated Completion Date:", "{{ dt(doc.anticipated_completion_date) }}"))
t.rep("<td><b>Annual Maintenance Fee:</b></td><td>${{ BLANK }}</td>",
      "<td><b>Annual Maintenance Fee:</b></td><td>{{ money(doc.annual_maintenance_fee) }}</td>")
t.rep("<td><b>Maintenance Deposit (due at signing):</b></td><td>${{ BLANK }}</td>",
      "<td><b>Maintenance Deposit (due at signing):</b></td><td>{{ money(doc.maintenance_deposit) }}</td>")
t.rep(
    "<td>[ ] Per visit  [ ] Monthly  [ ] Quarterly  [ ] Annually</td>",
    '<td>{{ cb(doc.invoicing_frequency=="Per Visit") }} Per visit  '
    '{{ cb(doc.invoicing_frequency=="Monthly") }} Monthly  '
    '{{ cb(doc.invoicing_frequency=="Quarterly") }} Quarterly  '
    '{{ cb(doc.invoicing_frequency=="Annually") }} Annually</td>',
)
t.finish()

# ============================================================ Statement of Work
t = T("statement_of_work.html")
t.rep("<td>SF-SOW-{{ BLANK }}</td>", "<td><b>{{ doc.name }}</b></td>")
t.rep(*cell("SOW Date:", "{{ dt(doc.contract_date) }}"))
t.rep(*cell("MSA Effective Date:", "{{ dt(doc.msa_effective_date) }}"))
t.rep(*cell("Project Name:", "{{ fill(doc.project_title, 40) }}"))
t.rep("<td>SF-PROJ-{{ BLANK }}</td>", "<td>{{ fill(doc.project, 24) }}</td>")
t.rep(*cell("Project Site Address:", "{{ multiline(doc.site_address, 45, 1) }}"))
t.rep(*cell("Company Contact:", "{{ fill(doc.company_contact, 45) }}"))
t.rep(*cell("Subcontractor:", "{{ fill(doc.party_display, 45) }}"))
t.rep(*cell("Subcontractor Address:", "{{ multiline(doc.billing_address, 45, 1) }}"))
t.rep(*cell("Subcontractor Contact:", "{{ fill(doc.contact_person, 45) }}"))
t.rep(*cell("Subcontractor License #:", "{{ fill(doc.subcontractor_license, 45) }}"))
t.rep(
    "<p>1.1  Project Description:</p>\n<p>{{ BLANK }}</p>\n<p>{{ BLANK }}</p>\n<p>{{ BLANK }}</p>",
    "<p>1.1  Project Description:</p>\n<p>{{ multiline(doc.project_description, 90, 3) }}</p>",
)
# 2.2 Detailed Scope Description: the rich-text scope_of_work field, else writing lines
_six = "\n".join("<p>{{ BLANK }}</p>" for _ in range(6))
t.rep(
    "Attach additional pages or reference drawings as needed.</p>\n" + _six,
    "Attach additional pages or reference drawings as needed.</p>\n"
    "{% if doc.scope_of_work %}<div>{{ doc.scope_of_work }}</div>"
    "{% else %}" + _six.replace("{{ BLANK }}", "{{ blank(30) }}") + "{% endif %}",
)
t.rep(*cell("Mobilization / Start Date:", "{{ dt(doc.mobilization_date) }}"))
t.rep(*cell("Substantial Completion Date:", "{{ dt(doc.substantial_completion_date) }}"))
t.rep(*cell("Final Completion Date:", "{{ dt(doc.final_completion_date) }}"))
t.rerep(
    r"<td><b>Working Hours:</b></td><td>Mon[^<]*</td>",
    "<td><b>Working Hours:</b></td><td>{{ fill(doc.working_hours, 45) }}</td>",
)
t.rep(*cell("Site Access Notes:", "{{ multiline(doc.site_access_notes, 45, 1) }}"))
# compensation milestones -> loop
t.rerep(
    r'<table class="ct-table"><tr><td><b>Milestone / Phase</b>.*?</table>',
    '<table class="ct-table">'
    "<tr><td><b>Milestone / Phase</b></td><td><b>Description</b></td><td><b>Due Upon</b></td><td><b>Amount</b></td></tr>"
    "{% for m in doc.milestones %}"
    "<tr><td>{{ m.milestone }}</td><td>{{ fill(m.description, 24) }}</td><td>{{ fill(m.due_upon, 18) }}</td><td>{{ money(m.amount) }}</td></tr>"
    "{% endfor %}"
    "{% if not doc.milestones %}"
    "<tr><td>Mobilization Payment</td><td>Upon execution of SOW + mobilization</td><td>{{ blank(18) }}</td><td>{{ money(None) }}</td></tr>"
    "<tr><td>Progress Payment #1</td><td>{{ blank(24) }}</td><td>{{ blank(18) }}</td><td>{{ money(None) }}</td></tr>"
    "<tr><td>Substantial Completion</td><td>Upon Company acceptance</td><td>{{ blank(18) }}</td><td>{{ money(None) }}</td></tr>"
    "<tr><td>Final Payment / Retention</td><td>Upon final completion + lien waiver</td><td>{{ blank(18) }}</td><td>{{ money(None) }}</td></tr>"
    "{% endif %}"
    "<tr><td><b>TOTAL</b></td><td></td><td></td><td><b>{{ money(doc.milestones_total) }}</b></td></tr>"
    "</table>",
)
t.rep("<td><b>Journeyman / Lead Rate:</b></td><td>${{ BLANK }} / hr</td>",
      "<td><b>Journeyman / Lead Rate:</b></td><td>{{ money(doc.rate_journeyman) }} / hr</td>")
t.rep("<td><b>Apprentice / Helper Rate:</b></td><td>${{ BLANK }} / hr</td>",
      "<td><b>Apprentice / Helper Rate:</b></td><td>{{ money(doc.rate_apprentice) }} / hr</td>")
t.rerep(
    r"<td><b>Equipment Rate:</b></td><td>\$\{\{ BLANK \}\} / hr \(specify: \{\{ BLANK \}\}\)</td>",
    "<td><b>Equipment Rate:</b></td><td>{{ money(doc.rate_equipment) }} / hr (specify: {{ fill(doc.equipment_rate_note, 20) }})</td>",
)
t.rep("<td><b>Materials:</b></td><td>Cost + ___% markup</td>",
      "<td><b>Materials:</b></td><td>Cost + {{ fill(doc.materials_markup_percent, 4) }}% markup</td>")
t.rep("<td><b>Not-to-Exceed Amount:</b></td><td>${{ BLANK }}</td>",
      "<td><b>Not-to-Exceed Amount:</b></td><td>{{ money(doc.not_to_exceed) }}</td>")
t.rep(*cell("Name:", "{{ fill(doc.supervisor_name, 45) }}"))
t.rep(*cell("Phone:", "{{ fill(doc.supervisor_phone, 45) }}"))
t.rep(*cell("Email:", "{{ fill(doc.supervisor_email, 45) }}"))
t.rep(
    "<td><b>License / Certification (if </b><b>req&#x27;d</b><b>):</b></td><td>{{ BLANK }}</td>",
    "<td><b>License / Certification (if req&#x27;d):</b></td><td>{{ fill(doc.supervisor_license, 45) }}</td>",
)
t.finish()

# ============================================================ Rental Agreement
t = T("rental_agreement.html")
t.rep(*cell("Renter Name:", "{{ fill(doc.party_display, 50) }}"))
t.rep(*cell("Renter Address:", "{{ multiline(doc.billing_address, 50, 1) }}"))
t.rep(*cell("Phone:", "{{ fill(doc.contact_phone, 50) }}"))
t.rep(*cell("Email:", "{{ fill(doc.contact_email, 50) }}"))
t.rep(*cell("Rental Site Address:", "{{ multiline(doc.site_address, 50, 1) }}"))
t.rep(*cell("Rental Start Date:", "{{ dt(doc.rental_start_date) }}"))
t.rep(*cell("Rental End Date:", "{{ dt(doc.rental_end_date) }}"))
t.rep(*cell("Agreement Date:", "{{ dt(doc.contract_date) }}"))
# equipment items 1-3 -> loop (keep three blank pairs when empty)
t.rerep(
    r"<tr><td><b>Item 1 – Description:.*?(?=<tr><td><b>Additional Equipment:)",
    "{% for item in doc.equipment_items %}"
    "<tr><td><b>Item {{ loop.index }} – Description:</b></td><td>{{ fill(item.description, 45) }}</td></tr>"
    "<tr><td><b>Item {{ loop.index }} – Serial / ID #:</b></td><td>{{ fill(item.serial_id, 45) }}</td></tr>"
    "{% endfor %}"
    "{% if not doc.equipment_items %}"
    "{% for i in [1, 2, 3] %}"
    "<tr><td><b>Item {{ i }} – Description:</b></td><td>{{ blank(45) }}</td></tr>"
    "<tr><td><b>Item {{ i }} – Serial / ID #:</b></td><td>{{ blank(45) }}</td></tr>"
    "{% endfor %}"
    "{% endif %}",
)
for label, field in [
    ("Base Rental Fee:", "doc.base_rental_fee"),
    ("Delivery &amp; Setup Fee:", "doc.delivery_setup_fee"),
    ("Pickup &amp; Removal Fee:", "doc.pickup_removal_fee"),
    ("Water Treatment / Chemicals:", "doc.chemicals_fee"),
    ("Total Rental Amount:", "doc.total_rental_amount"),
    ("Security Deposit:", "doc.security_deposit"),
    ("Total Due at Signing:", "doc.total_due_at_signing"),
]:
    t.rep(f"<td><b>{label}</b></td><td>${{{{ BLANK }}}}</td>", f"<td><b>{label}</b></td><td>{{{{ money({field}) }}}}</td>")
t.rep("<td><b>Other ({{ BLANK }}):</b></td><td>${{ BLANK }}</td>",
      "<td><b>Other ({{ fill(doc.other_fee_label, 16) }}):</b></td><td>{{ money(doc.other_fee) }}</td>")
t.finish()

# ============================================================ Maintenance Agreement
t = T("maintenance_services_agreement.html")
t.rep(*cell("Client Name:", "{{ fill(doc.party_display, 50) }}"))
# "Billing Address" appears again inside the card-authorization form, where it
# must remain a blank line — only the client-info occurrence is auto-filled.
t.rep_first(
    "<td><b>Billing Address:</b></td><td>{{ BLANK }}</td>",
    "<td><b>Billing Address:</b></td><td>{{ multiline(doc.billing_address, 50, 1) }}</td>",
    total=2,
)
t.rep(*cell("Phone:", "{{ fill(doc.contact_phone, 50) }}"))
t.rep(*cell("Email:", "{{ fill(doc.contact_email, 50) }}"))
t.rep(*cell("Service Site Address:", "{{ multiline(doc.site_address, 50, 1) }}"))
t.rep(
    "<td>[ ] Residential    [ ] Commercial    [ ] Other: {{ BLANK }}</td>",
    '<td>{{ cb(doc.property_type=="Residential") }} Residential    '
    '{{ cb(doc.property_type=="Commercial") }} Commercial    '
    '{{ cb(doc.property_type in ("Municipal", "Other")) }} Other: {{ blank(14) }}</td>',
)
t.rep(
    "<td>[ ] Outdoor    [ ] Indoor    [ ] Both</td>",
    '<td>{{ cb(doc.feature_location=="Outdoor") }} Outdoor    '
    '{{ cb(doc.feature_location=="Indoor") }} Indoor    '
    '{{ cb(doc.feature_location=="Both") }} Both</td>',
)
t.rep(*cell("Feature Description:", "{{ fill(doc.feature_description, 50) }}"))
t.rep(*cell("Agreement Start Date:", "{{ dt(doc.agreement_start_date) }}"))
t.rep(*cell("Gate Code / Entry Code:", "{{ fill(doc.gate_code, 50) }}"))
t.rep(*cell("Key Location:", "{{ fill(doc.key_location, 50) }}"))
# service plan option lines
t.rep(
    "<p>  [ ]  Standard Maintenance Plan  –  ${{ BLANK }} per visit  /  {{ BLANK }} visits per month/year</p>",
    '<p>{% set _o = service_options.get("standard") %}  {{ cb(_o and _o.included) }}  Standard Maintenance Plan  –  '
    "{{ money(_o.price if _o else None) }} per visit  /  {{ fill(_o.unit if _o else None, 14) }}</p>",
)
t.rep(
    "<p>  [ ]  Seasonal Startup (Spring)  –  ${{ BLANK }} per event</p>",
    '<p>{% set _o = service_options.get("startup") %}  {{ cb(_o and _o.included) }}  Seasonal Startup (Spring)  –  '
    "{{ money(_o.price if _o else None) }} per event</p>",
)
t.rep(
    "<p>  [ ]  Winterization (Fall)  –  ${{ BLANK }} per event</p>",
    '<p>{% set _o = service_options.get("winterization") %}  {{ cb(_o and _o.included) }}  Winterization (Fall)  –  '
    "{{ money(_o.price if _o else None) }} per event</p>",
)
t.rep(
    "<p>  [ ]  Seasonal Startup + Winterization Package  –  ${{ BLANK }} per year</p>",
    '<p>{% set _o = service_options.get("package") %}  {{ cb(_o and _o.included) }}  Seasonal Startup + Winterization Package  –  '
    "{{ money(_o.price if _o else None) }} per year</p>",
)
t.rep(
    "<td>[ ] Weekly  [ ] Bi-Weekly  [ ] Monthly  [ ] Quarterly  [ ] Custom</td>",
    '<td>{{ cb(doc.visit_frequency=="Weekly") }} Weekly  '
    '{{ cb(doc.visit_frequency=="Bi-Weekly") }} Bi-Weekly  '
    '{{ cb(doc.visit_frequency=="Monthly") }} Monthly  '
    '{{ cb(doc.visit_frequency=="Quarterly") }} Quarterly  '
    '{{ cb(doc.visit_frequency=="Custom") }} Custom</td>',
)
t.rep(*cell("Preferred Visit Day(s):", "{{ fill(doc.preferred_days, 45) }}"))
t.rep(*cell("Preferred Time Window:", "{{ fill(doc.preferred_time, 45) }}"))
t.rep(*cell("Spring Startup (target month):", "{{ fill(doc.startup_month, 45) }}"))
t.rep(*cell("Winterization (target month):", "{{ fill(doc.winterization_month, 45) }}"))
# credit-card authorization: optional per payment_section_choice, never auto-filled
cc_start = t.src.find('<table class="ct-table"><tr><td><b>Cardholder Name:')
assert cc_start != -1
cc_end_marker = "Last 4 digits confirmed:"
cc_end = t.src.find("</table>", t.src.find(cc_end_marker))
assert cc_end != -1
cc_end += len("</table>")
cc_block = t.src[cc_start:cc_end]
t.src = t.src[:cc_start] + (
    '{% if doc.payment_section_choice in ("Payment Link", "Both") %}'
    '<p><b>Preferred payment setup:</b> Client will receive a secure payment link '
    "(processed by our payment provider) by text or email to store a card on file and "
    "authorize charges per Section 4. No card details are written on or stored with this "
    "Agreement. A QR code for the same link may be presented at signing.</p>"
    "{% endif %}"
    '{% if doc.payment_section_choice in ("Manual Card Authorization", "Both") %}'
    + cc_block
    + "{% endif %}"
) + t.src[cc_end:]
t.finish()

# ============================================================ Master Subcontractor Agreement
t = T("master_subcontractor_agreement.html")
t.rep(
    "<table class=\"ct-table\"><tr><td><b>[ ]</b></td><td><b>TIER 1",
    '<table class="ct-table"><tr><td>{{ cb(doc.msa_tier and "Tier 1" in doc.msa_tier) }}</td><td><b>TIER 1',
)
t.rep(
    "<table class=\"ct-table\"><tr><td><b>[ ]</b></td><td><b>TIER 2",
    '<table class="ct-table"><tr><td>{{ cb(doc.msa_tier and "Tier 2" in doc.msa_tier) }}</td><td><b>TIER 2',
)
t.rep(
    "<p>{{ BLANK }}, an individual / entity with its principal place of business at {{ BLANK }} (&quot;Subcontractor&quot;).</p>",
    '<p><b>{{ fill(doc.party_display, 55) }}</b>, an individual / entity with its principal place of '
    'business at {{ fill((doc.billing_address or "").replace("\\n", ", "), 55) }} (&quot;Subcontractor&quot;).</p>',
)
t.finish()


# ============================================================ Nondisclosure Agreement (DOC-0033, retained)
t = T("nondisclosure_agreement.html")
# Office address update (user-directed, Jun 10 2026): the retained original
# predates the move; all agreements now carry the current address.
t.rep(
    "3176 South 400 East, Bountiful, Utah  84010",
    "85 W 300 S, Bountiful, UT 84010",
)
t.rep(
    "is entered this ___ day of _____, 20__ (“Effective Date”)",
    'is entered this {% if doc.contract_date %}{{ frappe.utils.formatdate(doc.contract_date, "d") }}'
    "{% else %}___{% endif %} day of "
    '{% if doc.contract_date %}{{ frappe.utils.formatdate(doc.contract_date, "MMMM") }}'
    "{% else %}_____{% endif %}, "
    '{% if doc.contract_date %}{{ frappe.utils.formatdate(doc.contract_date, "yyyy") }}'
    "{% else %}20__{% endif %} (“Effective Date”)",
)
t.rep(
    "[COMPANY]. (the“[Company]”), a [State] [Entity] with its principal place of business at [ADDRESS].",
    "<b>{{ fill(doc.party_display, 40) }}</b> (the “Company”), a {{ blank(22) }} with its "
    'principal place of business at {{ fill((doc.billing_address or "").replace("\n", ", "), 45) }}.',
)
t.rep(
    "a [proposed business relationship] between",
    "a {% if doc.nda_purpose %}{{ fill(doc.nda_purpose, 34) }}{% else %}proposed business relationship{% endif %} between",
)
t.rep(
    "<td>[COMPANY].<br>By:{{ BLANK }}</td>",
    "<td><b>{{ fill(doc.party_display, 30) }}</b><br>By:{{ BLANK }}</td>",
)
t.finish()

# ============================================================ Architect Agreement (DOC-0101, retained)
t = T("architect_agreement.html")
# Office address update (user-directed, Jun 10 2026) - both notice-address
# blocks (agreement + embedded SOW signature pages).
t.rep(
    "3176 S 400 E<br>Bountiful, UT 84010",
    "85 W 300 S<br>Bountiful, UT 84010",
    n=2,
)
# page-1 header table (full-table pattern only matches the 2-row header, not
# the embedded SOW table that begins with the same rows)
t.rep(
    '<table class="ct-table"><tr><td><b>Architect:</b></td><td></td></tr>'
    "<tr><td><b>Effective Date:</b></td><td></td></tr></table>",
    '<table class="ct-table"><tr><td><b>Architect:</b></td><td>{{ fill(doc.party_display, 45) }}</td></tr>'
    "<tr><td><b>Effective Date:</b></td><td>{{ dt(doc.contract_date) }}</td></tr></table>",
)
# embedded SOW header rows (the only remaining occurrence after the rep above)
t.rep(
    "<tr><td><b>Architect:</b></td><td></td></tr><tr><td><b>Effective Date:</b></td><td></td></tr>",
    "<tr><td><b>Architect:</b></td><td>{{ fill(doc.party_display, 45) }}</td></tr>"
    "<tr><td><b>Effective Date:</b></td><td>{{ dt(doc.contract_date) }}</td></tr>",
)
t.rep(
    "<tr><td><b>Under Services Agreement Dated:</b></td><td></td></tr><tr><td><b>SOW No.</b></td><td></td></tr>",
    "<tr><td><b>Under Services Agreement Dated:</b></td><td>{{ dt(doc.contract_date) }}</td></tr>"
    "<tr><td><b>SOW No.</b></td><td>{{ doc.name }}</td></tr>",
)
t.rep(
    "WHEREAS, Architect has entered into an agreement with {{ BLANK }} (“<u>Owner</u>”) dated "
    "{{ BLANK }} (“<u>Prime Agreement</u>”) to provide professional services in connection with "
    "{{ BLANK }} “<u>Project</u>”)",
    "WHEREAS, Architect has entered into an agreement with <b>{{ fill(doc.architect_owner, 40) }}</b> "
    "(“<u>Owner</u>”) dated {{ dt(doc.architect_owner_agreement_date) }} "
    "(“<u>Prime Agreement</u>”) to provide professional services in connection with "
    "{{ fill(doc.project_title, 40) }} “<u>Project</u>”)",
)
t.rep(
    "<p><b>Description of </b><b>Services/Project to be Provided by Sapphire</b><b>:</b></p>",
    "<p><b>Description of </b><b>Services/Project to be Provided by Sapphire</b><b>:</b></p>"
    "{% if doc.scope_of_work %}<div>{{ doc.scope_of_work }}</div>"
    "{% else %}<p>{{ blank(90) }}</p><p>{{ blank(90) }}</p>{% endif %}",
)
t.finish()

# ============================================================ Employee-Contractor Agreement (DOC-0137, retained)
t = T("employee_contractor_agreement.html")
t.rep(
    "<p>{{ BLANK }}\t\t{{ BLANK }}</p>\n<p>Name\t\t\t\t\t\t\t\t\tDate</p>",
    "<p>{{ fill(doc.party_display, 35) }}\t\t{{ blank(18) }}</p>\n<p>Name\t\t\t\t\t\t\t\t\tDate</p>",
)
t.finish()
