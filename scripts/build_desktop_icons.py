#!/usr/bin/env python3
"""Regenerate the Desk home-grid tile artwork in ``erpnext_enhancements/public/desktop_icons/``.

Dev-only, and its OUTPUT is what ships -- CI never runs this, and prod never sees it.
Run it when you add a tile to ``setup/desktop_icon_map.py`` or change a glyph, then
commit the SVGs it writes. ``tests/test_desktop_icons.py`` is what keeps the committed
artwork honest afterwards.

Each tile is a 28x28 square built the same way ERPNext builds its own
(``erpnext/public/icons/desktop_icons/solid/organization.svg``): one squircle path in
the family colour, then a lucide glyph stroked in white on top. Lucide draws on a
24x24 grid at stroke-width 2, so the glyph is inset to 20x20 -- ``scale(0.8333)`` --
and stroked at 2.4 to land back on 2 once scaled.

Glyphs come from Frappe's bundled lucide sprite, read out of the sibling ``frappe``
checkout at ``origin/version-16``. Reading its WORKING TREE would be wrong: that tree
is on ``develop`` (v17) and its sprite is a different lucide release with different
names -- see CLAUDE.md. A glyph the sprite does not have is a hard error, never a
silently empty tile.

Usage, from the repo root::

  python scripts/build_desktop_icons.py
  python scripts/build_desktop_icons.py --frappe /path/to/frappe
  python scripts/build_desktop_icons.py --sprite /path/to/icons.svg
  python scripts/build_desktop_icons.py --check

Re-running with no map change must leave the tree clean; ``--check`` asserts exactly
that, and is the cheap way to prove the committed SVGs came from the committed map.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from erpnext_enhancements.setup.desktop_icon_map import ASSET_SUBDIR, TILES

SPRITE_PATH = "frappe/public/icons/lucide/icons.svg"
SPRITE_REF = "origin/version-16"

# The squircle, lifted verbatim from ERPNext's solid/organization.svg so our tiles sit
# on exactly the same silhouette as the core ones beside them in the grid.
SQUIRCLE = (
	"M20 0H8C3.58172 0 0 3.58172 0 8V20C0 24.4183 3.58172 28 8 28H20C24.4183 28 28 "
	"24.4183 28 20V8C28 3.58172 24.4183 0 20 0Z"
)

# Lucide's 24x24 box, inset to a 20x20 glyph area with a 4px margin on the 28x28 tile.
GLYPH_TRANSFORM = "translate(4 4) scale(0.8333)"
GLYPH_STROKE_WIDTH = "2.4"

SHAPES = ("path", "circle", "rect", "line", "polyline", "polygon", "ellipse")
SHAPE_RE = re.compile(r"<(?:" + "|".join(SHAPES) + r")\b[^>]*/>")


def find_frappe(explicit):
	"""Locate the sibling frappe checkout.

	Walks up from this repo so it works from the main clone and from a git worktree
	under ``.claude/worktrees/`` alike, where the sibling is several levels further up.
	"""
	if explicit:
		path = pathlib.Path(explicit).resolve()
		if not (path / ".git").exists():
			sys.exit("not a git checkout: " + str(path))
		return path

	for ancestor in [REPO, *REPO.parents]:
		candidate = ancestor / "frappe"
		if (candidate / SPRITE_PATH).exists() or (candidate / ".git").exists():
			return candidate
	sys.exit("could not find a sibling `frappe` checkout; pass --frappe PATH or --sprite PATH")


def read_sprite(frappe):
	"""Return the v16 lucide sprite as text, from git rather than the working tree."""
	proc = subprocess.run(
		["git", "-C", str(frappe), "show", SPRITE_REF + ":" + SPRITE_PATH],
		capture_output=True,
		text=True,
		encoding="utf-8",
	)
	if proc.returncode != 0:
		sys.exit(
			f"could not read {SPRITE_REF}:{SPRITE_PATH} from {frappe}\n"
			f"  (fetch it first: git -C {frappe} fetch origin version-16)\n"
			f"{proc.stderr.strip()}"
		)
	return proc.stdout


def extract_glyph(sprite, name):
	"""Return the shape elements of lucide symbol ``name``, or exit if it is absent."""
	pattern = r'<symbol[^>]*\bid="icon-' + re.escape(name) + r'"[^>]*>(.*?)</symbol>'
	match = re.search(pattern, sprite, re.S)
	if not match:
		sys.exit("lucide sprite (" + SPRITE_REF + ") has no icon named " + repr(name))

	inner = match.group(1)
	shapes = SHAPE_RE.findall(inner)
	if not shapes:
		sys.exit("lucide icon " + repr(name) + " yielded no drawable shapes")

	# Nothing may be dropped silently: what we keep must be all there was.
	leftover = SHAPE_RE.sub("", inner).strip()
	if leftover:
		sys.exit(
			"lucide icon " + repr(name) + " has markup this script does not handle: "
			+ repr(leftover)
		)

	return shapes


def render(glyph, colour, sprite):
	"""Return the finished 28x28 tile SVG for one glyph and colour."""
	shapes = extract_glyph(sprite, glyph)
	body = "\n".join("\t\t" + shape for shape in shapes)
	return (
		'<svg width="28" height="28" viewBox="0 0 28 28" fill="none"'
		' xmlns="http://www.w3.org/2000/svg">\n'
		'\t<path d="' + SQUIRCLE + '" fill="' + colour + '"/>\n'
		'\t<g transform="' + GLYPH_TRANSFORM + '" fill="none" stroke="#fff"'
		' stroke-width="' + GLYPH_STROKE_WIDTH + '" stroke-linecap="round"'
		' stroke-linejoin="round">\n'
		+ body + "\n"
		"\t</g>\n"
		"</svg>\n"
	)


def main():
	parser = argparse.ArgumentParser(description="Regenerate Desk tile artwork.")
	parser.add_argument("--frappe", help="path to the frappe checkout to read the sprite from")
	parser.add_argument("--sprite", help="path to a lucide icons.svg, bypassing git")
	parser.add_argument(
		"--check",
		action="store_true",
		help="do not write; exit 1 if any committed tile differs from what this would emit",
	)
	args = parser.parse_args()

	if args.sprite:
		sprite = pathlib.Path(args.sprite).read_text(encoding="utf-8")
	else:
		sprite = read_sprite(find_frappe(args.frappe))

	out_dir = REPO / "erpnext_enhancements" / ASSET_SUBDIR
	out_dir.mkdir(parents=True, exist_ok=True)

	expected = {}
	for slug, glyph, colour in TILES.values():
		expected[slug + ".svg"] = render(glyph, colour, sprite)

	stale = []
	written = 0
	for filename, content in sorted(expected.items()):
		target = out_dir / filename
		current = target.read_text(encoding="utf-8") if target.exists() else None
		if current == content:
			continue
		if args.check:
			stale.append(filename)
		else:
			target.write_text(content, encoding="utf-8", newline="\n")
			written += 1

	orphans = sorted(p.name for p in out_dir.glob("*.svg") if p.name not in expected)
	if orphans:
		if args.check:
			stale.extend(orphans)
		else:
			for name in orphans:
				(out_dir / name).unlink()
			print("removed " + str(len(orphans)) + " orphaned tile(s): " + ", ".join(orphans))

	if args.check:
		if stale:
			print("stale or orphaned tiles: " + ", ".join(sorted(set(stale))), file=sys.stderr)
			print("run: python scripts/build_desktop_icons.py", file=sys.stderr)
			return 1
		print(str(len(expected)) + " tiles up to date")
		return 0

	print(
		str(len(expected)) + " tiles in " + str(out_dir.relative_to(REPO))
		+ " (" + str(written) + " written)"
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
