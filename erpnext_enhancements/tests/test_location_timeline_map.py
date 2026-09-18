import unittest
import os
import re
from pathlib import Path


def _strip_comments(text):
    """Module-level twin of the class helper, for the suites appended below.

    Comment stripping is not optional in this file: it discusses the Leaflet
    migration in prose and names every token the absence assertions search for.
    """
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    text = re.sub(r"//.*", "", text)
    return text

class TestLocationTimelineMap(unittest.TestCase):
    def setUp(self):
        base_dir = os.path.dirname(os.path.dirname(__file__))
        self.js_path = os.path.join(base_dir, 'workforce', 'page', 'location_timeline', 'location_timeline.js')
        self.css_path = os.path.join(base_dir, 'public', 'css', 'workforce', 'location_timeline.css')

        with open(self.js_path, 'r') as f:
            self.js_content = f.read()

        with open(self.css_path, 'r') as f:
            self.css_content = f.read()

    def strip_comments(self, text):
        """
        Strips // and /* */ comments from JavaScript code.
        This is necessary because the file legitimately discusses the migration in prose
        and naming the excluded tokens in comments would trigger false positives.
        """
        # Remove block comments
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        # Remove line comments
        text = re.sub(r'//.*', '', text)
        return text

    def test_no_leaflet_references(self):
        clean_js = self.strip_comments(self.js_content)
        forbidden_tokens = [
            'L.map', 'L.tileLayer', 'L.polyline', 'L.circle', 
            'L.circleMarker', 'L.divIcon', 'L.marker', 
            'L.layerGroup', 'L.canvas'
        ]
        for token in forbidden_tokens:
            self.assertNotIn(token, clean_js, f"Found forbidden token {token} in JS")
        
        self.assertNotIn('leaflet', clean_js.lower(), "Found 'leaflet' reference in JS")

    def test_calls_google_maps_apis(self):
        clean_js = self.strip_comments(self.js_content)
        self.assertIn('window.EEGoogleMaps.load', clean_js)
        self.assertIn('window.EEGoogleMaps.mapOptions', clean_js)

    def test_calls_maps_config(self):
        clean_js = self.strip_comments(self.js_content)
        self.assertIn('erpnext_enhancements.api.travel.get_maps_config', clean_js)

    def test_no_mapid_and_styles_together(self):
        clean_js = self.strip_comments(self.js_content)
        # We ensure mapOptions doesn't have both mapId and styles
        self.assertNotRegex(clean_js, r'mapId.*styles|styles.*mapId', "Map should never be constructed with both mapId and styles.")

    def test_no_leaflet_css(self):
        self.assertNotIn('.leaflet-', self.css_content)

    def test_bare_paths_in_frappe_require(self):
        clean_js = self.strip_comments(self.js_content)
        # Check that assets list exists and has no ?v=
        self.assertIn('const ASSETS = [', clean_js)
        match = re.search(r'const ASSETS = \[(.*?)\];', clean_js, re.DOTALL)
        self.assertIsNotNone(match, "ASSETS array not found")
        assets_content = match.group(1)
        self.assertNotIn('?v=', assets_content)
        
if __name__ == '__main__':
    unittest.main()


class TestNoLeafletMethodsSurvive(unittest.TestCase):
    """The constructor test was not enough.

    The v1.483.0 port asserted that no `L.map(`, `L.polyline(` etc. remained -- and
    passed -- while `this.map.invalidateSize()` sat in onShow() the whole time. That is
    a Leaflet METHOD on a google.maps.Map, which has no such thing: it threw a TypeError
    on every RETURN visit to the page (the first survived only because `this.map` was
    still null) and took the rest of onShow with it. A constructor-shaped absence test
    cannot see a method call, so this is the other half of that guard.

    google.maps.Map's equivalent of invalidateSize is
    `google.maps.event.trigger(map, 'resize')`.
    """

    #: Leaflet map/layer methods with no google.maps.Map counterpart. `addTo`,
    #: `addLayer` and `clearLayers` are deliberately ABSENT from this list: the port
    #: keeps a small layer-group shim that provides exactly those three, so they are
    #: intentional here rather than leftovers.
    LEAFLET_ONLY = (
        "invalidateSize",
        "setView",
        "flyTo",
        "panTo",
        "eachLayer",
        "hasLayer",
        "openPopup",
        "closePopup",
        "setZoomAround",
        "latLngToLayerPoint",
        "getRenderer",
    )

    def _sources(self):
        app = Path(__file__).resolve().parents[1]
        return {
            "location_timeline.js": app / "workforce/page/location_timeline/location_timeline.js",
            "kiosk/map.js": app / "public/js/kiosk/map.js",
        }

    def test_no_leaflet_only_method_is_called(self):
        for label, path in self._sources().items():
            body = _strip_comments(path.read_text(encoding="utf-8"))
            for method in self.LEAFLET_ONLY:
                with self.subTest(file=label, method=method):
                    self.assertNotIn(
                        f".{method}(",
                        body,
                        f"{label} calls Leaflet's {method}() on a Google map",
                    )

    def test_resizing_uses_the_google_event(self):
        body = _strip_comments(self._sources()["location_timeline.js"].read_text(encoding="utf-8"))
        self.assertIn("google.maps.event.trigger", body)
