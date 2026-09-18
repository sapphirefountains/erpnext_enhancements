import unittest
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def strip_js_comments(text):
    """
    Strips JS line comments (// ...) and block comments (/* ... */).
    This exists because map.js discusses the migration away from Leaflet
    in its header comment, and we must not let those mentions trigger
    false positives in our absence assertions. "Absence assertions need
    comments stripped".
    """
    # Remove block comments
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    # Remove line comments
    text = re.sub(r'//.*', '', text)
    return text

class TestKioskMap(unittest.TestCase):
    def test_map_js_no_leaflet(self):
        js_path = os.path.join(APP_DIR, 'public', 'js', 'kiosk', 'map.js')
        with open(js_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        clean_content = strip_js_comments(content)
        
        forbidden_tokens = [
            'L.map',
            'L.tileLayer',
            'L.polyline',
            'L.circle',
            'L.circleMarker',
            'L.marker',
            'L.divIcon',
            'leaflet.js',
            'leaflet.css'
        ]
        
        for token in forbidden_tokens:
            self.assertNotIn(token, clean_content, f"map.js must not contain Leaflet reference: {token}")

    def test_map_js_calls_google_maps_loader(self):
        js_path = os.path.join(APP_DIR, 'public', 'js', 'kiosk', 'map.js')
        with open(js_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        clean_content = strip_js_comments(content)
        
        self.assertIn('window.EEGoogleMaps.load', clean_content)
        self.assertIn('window.EEGoogleMaps.mapOptions', clean_content)
        self.assertIn('KIOSK_BOOT.maps', clean_content)
        
        self.assertNotIn('maps.googleapis.com/maps/api/js', clean_content, "map.js must use the shared loader, not inject the API itself")

    def test_kiosk_html_loads_scripts_correctly(self):
        html_path = os.path.join(APP_DIR, 'www', 'kiosk.html')
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        loader_idx = content.find('google_maps_loader.js?v={{ deploy_version }}')
        map_idx = content.find('map.js?v={{ deploy_version }}')
        
        self.assertNotEqual(loader_idx, -1, "kiosk.html must load google_maps_loader.js with the cache bust token")
        self.assertNotEqual(map_idx, -1, "kiosk.html must load map.js with the cache bust token")
        
        self.assertLess(loader_idx, map_idx, "kiosk.html must load google_maps_loader.js BEFORE map.js")

    def test_kiosk_py_exposes_maps_config(self):
        py_path = os.path.join(APP_DIR, 'www', 'kiosk.py')
        with open(py_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        self.assertIn('maps', content)
        self.assertIn('google_maps_api_key', content)
        self.assertIn('google_maps_map_id_light', content)
        self.assertIn('google_maps_map_id_dark', content)
        
        # Test docstring mentions maps
        match = re.search(r'def get_context\(context\):\s*"""(.*?)"""', content, flags=re.DOTALL)
        if match:
            docstring = match.group(1)
            self.assertIn('maps', docstring, "get_context docstring must document the 'maps' payload")
        else:
            self.fail("Could not find get_context docstring")

    def test_kiosk_sw_does_not_reference_google_maps(self):
        sw_path = os.path.join(APP_DIR, 'www', 'kiosk-sw.js')
        with open(sw_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        self.assertNotIn('maps.googleapis.com', content, "Service worker must not attempt to answer for google maps API")


class TestTheMapCanvasActuallyHasHeight(unittest.TestCase):
    """v1.483.1. The map tab rendered a correctly-sized grey box with nothing in
    it, and no error: the canvas div was injected with `height: 100%`, but
    `.tk-map` carries no `height` of its own (it sizes from `min-height: 55vh`
    inside an auto-height flex parent), so the percentage resolved against `auto`
    and collapsed to zero. The container still painted its own background, and
    markers drew happily into a 0px div -- which is why the console showed only a
    deprecation notice.

    Leaflet never hit this because it attached to `.tk-map` itself. Injecting a
    child is what introduced the dependency on how the parent's height resolves.
    """

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "public", "js", "kiosk", "map.js"), encoding="utf-8") as f:
            self.js = strip_js_comments(f.read())
        with open(os.path.join(root, "public", "css", "kiosk", "kiosk.css"), encoding="utf-8") as f:
            self.css = f.read()

    def test_the_canvas_is_not_sized_with_a_percentage_height(self):
        self.assertNotIn(
            "style.height = '100%'",
            self.js,
            "a percentage height on the canvas collapses to 0 -- .tk-map has no height",
        )
        self.assertNotIn('style.height = "100%"', self.js)

    def test_the_canvas_fills_its_container_by_absolute_positioning(self):
        self.assertIn("style.position = 'absolute'", self.js)
        for side in ("top", "right", "bottom", "left"):
            with self.subTest(side=side):
                self.assertIn(f"style.{side} = '0'", self.js)

    def test_tk_map_stays_positioned(self):
        """The absolute canvas is positioned against `.tk-map`. Drop this and it
        escapes to the nearest positioned ancestor, which is a different bug
        wearing the same grey box."""
        block = self.css[self.css.index(".tk-map {"):]
        block = block[: block.index("}")]
        self.assertIn("position: relative", block)

    def test_the_container_still_has_a_height_of_its_own(self):
        block = self.css[self.css.index(".tk-map {"):]
        block = block[: block.index("}")]
        self.assertIn("min-height", block)


class TestNullIslandIsNotALocation(unittest.TestCase):
    """0,0 is not a place, it is a missing place -- and `== null` does not catch it.

    A site that arrived as 0/0 was drawn as a real marker off the coast of Africa,
    and bounds.extend() on it pulled fitBounds across the Atlantic: the map rendered
    the entire world with two pins on it. The server already rejects 0,0
    (workforce/sites.py::_valid_coords) and so does the desk timeline (hasCoords).
    The kiosk was the one place that did not.
    """

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "public", "js", "kiosk", "map.js"), encoding="utf-8") as f:
            self.js = strip_js_comments(f.read())

    def _guard(self):
        g = self.js[self.js.index("function validCoords("):]
        return g[: g.index("\n  }")]

    def test_a_coordinate_guard_exists(self):
        self.assertIn("function validCoords(", self.js)

    def test_it_rejects_null_island(self):
        self.assertIn("lat === 0 && lng === 0", self._guard())

    def test_it_range_checks(self):
        guard = self._guard()
        self.assertIn("isFinite", guard)
        self.assertIn("90", guard)
        self.assertIn("180", guard)

    def test_both_fixes_and_site_markers_use_it(self):
        """A guard applied to only one of the two still renders the world."""
        self.assertIn("validCoords(p.latitude, p.longitude)", self.js)
        self.assertIn("validCoords(iv.site_latitude, iv.site_longitude)", self.js)

    def test_the_old_null_only_checks_are_gone(self):
        for dead in ("p.latitude == null || p.longitude == null",
                     "iv.site_latitude == null || iv.site_longitude == null"):
            with self.subTest(check=dead):
                self.assertNotIn(dead, self.js)


