import unittest
import os
import re

class TestTritonWidgetLocation(unittest.TestCase):
    def setUp(self):
        js_path = os.path.join(
            os.path.dirname(__file__),
            "..", "public", "js", "global_enhancements", "triton_widget.js"
        )
        with open(js_path, "r", encoding="utf-8") as f:
            self.js_code = f.read()

        # Strip comments
        self.code_no_comments = re.sub(r'//.*', '', self.js_code)
        self.code_no_comments = re.sub(r'/\*.*?\*/', '', self.code_no_comments, flags=re.DOTALL)

    def test_geolocation_only_in_handlers(self):
        # 1. The widget calls stash_location_fix and does so only inside a click handler
        self.assertIn("stash_location_fix", self.code_no_comments)
        
        # getCurrentPosition must not appear inside init( or build( function bodies.
        # This regex tries to find the bodies of init and build and checks for getCurrentPosition.
        init_body_match = re.search(r'function init\(\)\s*\{([^}]*)\}', self.code_no_comments)
        if init_body_match:
            self.assertNotIn("getCurrentPosition", init_body_match.group(1))

        build_body_match = re.search(r'function build\(\)\s*\{([^}]*)\}', self.code_no_comments)
        if build_body_match:
            self.assertNotIn("getCurrentPosition", build_body_match.group(1))

    def test_geolocation_options(self):
        # 2. maximumAge: 0 and enableHighAccuracy: true are both set
        self.assertIn("maximumAge: 0", self.code_no_comments)
        self.assertIn("enableHighAccuracy: true", self.code_no_comments)

    def test_ui_command_clock_in(self):
        # 3. The ui_command switch handles clock_in_request
        self.assertIn('clock_in_request', self.code_no_comments)

    def test_log_time_params(self):
        # 4. The confirm path calls api.time_kiosk.log_time with action "Start"
        # and NEVER calls any clock-in endpoint without lat/lng in the same call.
        self.assertIn('api.time_kiosk.log_time', self.code_no_comments)
        self.assertIn('"Start"', self.code_no_comments)
        # Ensure log_time is called with lat and lng
        log_time_calls = re.findall(r'log_time[\s\S]*?\}', self.code_no_comments)
        for call in log_time_calls:
            self.assertIn('lat', call)
            self.assertIn('lng', call)
            self.assertIn('accuracy', call)

    def test_no_localstorage_for_location(self):
        # 5. The widget never writes a latitude/longitude into localStorage
        ls_calls = re.findall(r'localStorage\.setItem\((.*?)\)', self.code_no_comments)
        for call in ls_calls:
            self.assertNotIn('lat', call.lower())
            self.assertNotIn('lng', call.lower())
            self.assertNotIn('location', call.lower())

if __name__ == "__main__":
    unittest.main()
