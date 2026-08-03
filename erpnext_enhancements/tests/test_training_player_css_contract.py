"""Does the learner player's stylesheet style the page the scripts actually build?

It did not. Of the 138 ``tr-*`` classes the four player scripts emit, **130 had no
rule anywhere in `player.css`**, and of the 43 the stylesheet defined, 35 were
never emitted. The two halves had been written against different vocabularies:

===========================  ===========================
the scripts emit             the stylesheet styled
===========================  ===========================
``tr-subhead``               ``tr-header``
``tr-view``                  ``tr-main``
``tr-bottom``                ``tr-bar``
``tr-outline-row``           ``tr-outline-item``
``tr-video-meter``           ``tr-coverage``
``tr-video-takeover``        ``tr-checkpoint``
``tr-block-video``           ``tr-block--video``  (one dash vs two)
===========================  ===========================

`player.css` was written early, against the Phase-2a design. The scripts were then
rewritten through video, checkpoints and quiz, and the stylesheet never followed.
The page still *looked* like it had styling, because the eight classes that did
overlap included ``:root`` (so the palette applied) and ``.tr-shell`` — which was
worse than no styling at all, since `.tr-shell` is a grid at >= 900px and the
template and `player.js` each put that class on a different element, nesting the
grid inside one 260px column of itself. The visible result was two narrow columns
of one-word-per-line text.

Nothing catches this at runtime. An unstyled element is not an error; it renders,
just wrongly, and only a human looking at the page can tell. So the check is
static and mechanical: **every class the scripts emit must have a rule.**

Run: python -m unittest erpnext_enhancements.tests.test_training_player_css_contract
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
CSS = APP / "public/css/training/player.css"
JS_DIR = APP / "public/js/training"
TEMPLATE = APP / "www/training.html"

SCRIPTS = ("player.js", "blocks.js", "video.js", "quiz.js")

# Classes built by concatenation — ``el("section", "tr-block tr-block-" + modifier)``
# — so the literal in the source ends in a dash. The suffixes come from the block
# types blocks.js knows about and the tones its callouts use; listed here because a
# regex over the source cannot resolve them.
DYNAMIC_PREFIXES = {
    # el("section", "tr-block tr-block-" + modifier) — the block type.
    "tr-block-": (
        "text", "image", "pdf", "video", "embed", "callout", "file", "divider",
        "unknown", "heading", "body", "caption", "note",
    ),
}

# ``chip(text, tone)`` and ``note(text, tone)`` append their tone to a class, so
# the tones ARE classes. They are read out of the call sites rather than listed
# here: a hardcoded list is only correct until somebody adds a tone, and this test
# was demonstrated to miss exactly that — a new tone in the JS with no rule in the
# CSS passed cleanly, which is the whole failure this module exists to prevent.
TONED_HELPERS = {"chip": "tr-chip-", "note": "tr-block-note-"}

# ``tr-``-prefixed strings that are NOT classes and must never be required to have
# a rule. Both were found by this module's own dangling-prefix check, which is the
# argument for keeping that check strict rather than skipping what it cannot
# resolve.
NOT_CLASSES = {
    "tr-cp-",                   # video.js: input.name for a checkpoint radio group
    "tr-q-text-",               # quiz.js:  element id, used by aria-labelledby
    "tr-quiz-styles",           # quiz.js:  id of the <style> it injects
    "tr-video-structural-css",  # video.js: id of the <style> it injects
    "tr-quiz-submitted",        # quiz.js:  CustomEvent name
    "tr-quiz-retry",            # quiz.js:  CustomEvent name
}

# State and modifier classes are applied with classList.add on an element that
# already carries its base class, so they are styled as `.base.is-state` and do not
# need a bare rule of their own.
STATE_CLASSES = re.compile(r"^(is|has)-")


def _css():
    return CSS.read_text(encoding="utf-8")


def _css_without_comments():
    """Comments stripped.

    The stylesheet's header comment documents a class contract — and documents the
    *wrong* one, naming ``.tr-header``/``.tr-main``/``.tr-bar``, none of which the
    scripts emit. Counting that prose as "styled" would have made this whole module
    pass while the page rendered as two columns of single words.
    """
    return re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)


def _defined():
    return set(re.findall(r"\.(tr-[a-z0-9-]+)", _css_without_comments()))


def _strip_js_comments(src):
    """Comments removed, for the usual reason.

    A comment that names a class is not a class. Leaving them in would let prose
    like "// .tr-coverage is now .tr-video-meter" register as a rendered class and
    quietly satisfy the whole contract.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("//")
    )