class TestPopupTextIsReadableOnGooglesSurface(unittest.TestCase):
    """Google renders the InfoWindow on its OWN light surface even when the map's
    colorScheme is DARK. Content inheriting the app's text colour is near-white on
    white, i.e. invisible -- which is what dark mode showed."""

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "public", "js", "kiosk", "map.js"), encoding="utf-8") as f:
            self.js = f.read()
        with open(os.path.join(root, "public", "css", "kiosk", "kiosk.css"), encoding="utf-8") as f:
            self.css = f.read()

    def _rule(self):
        block = self.css[self.css.index(".tk-gpopup"):]
        return block[: block.index("}")]

    def test_popup_content_is_wrapped_so_it_can_be_styled(self):
        self.assertIn("tk-gpopup", self.js)

    def test_the_stylesheet_sets_an_explicit_colour(self):
        self.assertIn("color:", self._rule())

    def test_the_colour_is_literal_not_a_theme_var(self):
        """A theme var is precisely what broke this: it follows the APP's surface,
        and the bubble belongs to Google."""
        colour = [ln for ln in self._rule().splitlines() if ln.strip().startswith("color:")][0]
        self.assertNotIn("var(--tk-text", colour)
        self.assertRegex(colour, r"#[0-9a-fA-F]{3,6}")
