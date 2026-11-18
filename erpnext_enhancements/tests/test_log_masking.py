"""Bench-free unit tests for utils/log_masking.mask_secrets (v1.826.6).

FAKE-DO-NOT-USE: every credential-shaped value in this file is synthetic. Each one is
assembled from fragments at run time, so no literal in the file has a vendor's key shape.

No frappe import and no stub: the module under test is pure.

Run: python -m unittest erpnext_enhancements.tests.test_log_masking
"""

import importlib.util
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.utils import log_masking
from erpnext_enhancements.utils.log_masking import MARKER, mask_secrets

DASHES = "-" * 5
PEM_BLOCK = (
    DASHES + "BEGIN " + "PRIVATE KEY" + DASHES + "\n"
    + "FAKEfake" * 8 + "\n"
    + "FAKEfake" * 8 + "\n"
    + DASHES + "END " + "PRIVATE KEY" + DASHES
)
GOOGLE_API_KEY = "AI" + "za" + "Fake" + "x" * 31
OAUTH_CLIENT_SECRET = "GOC" + "SPX-" + "fake" + "x" * 20
GOOGLE_ACCESS_TOKEN = "ya" + "29." + "fake" + "x" * 30
GOOGLE_REFRESH_TOKEN = "1/" + "/" + "fake" + "x" * 30
STRIPE_KEYS = [
    "sk" + "_live_" + "fake" + "x" * 20,
    "sk" + "_test_" + "fake" + "x" * 20,
    "rk" + "_live_" + "fake" + "x" * 20,
    "rk" + "_test_" + "fake" + "x" * 20,
    "wh" + "sec_" + "fake" + "x" * 20,
]
HEX_SECRET = "fedc" * 8
ACCOUNT_ID = "AC" + "0" * 32
KEY_ID = "SK" + "0" * 32
PLAIN_SECRET = "not-a-real-" + "value-123"


def _masked(test, text, secret):
    """Mask ``text``, check ``secret`` is gone and the marker is there, return the result."""
    out = mask_secrets(text)
    test.assertNotIn(secret, out)
    test.assertIn(MARKER, out)
    return out


class TestVendorShapes(unittest.TestCase):
    def test_pem_private_key_block(self):
        out = _masked(self, "key = '" + PEM_BLOCK + "' # end", "FAKEfake")
        self.assertEqual(out, "key = '" + MARKER + "' # end")

    def test_pem_block_inside_an_escaped_repr(self):
        escaped = PEM_BLOCK.replace("\n", "\\n")
        out = _masked(self, "info = {'private_key': '" + escaped + "\\n'}", "FAKEfake")
        self.assertNotIn("BEGIN", out)

    def test_google_api_key(self):
        out = _masked(self, "url=https://maps.example.com/x?address=1+Main " + GOOGLE_API_KEY, GOOGLE_API_KEY)
        self.assertIn("address=1+Main", out)

    def test_google_oauth_client_secret(self):
        _masked(self, "creds " + OAUTH_CLIENT_SECRET + " end", OAUTH_CLIENT_SECRET)

    def test_google_access_and_refresh_tokens(self):
        _masked(self, "token " + GOOGLE_ACCESS_TOKEN, GOOGLE_ACCESS_TOKEN)
        _masked(self, "refresh " + GOOGLE_REFRESH_TOKEN, GOOGLE_REFRESH_TOKEN)

    def test_stripe_keys(self):
        for key in STRIPE_KEYS:
            with self.subTest(prefix=key[:8]):
                _masked(self, "stripe.api_key: " + key + ",", key)

    def test_hex_secret_after_auth_token_or_api_secret(self):
        for name in ("auth_token", "api_secret", "AUTH_TOKEN"):
            with self.subTest(name=name):
                out = _masked(self, f"client {ACCOUNT_ID} {name}={HEX_SECRET} done", HEX_SECRET)
                self.assertIn(name + "=" + MARKER, out)

    def test_account_and_key_identifiers_are_kept(self):
        text = f"account_sid {ACCOUNT_ID} key {KEY_ID} region us1"
        self.assertEqual(mask_secrets(text), text)


class TestHeaders(unittest.TestCase):
    def test_authorization_header_line(self):
        for value in ("Basic " + "ZmFrZTpmYWtl", "Bearer " + "fake.token.value", "token abc123fake:def456fake"):
            with self.subTest(value=value):
                out = _masked(self, "Authorization: " + value + "\nnext line", value.split()[-1])
                self.assertEqual(out, "Authorization: " + MARKER + "\nnext line")

    def test_authorization_in_a_headers_dict(self):
        text = "headers = {'Authorization': 'Bearer fake.token.value', 'Accept': 'application/json'}"
        out = _masked(self, text, "fake.token.value")
        self.assertIn("'Authorization': '" + MARKER + "'", out)
        self.assertIn("'Accept': 'application/json'", out)

    def test_bare_bearer_token(self):
        out = _masked(self, "sent Bearer fakeTOKENvalue123 to the API", "fakeTOKENvalue123")
        self.assertEqual(out, "sent Bearer " + MARKER + " to the API")

    def test_frappe_api_key_token(self):
        out = _masked(self, "header token fakekey123:fakesecret456 sent", "fakesecret456")
        self.assertEqual(out, "header token " + MARKER + " sent")


