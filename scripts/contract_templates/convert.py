"""Step 1 of the contract-template pipeline: docx -> raw HTML.

Converts Brian's agreement .docx files into ordered, clean HTML under
erpnext_enhancements/templates/contracts/ — paragraphs and tables interleaved
in body order, headings preserved, bold/italic kept, and every run of 6+
underscores normalized to a literal ``{{ BLANK }}`` token for step 2
(jinjify.py) to target. Run both steps whenever the source agreements are
revised; see README.md in this folder.

Usage:  python scripts/contract_templates/convert.py <folder-with-docx-files>
"""

import html
import os
import re
import sys

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

OUT_DIR = "erpnext_enhancements/templates/contracts"

# Revised suite (Apr 2026) + the three retained originals (per the Contract
# Comparison Report: DOC-0033/0101/0137 have no replacement in the revised
# suite and stay in active use). Files not present in the given folder are
# skipped, so the script can be run per-folder.
DOCS = {
	"01_Master_Subcontractor_Agreement.docx": "master_subcontractor_agreement.html",
	"01b_Statement_of_Work_Template.docx": "statement_of_work.html",
	"03_Owner_Contract.docx": "owner_contract.html",
	"04_Rental_Agreement.docx": "rental_agreement.html",
	"05_Maintenance_Services_Agreement.docx": "maintenance_services_agreement.html",
	"DOC-0033 General Nondisclosure Agreement.docx": "nondisclosure_agreement.html",
	"DOC-0101 Architect Agreement.docx": "architect_agreement.html",
	"DOC-0137 Employee-Contractor Agreement.docx": "employee_contractor_agreement.html",
}


def iter_blocks(parent):
	for child in parent.element.body.iterchildren():
		if child.tag == qn("w:p"):
			yield Paragraph(child, parent)
		elif child.tag == qn("w:tbl"):
			yield Table(child, parent)


def para_html(p):
	style = (p.style.name or "").lower()
	text = ""
	for run in p.runs:
		t = html.escape(run.text)
		if not t:
			continue
		if run.bold and run.italic:
			t = f"<b><i>{t}</i></b>"
		elif run.bold:
			t = f"<b>{t}</b>"
		elif run.italic:
			t = f"<i>{t}</i>"
		if run.underline and "_" not in run.text:
			t = f"<u>{t}</u>"
		text += t
	if not text.strip():
		return ""
	if "heading 1" in style or "title" in style:
		return f"<h1>{text}</h1>"
	if "heading 2" in style:
		return f"<h2>{text}</h2>"
	if "heading 3" in style:
		return f"<h3>{text}</h3>"
	plain = p.text.strip()
	if re.match(r"^\d+\.\s+[A-Z][A-Z &/–—-]+$", plain) and len(plain) < 70:
		return f"<h2>{text}</h2>"
	if plain.isupper() and len(plain) < 60 and len(plain) > 3:
		return f"<h3>{text}</h3>"
	return f"<p>{text}</p>"


def cell_html(cell):
	parts = []
	for p in cell.paragraphs:
		t = ""
		for run in p.runs:
			x = html.escape(run.text)
			if run.bold:
				x = f"<b>{x}</b>"
			t += x
		if t.strip():
			parts.append(t)
	return "<br>".join(parts)


def table_html(t):
	rows = []
	for row in t.rows:
		cells, seen = [], set()
		for c in row.cells:
			if id(c._tc) in seen:
				continue
			seen.add(id(c._tc))
			span = (
				int(c._tc.tcPr.gridSpan.val)
				if c._tc.tcPr is not None and c._tc.tcPr.gridSpan is not None
				else 1
			)
			attr = f' colspan="{span}"' if span > 1 else ""
			cells.append(f"<td{attr}>{cell_html(c)}</td>")
		rows.append("<tr>" + "".join(cells) + "</tr>")
	return '<table class="ct-table">' + "".join(rows) + "</table>"


def main():
	base = sys.argv[1]
	for src, dst in DOCS.items():
		if not os.path.exists(os.path.join(base, src)):
			print(f"skip (not in folder): {src}")
			continue
		d = docx.Document(os.path.join(base, src))
		parts = []
		for block in iter_blocks(d):
			if isinstance(block, Paragraph):
				h = para_html(block)
				if h:
					parts.append(h)
			else:
				parts.append(table_html(block))
		out = "\n".join(parts)
		out = re.sub(r"_{6,}", "{{ BLANK }}", out)
		with open(os.path.join(OUT_DIR, dst), "w", encoding="utf-8", newline="\n") as f:
			f.write(out + "\n")
		print(f"regenerated {dst}")


if __name__ == "__main__":
	main()
