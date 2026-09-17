import unittest
import os
import re

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