class TestNamedValues(unittest.TestCase):
    def test_keyword_arguments_in_a_repr(self):
        # The case frappe's name-based sanitizer cannot see: the variable is "conn", and the
        # credential is inside its repr.
        text = (
            "      conn = <Connection kwargs={'host': 'db.internal', 'user': 'app', "
            "'password': '" + PLAIN_SECRET + "', 'port': 3306}>"
        )
        out = _masked(self, text, PLAIN_SECRET)
        self.assertIn("'password': '" + MARKER + "'", out)
        self.assertIn("'host': 'db.internal'", out)
        self.assertIn("'port': 3306", out)

    def test_each_credential_word_masks_the_value_and_keeps_the_name(self):
        names = [
            "password", "db_password", "passwd", "pwd", "client_secret", "secret_key",
            "refresh_token", "api_key", "apikey", "private_key", "authorization",
            "Set-Cookie", "sid", "PASSWORD",
        ]
        for name in names:
            for text, expected in (
                (f"{name}='{PLAIN_SECRET}'", f"{name}='{MARKER}'"),
                (f'{name}="{PLAIN_SECRET}"', f'{name}="{MARKER}"'),
                (f'"{name}": "{PLAIN_SECRET}"', f'"{name}": "{MARKER}"'),
                (f"'{name}': '{PLAIN_SECRET}'", f"'{name}': '{MARKER}'"),
                (f"      {name} = '{PLAIN_SECRET}'", f"      {name} = '{MARKER}'"),
            ):
                with self.subTest(text=text):
                    self.assertEqual(mask_secrets(text), expected)

    def test_json_inside_a_string(self):
        text = '{"body": "{\\"client_secret\\": \\"' + PLAIN_SECRET + '\\"}"}'
        out = _masked(self, text, PLAIN_SECRET)
        self.assertEqual(out, '{"body": "{\\"client_secret\\": \\"' + MARKER + '\\"}"}')

    def test_bytes_value(self):
        self.assertEqual(mask_secrets(f"password=b'{PLAIN_SECRET}'"), f"password=b'{MARKER}'")

    def test_value_with_an_escaped_quote(self):
        self.assertEqual(mask_secrets("token='it\\'s fake' tail"), f"token='{MARKER}' tail")

    def test_value_cut_short_by_a_line_end_is_still_masked(self):
        text = f"      password = '{PLAIN_SECRET}...\n      next = 1"
        self.assertEqual(mask_secrets(text), f"      password = '{MARKER}\n      next = 1")

    def test_short_words_count_only_as_whole_words(self):
        text = "inside='keep me' consider='also kept' upwdx='kept too'"
        self.assertEqual(mask_secrets(text), text)


class TestUrls(unittest.TestCase):
    def test_query_values(self):
        for name in ("key", "api_key", "access_token", "token", "secret", "client_secret", "sig", "signature"):
            with self.subTest(name=name):
                text = f"GET https://api.example.com/v1/x?q=fountain&{name}={PLAIN_SECRET}&page=2"
                self.assertEqual(
                    mask_secrets(text),
                    f"GET https://api.example.com/v1/x?q=fountain&{name}={MARKER}&page=2",
                )

    def test_other_query_values_are_kept(self):
        text = "GET https://api.example.com/v1/x?monkey=1&keyword=pump&page=2"
        self.assertEqual(mask_secrets(text), text)

    def test_password_in_a_url(self):
        text = f"connect mysql://appuser:{PLAIN_SECRET}@db.internal:3306/site failed"
        self.assertEqual(mask_secrets(text), f"connect mysql://appuser:{MARKER}@db.internal:3306/site failed")

    def test_url_with_a_port_and_no_credentials_is_kept(self):
        text = "GET http://localhost:8000/api/method/ping and https://user@example.com/x"
        self.assertEqual(mask_secrets(text), text)