def _emitted():
    """Every ``tr-*`` class the scripts and the template put into the DOM.

    Scans *all* string literals rather than a handful of call shapes. The narrower
    version missed ``el("span", "tr-chip" + (tone ? " tr-chip-" + tone : ""))`` —
    the tone half is a separate literal, so six chip classes never registered and
    the check silently under-reported. If a class name can reach the DOM at all it
    has to appear inside quotes somewhere, so quotes are the thing to scan.
    """
    found = {}
    sources = [(name, (JS_DIR / name).read_text(encoding="utf-8")) for name in SCRIPTS]
    sources.append((TEMPLATE.name, TEMPLATE.read_text(encoding="utf-8")))

    for name, src in sources:
        code = _strip_js_comments(src)
        literals = re.findall(r'"([^"\n]*)"', code)
        literals += re.findall(r"'([^'\n]*)'", code)
        # Classes inside a built HTML string. quiz.js writes its score ring as
        # '<circle class="tr-ring-fg" …>' inside a SINGLE-quoted literal, so the
        # class name is not a literal in its own right and tokenising alone misses
        # it — the double-quote pairing lands on the attribute names instead.
        literals += re.findall(r"""class=["']([^"'>]*)["']""", code)
        for literal in literals:
            for token in literal.split():
                if re.fullmatch(r"tr-[a-z0-9-]*", token) and token not in NOT_CLASSES:
                    found.setdefault(token, set()).add(name)
    return found


def _tones():
    """Tone suffixes, read out of the ``chip(...)`` / ``note(...)`` call sites.

    A tone is a bare quoted literal in the call. User-facing text always goes
    through the translator — ``chip(t("Required"), "required")`` — so stripping
    every ``t(...)`` and ``fmt(...)`` first leaves exactly the tones behind. That
    also handles the ternary form, ``chip(cond ? t("A") : t("B"), cond ? "a" : "b")``,
    where both tones survive and both need a rule.
    """
    found = {}
    for name in SCRIPTS:
        code = _strip_js_comments((JS_DIR / name).read_text(encoding="utf-8"))
        for helper, prefix in TONED_HELPERS.items():
            for match in re.finditer(rf"\b{helper}\(", code):
                start = match.end()
                depth, end = 1, start
                while end < len(code) and depth:
                    if code[end] == "(":
                        depth += 1
                    elif code[end] == ")":
                        depth -= 1
                    end += 1
                call = code[start : end - 1]
                # Drop translated/formatted text; what is left is tones.
                call = re.sub(r'\b(?:t|fmt)\(\s*"[^"\n]*"', "", call)
                for tone in re.findall(r'"([a-z][a-z0-9-]*)"', call):
                    found.setdefault(prefix + tone, set()).add(name)
    return found


def _expand(emitted):
    """Resolve concatenated prefixes into the concrete classes they produce."""
    out = {}
    for cls, sources in _tones().items():
        out.setdefault(cls, set()).update(sources)
    for cls, sources in emitted.items():
        if cls in DYNAMIC_PREFIXES:
            for suffix in DYNAMIC_PREFIXES[cls]:
                out.setdefault(cls + suffix, set()).update(sources)
        elif cls in {p for p in TONED_HELPERS.values()}:
            continue  # the bare prefix itself is never a class; _tones() has it
        elif cls.endswith("-"):
            # An unknown concatenation. Fail loudly rather than skip it: a silent
            # skip here is exactly how a whole family of classes goes unstyled.
            out.setdefault(cls, set()).update(sources)
        else:
            out.setdefault(cls, set()).update(sources)
    return out


class TestTheScanWorks(unittest.TestCase):
    """Guards every assertion below from passing because a regex went stale."""

    def test_the_stylesheet_is_found_and_substantial(self):
        self.assertGreater(len(_css()), 2000)
        self.assertGreater(len(_defined()), 30)

    def test_every_script_is_scanned(self):
        for name in SCRIPTS:
            self.assertTrue((JS_DIR / name).exists(), f"{name} missing")

    def test_classes_are_actually_extracted(self):
        emitted = _expand(_emitted())
        self.assertGreater(len(emitted), 100, "the extractor found almost nothing")
        # Spot-check the three that broke the page layout.
        for cls in ("tr-subhead", "tr-view", "tr-bottom"):
            self.assertIn(cls, emitted)

    def test_no_unresolved_concatenated_prefix(self):
        """A class literal ending in `-` is built by concatenation. Every such
        prefix must be listed in DYNAMIC_PREFIXES so its real classes get checked."""
        dangling = sorted(c for c in _expand(_emitted()) if c.endswith("-"))
        self.assertEqual(
            dangling, [], f"unresolved dynamic class prefixes: {dangling}"
        )


class TestEveryEmittedClassIsStyled(unittest.TestCase):
    """The check that matters.

    An unstyled class is not a runtime error. The element renders, wrongly, and
    only somebody looking at the page can tell — which is why this went unnoticed
    through four phases and was reported by a person opening the page, not by any
    test.
    """

    def test_no_class_reaches_the_dom_without_a_rule(self):
        defined = _defined()
        missing = []
        for cls, sources in sorted(_expand(_emitted()).items()):
            if STATE_CLASSES.match(cls):
                continue
            if cls not in defined:
                missing.append(f"{cls} (from {', '.join(sorted(sources))})")
        self.assertEqual(
            missing,
            [],
            f"{len(missing)} class(es) rendered but never styled:\n  " + "\n  ".join(missing),
        )

    def test_the_page_chrome_is_styled(self):
        """Called out separately because these three are the page layout itself —
        when they are unstyled the result is not 'slightly off', it is unreadable."""
        defined = _defined()
        for cls in ("tr-subhead", "tr-view", "tr-bottom"):
            self.assertIn(cls, defined, f".{cls} is the page layout and has no rule")


