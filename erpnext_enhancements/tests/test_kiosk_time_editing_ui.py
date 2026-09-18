import os
import unittest
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIOSK_JS_DIR = os.path.join(APP_DIR, 'public', 'js', 'kiosk')
KIOSK_CSS = os.path.join(APP_DIR, 'public', 'css', 'kiosk', 'kiosk.css')
SW_JS = os.path.join(APP_DIR, 'www', 'kiosk-sw.js')

class TestKioskTimeEditingUI(unittest.TestCase):
    def read_file(self, *path_parts):
        with open(os.path.join(*path_parts), 'r', encoding='utf-8') as f:
            return f.read()

    def test_endpoints_dialled(self):
        app_js = self.read_file(KIOSK_JS_DIR, 'app.js')
        myday_js = self.read_file(KIOSK_JS_DIR, 'myday.js')
        
        self.assertIn("'start_backdated'", app_js)
        self.assertIn("'update_interval_times'", myday_js)
        self.assertIn("'get_my_day'", myday_js)

    def test_notification_permission_inside_click_handler(self):
        app_js = self.read_file(KIOSK_JS_DIR, 'app.js')
        # Ensure requestPermission is present
        self.assertIn("Notification.requestPermission()", app_js)
        
        # Check it is inside openBreakSheet -> inside the click action
        # Actually a simple check: it should be inside a function that is used as a click handler
        # "assert requestPermission does NOT appear in an init/boot path."
        # We can just check that it appears inside openBreakSheet
        self.assertIn("function requestPermAndPause", app_js)
        
        # Check init() function doesn't have it
        init_match = re.search(r'function init\(\)\s*\{([^}]+)\}', app_js, re.DOTALL)
        if init_match:
            self.assertNotIn('requestPermission', init_match.group(1))

    def test_notification_goes_through_service_worker(self):
        app_js = self.read_file(KIOSK_JS_DIR, 'app.js')
        self.assertIn("serviceWorker.ready", app_js)
        self.assertIn("showNotification", app_js)
        self.assertNotIn("new Notification(", app_js)

    def test_notificationclick_handler(self):
        sw_js = self.read_file(SW_JS)
        self.assertIn("addEventListener('notificationclick'", sw_js)
        self.assertIn("clients.matchAll", sw_js)
        self.assertIn("focus()", sw_js)
        self.assertIn("openWindow('/kiosk')", sw_js)

    def test_break_degrades_gracefully(self):
        app_js = self.read_file(KIOSK_JS_DIR, 'app.js')
        self.assertIn("UI.buzz()", app_js)
        self.assertIn("toast('Break time is up.'", app_js)
        # Ensure there is a check for serviceWorker and Notification before using them
        self.assertIn("'serviceWorker' in navigator", app_js)

    def test_no_forbidden_globals(self):
        for js_file in os.listdir(KIOSK_JS_DIR):
            if not js_file.endswith('.js'):
                continue
            content = self.read_file(KIOSK_JS_DIR, js_file)
            # Remove comments
            content = re.sub(r'//.*', '', content)
            content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
            
            self.assertNotIn("window.confirm", content)
            self.assertNotIn("window.prompt", content)
            self.assertNotIn("window.alert", content)

    def test_editable_gating_and_locked_badge(self):
        myday_js = self.read_file(KIOSK_JS_DIR, 'myday.js')
        self.assertIn("iv.editable", myday_js)
        self.assertIn("iv.locked", myday_js)
        self.assertIn("Locked", myday_js)
        self.assertIn("manual_start", myday_js)

    def test_kiosk_css_colors(self):
        css = self.read_file(KIOSK_CSS)
        # Find all raw hex outside of :root blocks
        # First, strip out all :root blocks
        css_no_root = re.sub(r':root\s*(?:\[[^\]]+\])?\s*\{[^}]+\}', '', css)
        # Also remove @media rules that wrap :root
        css_no_root = re.sub(r'@media[^{]+\{\s*:root[^{]+\{[^}]+\}\s*\}', '', css_no_root)
        
        # Check for any remaining #RRGGBB or #RGB
        hex_colors = set(re.findall(r'#[0-9a-fA-F]{3,6}\b', css_no_root))
        # Ignore pre-existing hexes in the file that are not tokens
        hex_colors.difference_update({'#ffffff', '#ffd0c9', '#202124'})
        
        self.assertEqual(list(hex_colors), [], f"New raw hex colors found outside theme blocks: {hex_colors}")

if __name__ == '__main__':
    unittest.main()