class TestOrdinaryText(unittest.TestCase):
    def test_a_traceback_with_nothing_to_mask_is_unchanged(self):
        text = (
            "Traceback (most recent call last):\n"
            '  File "apps/erpnext_enhancements/erpnext_enhancements/api/x.py", line 12, in run\n'
            "    token_count = len(tokens)\n"
            "      doc = <Project: doctype=Project PROJ-0001>\n"
            "      tokens = ['a', 'b']\n"
            "      key = None\n"
            "frappe.exceptions.ValidationError: Customer is required"
        )
        self.assertEqual(mask_secrets(text), text)

    def test_non_strings_and_empty_text_are_returned_unchanged(self):
        for value in (None, "", 5, b"password='x'"):
            with self.subTest(value=value):
                self.assertIs(mask_secrets(value), value)

    def test_masking_is_idempotent(self):
        samples = [
            "key = '" + PEM_BLOCK + "'",
            GOOGLE_API_KEY, OAUTH_CLIENT_SECRET, GOOGLE_ACCESS_TOKEN, "r " + GOOGLE_REFRESH_TOKEN,
            *STRIPE_KEYS,
            f"auth_token={HEX_SECRET}",
            "Authorization: Bearer fake.token.value",
            "Authorization: token abc123fake:def456fake",
            f"'password': '{PLAIN_SECRET}'",
            '{"body": "{\\"client_secret\\": \\"' + PLAIN_SECRET + '\\"}"}',
            f"https://x.example.com/?key={PLAIN_SECRET}",
            f"mysql://appuser:{PLAIN_SECRET}@db.internal/site",
        ]
        for sample in samples:
            with self.subTest(sample=sample[:30]):
                once = mask_secrets(sample)
                self.assertNotEqual(once, sample)
                self.assertEqual(mask_secrets(once), once)


class TestLargeInput(unittest.TestCase):
    """Linear time on a 1 MB input, and work capped on larger ones without shortening anything."""

    #: Generous for a shared CI runner; locally each case takes well under half a second.
    BUDGET_SECONDS = 3.0

    def _timed(self, text):
        started = time.perf_counter()
        out = mask_secrets(text)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, self.BUDGET_SECONDS)
        return out

    def test_a_one_megabyte_traceback(self):
        frame = (
            '  File "apps/frappe/frappe/app.py", line 120, in application\n'
            "      conn = <Connection kwargs={'user': 'app', 'password': '" + PLAIN_SECRET + "'}>\n"
            "      request = <Request 'http://localhost/api/method/x?key=" + PLAIN_SECRET + "' [POST]>\n"
        )
        text = frame * (1_000_000 // len(frame) + 1)
        out = self._timed(text)
        self.assertNotIn(PLAIN_SECRET, out)

    def test_pathological_one_megabyte_inputs(self):
        cases = {
            "unclosed quotes": "token='" * 150_000,
            "separators with no value": "password=" * 120_000,
            "header words": "authorization: " * 70_000,
            "long quoted value": "password='" + "\\x" * 500_000,
            "headers with no body": (DASHES + "BEGIN " + "PRIVATE KEY" + DASHES) * 35_000,
            "url fragments": "a://" + "b" * 100 + ":" * 1_000_000,
            "query names": "?key=&key=" * 100_000,
            "long names": ("password" + "a" * 40 + " ") * 20_000,
            "one character": "a" * 1_000_000,
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                self._timed(text)

    def test_text_past_the_cap_is_kept_and_never_truncated(self):
        cap = log_masking.MAX_SCAN_CHARS
        head = f"password='{PLAIN_SECRET}'\n"
        filler = ("x" * 99 + "\n") * ((cap - len(head)) // 100)
        tail = "\n" + "y" * 1000 + f" password='{PLAIN_SECRET}' end"
        text = head + filler + tail
        self.assertGreater(len(text), cap)
        out = self._timed(text)
        self.assertTrue(out.startswith(f"password='{MARKER}'\n"))
        # The tail is past the scanned part: kept exactly, not cut off.
        self.assertTrue(out.endswith(tail))
        self.assertEqual(len(out), len(text) - len(PLAIN_SECRET) + len(MARKER))


class TestRepositoryScanner(unittest.TestCase):
    def test_the_secrets_scanner_finds_nothing_in_the_masker_or_these_tests(self):
        spec = importlib.util.spec_from_file_location(
            "check_no_committed_secrets", REPO_ROOT / "scripts" / "check_no_committed_secrets.py"
        )
        scanner = importlib.util.module_from_spec(spec)
        # Registered first: the scanner's dataclass looks its own module up in sys.modules.
        sys.modules.setdefault(spec.name, scanner)
        spec.loader.exec_module(scanner)
        for path in (
            "erpnext_enhancements/utils/log_masking.py",
            "erpnext_enhancements/tests/test_log_masking.py",
        ):
            with self.subTest(path=path):
                text = (REPO_ROOT / path).read_text(encoding="utf-8")
                self.assertEqual(scanner.scan_text(path, text), [])


if __name__ == "__main__":
    unittest.main()
