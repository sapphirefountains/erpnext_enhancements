"""The accuracy rings must be *visible*, not merely present (v1.486.0).

Reported as "the accuracy rings aren't working at all". They were working: the
toggle wired correctly, the layer switched, the circles were constructed with real
radii (live fixes run 4.5-29.5 m). They were drawn at Leaflet's original
`weight: 1, opacity: 0.35, fillOpacity: 0.06` -- values the Google port carried
over faithfully, and which had been tuned against **light** OSM tiles.

On the dark Google basemap that is a 6% fill and a 1px 35%-opacity stroke of a
mid-tone palette colour: in practice, nothing. The toggle looked dead because what
it switched on could not be seen.

Two things are pinned here, because the failure was invisible in both directions:

* the ring styling stays above a legibility floor, so a future tweak cannot quietly
  return it to "present but unseeable";
* the toggle still actually gates the layer, so raising the opacity does not turn a
  diagnostic overlay into something permanently on top of the trail.

Run: python -m unittest erpnext_enhancements.tests.test_timeline_accuracy_rings
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PAGE_JS = APP / "workforce/page/location_timeline/location_timeline.js"

#: Below these the ring is technically drawn and practically invisible on a dark
#: basemap. Not a design opinion -- the numbers under them are the ones that were
#: reported as broken.
MIN_STROKE_OPACITY = 0.6
MIN_STROKE_WEIGHT = 1.5
MIN_FILL_OPACITY = 0.1


def _accuracy_circle_block():
    """The google.maps.Circle literal built from a fix's `accuracy`."""
    body = PAGE_JS.read_text(encoding="utf-8")
    start = body.index("radius: +p.accuracy")
    # back up to the constructor, forward to the end of the literal
    ctor = body.rindex("new google.maps.Circle(", 0, start)
    return body[ctor: body.index("});", start)]


def _number(block, key):
    m = re.search(key + r":\s*([0-9.]+)", block)
    assert m, "%s not found in the accuracy circle" % key
    return float(m.group(1))


class TestTheRingsAreLegible(unittest.TestCase):
    def setUp(self):
        self.block = _accuracy_circle_block()

    def test_stroke_is_visible(self):
        self.assertGreaterEqual(_number(self.block, "strokeOpacity"), MIN_STROKE_OPACITY)

    def test_stroke_has_width(self):
        self.assertGreaterEqual(_number(self.block, "strokeWeight"), MIN_STROKE_WEIGHT)

    def test_fill_is_visible(self):
        self.assertGreaterEqual(_number(self.block, "fillOpacity"), MIN_FILL_OPACITY)

    def test_the_radius_is_still_the_real_accuracy(self):
        """The ring means something: it is the fix's own accuracy in metres, not a
        decorative constant. google.maps.Circle takes metres, which is why this one
        ported cleanly when the pixel-radius markers did not."""
        self.assertIn("radius: +p.accuracy", self.block)


class TestTheToggleStillGatesTheLayer(unittest.TestCase):
    """Raising the opacity must not turn a diagnostic overlay into permanent chrome."""

    def setUp(self):
        self.body = PAGE_JS.read_text(encoding="utf-8")

    def test_the_layer_is_off_until_asked_for(self):
        self.assertIn("this.showAccuracy = false", self.body)

    def test_the_toggle_drives_the_layer(self):
        fn = self.body[self.body.index("applyAccuracyLayer() {"):]
        fn = fn[: fn.index("\n        }")]
        self.assertIn("this.showAccuracy", fn)
        self.assertIn("this.layers.accuracy.addTo(this.map)", fn)
        self.assertIn("this.layers.accuracy.addTo(null)", fn)

    def test_rings_are_a_trail_feature_only(self):
        """Live mode shows one current position per person; accuracy rings around
        them would be noise, and the original scoped them to the trail."""
        fn = self.body[self.body.index("applyAccuracyLayer() {"):]
        fn = fn[: fn.index("\n        }")]
        self.assertIn("'trail'", fn)
