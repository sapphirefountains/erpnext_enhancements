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