class TestNoDuplicateShell(unittest.TestCase):
    """`.tr-shell` is a grid at >= 900px, and it was on two nested elements.

    The template puts it on the mount point; `player.js` built a second one inside.
    The inner grid was then laid out inside a single 260px column of the outer,
    which is what produced the columns of one-word-per-line text.
    """

    def test_the_template_owns_the_shell_class(self):
        self.assertIn('class="tr-shell"', TEMPLATE.read_text(encoding="utf-8"))

    def test_the_player_does_not_build_a_second_one(self):
        src = (JS_DIR / "player.js").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("//")
        )
        self.assertNotIn('el("div", "tr-shell")', code)


class TestNoDeadStylesheet(unittest.TestCase):
    """The other direction. Not fatal, but it is the tell.

    35 styled classes were emitted by nothing — the stylesheet was describing a
    page that no longer existed. A rule for an element that is never rendered is
    the strongest available hint that the two halves have drifted, so it is worth
    failing on rather than tolerating.
    """

    # Styled deliberately without ever being emitted by the scanned sources.
    ALLOWED_UNUSED = {
        # Set on <html>/<body> by the template's own inline style block, and used
        # as a theming hook rather than rendered by the scripts.
        "tr-shell",
    }

    def test_no_rule_styles_an_element_that_is_never_rendered(self):
        emitted = set(_expand(_emitted()))
        # A rule may legitimately target a state class or a descendant of an
        # emitted class, so only flag whole classes nothing anywhere produces.
        dead = sorted(
            cls
            for cls in _defined()
            if cls not in emitted
            and cls not in self.ALLOWED_UNUSED
            and not STATE_CLASSES.match(cls)
        )
        self.assertEqual(
            dead,
            [],
            f"{len(dead)} rule(s) style classes nothing renders:\n  " + "\n  ".join(dead),
        )


class TestHostThemeReset(unittest.TestCase):
    """The page renders inside Frappe's website stylesheet, which colours our tags.

    ``frappe/dist/css/website.bundle.*.css`` declares, among others::

        h1,h2,h3,h4,h5,h6 { color: #171717 }
        a                 { color: #171717 }
        body              { color: #525252 }
        strong            { color: #383838 }

    ``.tr-shell`` sets ``color: var(--tr-text)``, but **an inherited value loses to
    any explicit declaration**, however specific the inheriting selector is. So the
    elements the host colours never received our palette: "Your training" rendered
    as a near-black ``<h1>`` on our dark surface and was invisible until selected.
    The paragraph beside it was legible only because ``.tr-empty`` declares a colour
    of its own.

    This is not theme detection and the dark palette was applying correctly — the
    cascade simply never carried it to those tags. The rules below look like
    redundant boilerplate, which is exactly why they need a test: the obvious
    "cleanup" is to delete them.
    """

    # Every tag Frappe's website bundle gives an explicit colour to.
    HOST_COLOURED = ("h1", "h2", "h3", "p", "strong", "label", "a")

    @staticmethod
    def _colour_rules():
        """``{selector: colour-value}`` for every rule that declares a colour.

        Parsed rather than substring-searched. The first version of these
        assertions checked ``".tr-shell a" in css`` and passed happily against
        ``.tr-shell a-GONE`` — three of its four mutations survived. A selector is
        a token, so compare tokens.
        """
        rules = {}
        for selectors, body in re.findall(
            r"([^{}]+)\{([^{}]*)\}", _css_without_comments()
        ):
            match = re.search(r"(?<![-\w])color\s*:\s*([^;}]+)", body)
            if not match:
                continue
            for selector in selectors.split(","):
                rules[selector.strip()] = match.group(1).strip()
        return rules

    def test_the_reset_exists(self):
        self.assertIn("reset against the host", _css())

    def test_every_host_coloured_tag_is_reclaimed(self):
        rules = self._colour_rules()
        missing = [
            tag for tag in self.HOST_COLOURED if f".tr-shell {tag}" not in rules
        ]
        self.assertEqual(
            missing,
            [],
            f"the host stylesheet colours {missing} explicitly and no rule here "
            f"gives .tr-shell {missing} a colour of its own",
        )

    def test_the_reset_uses_the_palette(self):
        """Reclaiming with a literal would fix one scheme and break the other."""
        rules = self._colour_rules()
        for tag in self.HOST_COLOURED:
            value = rules.get(f".tr-shell {tag}", "")
            self.assertIn(
                "var(--tr-",
                value,
                f".tr-shell {tag} is coloured {value!r}, not from the palette",
            )

    def test_it_is_scoped_to_the_player(self):
        """Unscoped tag rules would repaint the surrounding site chrome."""
        code = _css_without_comments()
        for line in code.splitlines():
            stripped = line.strip().rstrip(",")
            if re.fullmatch(r"(h[1-6]|p|a|strong|b|label|small|li)", stripped):
                self.fail(f"bare tag selector {stripped!r} leaks outside .tr-shell")


if __name__ == "__main__":
    unittest.main()
