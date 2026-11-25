# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The knowledge base's reference library (2026-11-25): saved links and uploaded files.

**A pytest suite, on its own CI step** ("Knowledge base reference library"). Plain pytest functions:
``python -m unittest`` collects none of them (CLAUDE.md). Locally, as CI sees it::

    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest erpnext_enhancements/tests/test_knowledge_base_sources.py -q

What it pins, and the bug each part stops:

* **The link guard** (``source_fetch``): the server never reads an internal address. Loopback,
  private, link-local (the cloud metadata server), carrier-grade NAT, IPv4 written inside IPv6, a
  redirect from a public page to an internal one, and a socket that turns out to be private after
  connecting are all refused; only ``http``/``https`` on the default ports, no credentials in the
  address, at most five redirects, a size cap, and only readable content types (refused before the
  body is downloaded). A regression here is ERPNext storing its own infrastructure's answers as a
  "source" every staff user can read.
* **The text** (``source_text``): HTML without scripts or styles and with tables as rows, Word
  paragraphs, headings, lists and tables, a real PDF's text, Markdown and text with a byte-order mark,
  and the zip-bomb guard.
* **The job** (``sources.extract_source``) over a fake frappe installed for one test at a time: a
  link read, kept and summarized; secret-shaped text refused with nothing stored, kept or sent to
  Gemini; an unreadable kind; Gemini down still Ready; a typed summary never replaced; a source changed
  mid-read left alone; an unexpected failure logged by type only; a picture read through Gemini.
* **The controller** and **Fetch again**: what resets a source, what never does, the secret scan on
  typed text, and the write check.
* **The schema and wiring**: private by construction, the exact permission matrix, every server-set
  field read-only at permlevel 1, Select options equal to the constants, and the hooks.
"""

import ast
import datetime
import importlib
import io
import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

from erpnext_enhancements.knowledge_base import constants as K
from erpnext_enhancements.knowledge_base import source_fetch, source_text

APP = Path(__file__).resolve().parents[1]
SOURCE_JSON = APP / "knowledge_base" / "doctype" / "knowledge_source" / "knowledge_source.json"
HOOKS = APP / "hooks.py"


# ------------------------------------------------------------------ the link guard


@pytest.mark.parametrize(
	"url,fragment",
	[
		("", "Enter the link"),
		("   ", "Enter the link"),
		("https://example.com/a b", "a space"),
		("ftp://example.com/file.pdf", "Only web links"),
		("javascript:alert(1)", "Only web links"),
		("file:///etc/passwd", "Only web links"),
		("https://user:pw@example.com/", "user name or password"),
		("https://example.com:8080/", "standard web ports"),
		("https:///nohost", "not a web address"),
		("https://example.com/" + "a" * 1000, "longer than"),
	],
)
def test_a_link_that_cannot_be_saved_says_why(url, fragment):
	assert fragment in (source_fetch.url_problem(url) or "")


@pytest.mark.parametrize(
	"url",
	[
		"https://example.com/",
		"http://example.com/page?x=1",
		"https://example.com:443/a",
		"HTTPS://Example.com",
	],
)
def test_ordinary_links_pass(url):
	assert source_fetch.url_problem(url) is None


@pytest.mark.parametrize(
	"url,expected",
	[
		("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit", ("document", "1AbCdEfGhIjKlMnOp")),
		(
			"https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/edit#gid=0",
			("spreadsheets", "1AbCdEfGhIjKlMnOp"),
		),
		("https://docs.google.com/presentation/d/1AbCdEfGhIjKlMnOp/", ("presentation", "1AbCdEfGhIjKlMnOp")),
		("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view", ("file", "1AbCdEfGhIjKlMnOp")),
		("https://docs.google.com/document/u/0/d/1AbCdEfGhIjKlMnOp/edit", ("document", "1AbCdEfGhIjKlMnOp")),
		("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp", ("file", "1AbCdEfGhIjKlMnOp")),
		("https://example.com/document/d/1AbCdEfGhIjKlMnOp", None),
		("https://docs.google.com/forms/d/1AbCdEfGhIjKlMnOp", None),
	],
)
def test_google_links_are_recognized(url, expected):
	assert source_fetch.google_file(url) == expected


@pytest.mark.parametrize(
	"address",
	[
		"127.0.0.1",
		"10.1.2.3",
		"172.16.0.1",
		"192.168.1.1",
		"169.254.169.254",
		"100.64.0.1",
		"0.0.0.0",
		"224.0.0.1",
		"255.255.255.255",
		"::1",
		"fe80::1",
		"fc00::1",
		"::ffff:127.0.0.1",
		"::ffff:169.254.169.254",
		"not-an-ip",
	],
)
def test_internal_addresses_are_never_public(address):
	assert source_fetch.address_is_public(address) is False


@pytest.mark.parametrize("address", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_are_public(address):
	assert source_fetch.address_is_public(address) is True


def resolver(table):
	"""A ``getaddrinfo`` stand-in answering from ``{host: [addresses]}``."""

	def resolve(host, port, proto=0):
		if host not in table:
			raise OSError("no such host")
		return [(2, 1, 6, "", (address, port)) for address in table[host]]

	return resolve


def test_a_host_with_any_internal_address_is_refused():
	resolve = resolver({"mixed.example": ["93.184.216.34", "10.0.0.5"]})
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.resolve_public("mixed.example", 443, resolve=resolve)
	assert caught.value.kind == "blocked"


def test_a_host_that_does_not_resolve_is_a_network_problem():
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.resolve_public("nowhere.example", 443, resolve=resolver({}))
	assert caught.value.kind == "network"


class FakeSocket:
	def __init__(self, peer):
		self.peer = peer

	def getpeername(self):
		return (self.peer, 443)


class FakeResponse:
	def __init__(self, status=200, headers=None, body=b"", peer=None):
		self.status_code = status
		self.headers = headers or {}
		self.body = body
		self.read = False
		self.closed = False
		self.raw = types.SimpleNamespace(
			connection=types.SimpleNamespace(sock=FakeSocket(peer)) if peer else None
		)

	def iter_content(self, size):
		self.read = True
		for start in range(0, len(self.body), size):
			yield self.body[start : start + size]

	def close(self):
		self.closed = True


class FakeSession:
	def __init__(self, responses):
		self.responses = dict(responses)
		self.calls = []

	def get(self, url, **kwargs):
		self.calls.append((url, kwargs))
		answer = self.responses[url]
		if isinstance(answer, Exception):
			raise answer
		return answer


PUBLIC = resolver(
	{
		"example.com": ["93.184.216.34"],
		"cdn.example.com": ["93.184.216.35"],
		"internal.example": ["10.0.0.5"],
		"169.254.169.254": ["169.254.169.254"],
	}
)


def test_a_page_is_fetched_with_no_credentials_and_no_automatic_redirects():
	page = FakeResponse(headers={"Content-Type": "text/html; charset=utf-8"}, body=b"<p>Hello</p>")
	session = FakeSession({"https://example.com/a": page})
	fetched = source_fetch.fetch("https://example.com/a", session=session, resolve=PUBLIC)
	assert fetched.data == b"<p>Hello</p>"
	assert fetched.content_type == "text/html"
	assert fetched.final_url == "https://example.com/a"
	((_url, kwargs),) = session.calls
	assert kwargs["allow_redirects"] is False
	assert kwargs["stream"] is True
	assert kwargs["headers"] == source_fetch.HEADERS
	assert not {"Cookie", "Authorization"} & set(kwargs["headers"])
	assert page.closed


def test_a_redirect_to_an_internal_host_is_refused_before_it_is_requested():
	session = FakeSession(
		{"https://example.com/a": FakeResponse(302, {"Location": "http://internal.example/admin"})}
	)
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.fetch("https://example.com/a", session=session, resolve=PUBLIC)
	assert caught.value.kind == "blocked"
	assert [url for url, _ in session.calls] == ["https://example.com/a"]


def test_a_redirect_to_the_metadata_server_is_refused():
	session = FakeSession(
		{
			"https://example.com/a": FakeResponse(
				301, {"Location": "http://169.254.169.254/computeMetadata/v1/"}
			)
		}
	)
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.fetch("https://example.com/a", session=session, resolve=PUBLIC)
	assert caught.value.kind == "blocked"


def test_a_relative_redirect_is_followed_and_checked():
	session = FakeSession(
		{
			"https://example.com/a": FakeResponse(302, {"Location": "/b"}),
			"https://example.com/b": FakeResponse(headers={"Content-Type": "text/plain"}, body=b"ok"),
		}
	)
	assert source_fetch.fetch("https://example.com/a", session=session, resolve=PUBLIC).data == b"ok"


def test_too_many_redirects_stop():
	session = FakeSession({"https://example.com/a": FakeResponse(302, {"Location": "/a"})})
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.fetch("https://example.com/a", session=session, resolve=PUBLIC)
	assert caught.value.kind == "http"
	assert len(session.calls) == source_fetch.MAX_REDIRECTS + 1


@pytest.mark.parametrize("char", ["\\", '"', "<", ">", "^", "`", "{", "|", "}", "\x00", "\x1f", "\x7f", "\t"])
def test_a_character_outside_rfc_3986_is_refused(char):
	assert "cannot hold" in source_fetch.url_problem(f"https://example.com/a{char}b")


def test_a_host_written_outside_ascii_is_refused():
	assert "letters a web address cannot hold" in source_fetch.url_problem("https://ex\u00e4mple.com/")


def test_the_two_url_parsers_must_agree_on_the_host(monkeypatch):
	monkeypatch.setattr(source_fetch, "_connecting_host", lambda url: "elsewhere.example")
	assert "not a web address" in source_fetch.url_problem("https://example.com/")


class _Server:
	"""A real HTTP server on 127.0.0.1 that counts the requests it is sent and answers with
	``Connection: close`` and no length, the shape that once left a socket check nothing to read."""

	def __init__(self, body=b"<p>Hello from a closing server</p>"):
		import http.server
		import threading

		server = self
		self.requests = 0

		class Handler(http.server.BaseHTTPRequestHandler):
			protocol_version = "HTTP/1.0"

			def do_GET(self):
				server.requests += 1
				self.send_response(200)
				self.send_header("Content-Type", "text/html")
				self.send_header("Connection", "close")
				self.end_headers()
				self.wfile.write(body)

			def log_message(self, *args):
				pass

		self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		self.port = self.httpd.server_address[1]
		self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
		self.thread.start()

	def close(self):
		self.httpd.shutdown()
		self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
	running = _Server()
	monkeypatch.setattr(source_fetch, "ALLOWED_PORTS", (None, 80, 443, running.port))
	yield running
	running.close()


def test_an_internal_address_is_refused_at_connect_time_before_a_byte_is_sent(server):
	"""The lookup check is told the address is public; the connection still lands on 127.0.0.1, and
	the guarded session refuses it there, so the server never sees a request."""
	lying = resolver({"127.0.0.1": ["93.184.216.34"]})
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.fetch(f"http://127.0.0.1:{server.port}/page", resolve=lying)
	assert caught.value.kind == "blocked"
	assert server.requests == 0


def test_a_closing_response_is_read_whole_through_the_guarded_session(server, monkeypatch):
	monkeypatch.setattr(source_fetch, "address_is_public", lambda address: True)
	fetched = source_fetch.fetch(f"http://127.0.0.1:{server.port}/page")
	assert fetched.data == b"<p>Hello from a closing server</p>"
	assert fetched.content_type == "text/html"
	assert server.requests == 1


def test_a_socket_whose_peer_cannot_be_read_is_refused():
	class Broken:
		closed = False

		def getpeername(self):
			raise OSError("gone")

		def close(self):
			self.closed = True

	sock = Broken()
	with pytest.raises(source_fetch.FetchRefused):
		source_fetch.require_public_socket(sock)
	assert sock.closed


def test_the_guarded_session_takes_no_cookies_and_no_environment_proxy():
	session = source_fetch.guarded_session()
	assert session.trust_env is False
	assert tuple(session.cookies._policy.allowed_domains()) == ()


def test_a_slow_drip_stops_at_the_deadline(monkeypatch):
	now = {"t": 0.0}

	def clock():
		now["t"] += 20
		return now["t"]

	monkeypatch.setattr(source_fetch, "_clock", clock)
	slow = FakeResponse(headers={"Content-Type": "text/plain"}, body=b"x" * (64 * 1024 * 4))
	session = FakeSession({"https://example.com/a": slow})
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.fetch("https://example.com/a", session=session, resolve=PUBLIC)
	assert caught.value.kind == "network" and "too slowly" in caught.value.message


def test_an_error_page_is_not_saved():
	session = FakeSession({"https://example.com/a": FakeResponse(404, {"Content-Type": "text/html"})})
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.fetch("https://example.com/a", session=session, resolve=PUBLIC)
	assert caught.value.kind == "http"
	assert "404" in caught.value.message


def test_an_unreadable_kind_is_refused_before_its_body_is_downloaded():
	video = FakeResponse(headers={"Content-Type": "video/mp4"}, body=b"x" * 10)
	session = FakeSession({"https://example.com/v.mp4": video})
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.fetch("https://example.com/v.mp4", session=session, resolve=PUBLIC)
	assert caught.value.kind == "unsupported"
	assert video.read is False


def test_a_declared_size_over_the_cap_is_refused_unread():
	big = FakeResponse(headers={"Content-Type": "application/pdf", "Content-Length": "999999999"}, body=b"x")
	session = FakeSession({"https://example.com/a.pdf": big})
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.fetch("https://example.com/a.pdf", session=session, resolve=PUBLIC)
	assert caught.value.kind == "too_large"
	assert big.read is False


def test_a_streamed_body_over_the_cap_is_refused():
	big = FakeResponse(headers={"Content-Type": "text/plain"}, body=b"x" * 300)
	session = FakeSession({"https://example.com/a": big})
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.fetch("https://example.com/a", session=session, resolve=PUBLIC, max_bytes=100)
	assert caught.value.kind == "too_large"


def test_a_pdf_served_as_octet_stream_is_read_as_a_pdf():
	pdf = FakeResponse(headers={"Content-Type": "application/octet-stream"}, body=b"%PDF-1.4")
	session = FakeSession({"https://cdn.example.com/manual.pdf": pdf})
	fetched = source_fetch.fetch("https://cdn.example.com/manual.pdf", session=session, resolve=PUBLIC)
	assert fetched.content_type == "application/pdf"
	assert fetched.filename == "manual.pdf"


def test_a_timeout_is_a_network_problem_with_no_detail():
	class ReadTimeout(Exception):
		pass

	session = FakeSession({"https://example.com/a": ReadTimeout("secret detail")})
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.fetch("https://example.com/a", session=session, resolve=PUBLIC)
	assert caught.value.kind == "network"
	assert "secret detail" not in caught.value.message


def test_the_saved_copy_gets_a_safe_name():
	assert source_fetch.safe_filename("../../etc/passwd") == "etc-passwd"
	assert source_fetch.safe_filename("Pump Manual (rev 2).pdf") == "Pump-Manual-rev-2-.pdf"
	assert source_fetch.safe_filename("page", ".html") == "page.html"
	assert len(source_fetch.safe_filename("x" * 300, ".pdf")) == 100


# ------------------------------------------------------------------ the text


def test_kinds():
	assert source_text.kind_of("text/html; charset=utf-8") == "html"
	assert source_text.kind_of("application/pdf") == "pdf"
	assert source_text.kind_of("", "Spec.DOCX") == "docx"
	assert source_text.kind_of("application/octet-stream", "notes.md") == "text"
	assert source_text.kind_of("image/jpeg") == "image"
	assert source_text.kind_of("video/mp4", "clip.pdf") is None
	assert source_text.kind_of("application/vnd.ms-excel", "a.xls") is None


def test_html_keeps_what_a_reader_sees():
	page = (
		b"<html><head><title>Pump Guide</title><style>p{color:red}</style>"
		b"<script>alert('x')</script></head><body><h1>Pumps</h1><p>Prime the pump.</p>"
		b"<ul><li>Check seals</li><li>Check flow</li></ul>"
		b"<table><tr><th>Model</th><th>GPM</th></tr><tr><td>A1</td><td>40</td></tr></table>"
		b"<noscript>enable js</noscript></body></html>"
	)
	text, title = source_text.html_text(page)
	assert title == "Pump Guide"
	assert "# Pumps" in text
	assert "Prime the pump." in text
	assert "- Check seals" in text
	assert "Model | GPM" in text and "A1 | 40" in text
	for hidden in ("alert", "color:red", "enable js"):
		assert hidden not in text


def test_html_reads_its_declared_charset():
	page = '<meta charset="iso-8859-1"><p>Caf\xe9</p>'.encode("latin-1")
	text, _title = source_text.html_text(page)
	assert "Café" in text


def _docx(body_xml):
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w") as archive:
		archive.writestr(
			"word/document.xml",
			'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
			f"<w:body>{body_xml}</w:body></w:document>",
		)
	return buffer.getvalue()


def test_word_paragraphs_headings_lists_and_tables():
	data = _docx(
		'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Receiving</w:t></w:r></w:p>'
		"<w:p><w:r><w:t>Count every box.</w:t></w:r></w:p>"
		"<w:p><w:pPr><w:numPr/></w:pPr><w:r><w:t>Sign the slip</w:t></w:r></w:p>"
		"<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Part</w:t></w:r></w:p></w:tc>"
		"<w:tc><w:p><w:r><w:t>Qty</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
	)
	extracted = source_text.extract(data, "", "procedure.docx")
	assert extracted.kind == "docx"
	assert "# Receiving" in extracted.text
	assert "Count every box." in extracted.text
	assert "- Sign the slip" in extracted.text
	assert "Part | Qty" in extracted.text
	assert extracted.title == "Receiving"


def test_a_word_document_that_unpacks_too_large_is_refused(monkeypatch):
	monkeypatch.setattr(source_text, "MAX_DOCX_XML_BYTES", 10)
	with pytest.raises(ValueError):
		source_text.docx_text(_docx("<w:p><w:r><w:t>long enough</w:t></w:r></w:p>"))


def test_a_damaged_word_document_is_a_plain_message():
	with pytest.raises(ValueError, match="could not be read"):
		source_text.docx_text(b"not a zip")


def _pdf(text):
	"""A one-page PDF whose page draws ``text`` in Helvetica, with a correct xref table."""
	stream = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode("latin-1")
	objects = [
		b"<< /Type /Catalog /Pages 2 0 R >>",
		b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
		b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
		b"/Resources << /Font << /F1 5 0 R >> >> >>",
		b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
		b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
	]
	out = io.BytesIO()
	out.write(b"%PDF-1.4\n")
	offsets = []
	for number, body in enumerate(objects, start=1):
		offsets.append(out.tell())
		out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
	xref = out.tell()
	out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
	for offset in offsets:
		out.write(b"%010d 00000 n \n" % offset)
	out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
	return out.getvalue()


def test_a_pdf_gives_its_text():
	pytest.importorskip("pypdf")
	extracted = source_text.extract(_pdf("Basin drain procedure"), "application/pdf", "drain.pdf")
	assert extracted.kind == "pdf"
	assert "Basin drain procedure" in extracted.text


def test_a_damaged_pdf_is_a_plain_message():
	pytest.importorskip("pypdf")
	with pytest.raises(ValueError):
		source_text.pdf_text(b"%PDF-1.4 garbage")


def test_text_and_markdown_drop_the_bom_and_tidy():
	extracted = source_text.extract("﻿# Notes\r\n\r\n\r\n\r\nline  two  \n".encode(), "text/markdown")
	assert extracted.text == "# Notes\n\nline two"
	assert extracted.title == "Notes"


def test_a_picture_has_no_text_here_and_an_unknown_kind_is_refused():
	assert source_text.extract(b"\x89PNG", "image/png") == source_text.Extracted("", "", "image")
	with pytest.raises(ValueError):
		source_text.extract(b"x", "application/zip")


# ------------------------------------------------------------------ a fake frappe for the job


class FakeDoc:
	def __init__(self, site, doctype, values):
		object.__setattr__(self, "_site", site)
		object.__setattr__(self, "doctype", doctype)
		object.__setattr__(self, "_values", dict(values))
		object.__setattr__(self, "flags", _Flags())

	def __getattr__(self, key):
		values = object.__getattribute__(self, "_values")
		return values.get(key)

	def __setattr__(self, key, value):
		self._values[key] = value

	def get(self, key, default=None):
		return self._values.get(key, default)

	def set(self, key, value):
		self._values[key] = value

	def save(self):
		self._site.saved.append((self.doctype, dict(self._values), dict(self.flags)))
		if self.doctype == K.SOURCE_DOCTYPE:
			self._site.sources[self._values["name"]] = dict(self._values)
		return self

	def insert(self, ignore_permissions=False):
		if self.doctype == "File":
			self._values.setdefault("file_url", f"/private/files/{self._values['file_name']}")
			self._site.files.append(dict(self._values))
		elif self.doctype == "Error Log":
			self._site.error_logs.append(dict(self._values))
		elif self.doctype == K.SOURCE_DOCTYPE:
			name = f"KBS-{len(self._site.sources) + 1:05d}"
			self._values.update(name=name, extraction_state="Pending", status="Active")
			self._site.sources[name] = dict(self._values)
			self._site.inserted.append(dict(self._values))
		return self

	@property
	def name(self):
		return self._values.get("name")

	def db_set(self, key, value, update_modified=True):
		self._values[key] = value
		self._site.sources[self._values["name"]][key] = value

	def get_content(self):
		return self._values.get("_content", b"")


class _Flags(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class FakeSite:
	def __init__(self):
		self.sources = {}
		self.saved = []
		self.files = []
		self.error_logs = []
		self.enqueued = []
		self.deleted = []
		self.file_rows = {}
		self.inserted = []
		self.perms = {}
		self.list_calls = []
		self.roles = ["KB Author"]
		self.rollbacks = 0
		self.sql_calls = []
		self.locked = []

	def module(self):
		site = self
		frappe = types.ModuleType("frappe")
		frappe.__path__ = []
		utils = types.ModuleType("frappe.utils")
		utils.cint = lambda v: int(v or 0)
		utils.now_datetime = lambda: datetime.datetime(2026, 11, 25, 12, 0, 0)
		utils.add_to_date = lambda when, minutes=0, hours=0: when + datetime.timedelta(
			minutes=minutes, hours=hours
		)
		utils.get_url = lambda: "https://erp.example.com"
		frappe.utils = utils
		frappe.has_permission = lambda doctype, ptype="read", *a, **k: site.perms.get((doctype, ptype), True)

		def get_list(doctype, filters=None, fields=None, pluck=None, limit=None, limit_page_length=None):
			site.list_calls.append((doctype, dict(filters or {})))
			if doctype != K.SOURCE_DOCTYPE or not site.perms.get((doctype, "read"), True):
				return []
			rows = [
				row
				for row in site.sources.values()
				if row.get("_readable", True)
				and all(
					row.get(k) in v[1] if isinstance(v, list) and v[0] == "in" else row.get(k) == v
					for k, v in (filters or {}).items()
				)
			]
			if limit:
				rows = rows[:limit]
			if pluck:
				return [row.get(pluck) for row in rows]
			return [{f: row.get(f) for f in (fields or ["name"])} for row in rows]

		frappe.get_list = get_list
		frappe._ = lambda text: text
		frappe.flags = _Flags()
		frappe.local = types.SimpleNamespace(site="test-site")

		class Db:
			def exists(self, doctype, name=None):
				if doctype == K.SOURCE_DOCTYPE:
					return name in site.sources
				return False

			def get_value(self, doctype, filters, field):
				if doctype == K.SOURCE_DOCTYPE:
					return site.sources.get(filters, {}).get(field)
				for name, row in site.file_rows.items():
					if all(row.get(k) == v for k, v in filters.items()):
						return name
				return None

			def table_exists(self, doctype):
				return True

			def set_value(self, doctype, name, values, update_modified=True):
				site.sources[name].update(values)

			def rollback(self):
				site.rollbacks += 1

			def sql(self, query, values=None, as_dict=False):
				site.sql_calls.append(query)
				rows = [
					row
					for row in site.sources.values()
					if row.get("status") == "Active" and row.get("extraction_state") == "Ready"
				]
				if "count(*)" in query:
					newest = max((str(row.get("modified") or "") for row in rows), default=None)
					return [(len(rows), newest)]
				cut = values[0]
				return [
					{
						"name": row["name"],
						"title": row.get("title"),
						"keywords": row.get("keywords"),
						"summary": row.get("summary"),
						"department_block": row.get("department_block"),
						"body": (row.get("extracted_text") or "")[:cut],
					}
					for row in rows
				]

		frappe.db = Db()

		def get_doc(doctype, name=None, for_update=False):
			if for_update:
				site.locked.append(name)
			if isinstance(doctype, dict):
				return FakeDoc(site, doctype["doctype"], doctype)
			if doctype == K.SOURCE_DOCTYPE:
				return FakeDoc(site, doctype, site.sources[name])
			if doctype == "File":
				return FakeDoc(site, "File", site.file_rows[name])
			raise KeyError(doctype)

		frappe.get_doc = get_doc

		def get_all(doctype, filters=None, fields=None, pluck=None, limit=None):
			filters = dict(filters or {})
			rows = []
			if doctype == K.SOURCE_DOCTYPE:
				before = (filters.pop("modified", None) or [None, None])[1]
				for row in site.sources.values():
					if all(row.get(k) == v for k, v in filters.items()):
						if before is None or str(row.get("modified")) < str(before):
							rows.append(row)
			elif doctype == "File":
				rows = [
					row
					for row in site.file_rows.values()
					if all(
						row.get(k) != v[1] if isinstance(v, list) else row.get(k) == v
						for k, v in filters.items()
					)
				]
			if pluck:
				return [row.get(pluck) for row in rows]
			return [{f: row.get(f) for f in (fields or ["name"])} for row in rows]

		frappe.get_all = get_all
		frappe.get_roles = lambda user=None: list(site.roles)
		frappe.enqueue = lambda method, **kwargs: site.enqueued.append((method, kwargs))
		frappe.delete_doc = lambda doctype, name, ignore_permissions=False: site.deleted.append(
			(doctype, name)
		)

		class ThrowError(Exception):
			pass

		def throw(message, exc=None, title=None):
			raise (exc if isinstance(exc, type) and issubclass(exc, Exception) else ThrowError)(message)

		frappe.ThrowError = ThrowError
		frappe.TimestampMismatchError = type("TimestampMismatchError", (Exception,), {})
		frappe.throw = throw
		frappe.PermissionError = type("PermissionError", (Exception,), {})
		frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
		frappe.whitelist = lambda **kw: (lambda fn: fn)
		model = types.ModuleType("frappe.model")
		model.__path__ = []
		document = types.ModuleType("frappe.model.document")

		class Document:
			def __init__(self, values=None, before=None, new=False):
				self.__dict__.update(values or {})
				self.flags = _Flags()
				self._before = before
				self._new = new

			def get(self, key, default=None):
				return self.__dict__.get(key, default)

			def set(self, key, value):
				self.__dict__[key] = value

			def is_new(self):
				return self._new

			def get_doc_before_save(self):
				return self._before

		document.Document = Document
		model.document = document
		frappe.model = model
		return frappe, {
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.document": document,
		}


@pytest.fixture
def site(monkeypatch):
	"""A fake frappe in ``sys.modules`` for one test, with ``sources`` imported fresh over it."""
	fake = FakeSite()
	frappe, modules = fake.module()
	for name, module in modules.items():
		monkeypatch.setitem(sys.modules, name, module)
	for name in (
		"erpnext_enhancements.knowledge_base.sources",
		"erpnext_enhancements.knowledge_base.content",
		"erpnext_enhancements.knowledge_base.doctype.knowledge_source.knowledge_source",
		"erpnext_enhancements.api.knowledge_base_sources",
		"erpnext_enhancements.knowledge_base.source_tools",
	):
		monkeypatch.delitem(sys.modules, name, raising=False)
	fake.sources_module = importlib.import_module("erpnext_enhancements.knowledge_base.sources")
	# Imported fresh too, so the package attribute that `from ... import source_tools` reads is this
	# test's module, bound to this test's fake frappe, never an earlier test's.
	importlib.import_module("erpnext_enhancements.knowledge_base.source_tools")
	fake.frappe = frappe
	return fake


def add_source(site, name="KBS-00001", **values):
	row = {
		"name": name,
		"title": "",
		"source_type": "Link",
		"url": "https://example.com/manual",
		"source_file": "",
		"extraction_state": "Pending",
		"summary": "",
		"summary_by_ai": 0,
		"title_from_source": 0,
		"keywords": "",
		"snapshot_file": "",
	}
	row.update(values)
	site.sources[name] = row
	return row


def gemini(monkeypatch, sources, answer=None, error=None):
	"""Replace the Gemini call; returns the list of prompts it was given."""
	calls = []

	def fake(prompt, system_instruction, settings, **kwargs):
		calls.append((prompt, kwargs))
		if error:
			raise error
		return answer, ""

	module = types.ModuleType("erpnext_enhancements.api.gemini")
	module.generate_content_with_vertex_ai = fake
	monkeypatch.setitem(sys.modules, "erpnext_enhancements.api.gemini", module)
	return calls


def fetched(monkeypatch, sources, body, content_type="text/html", final="https://example.com/manual"):
	monkeypatch.setattr(
		sources.source_fetch,
		"fetch",
		lambda url: source_fetch.Fetched(body, content_type, final, "manual.html"),
	)


SUMMARY = json.dumps({"summary": "A pump manual.", "keywords": ["pump", "manual", "PUMP"], "text": ""})


def test_a_link_is_read_kept_and_summarized(site, monkeypatch):
	sources = site.sources_module
	add_source(site)
	fetched(monkeypatch, sources, b"<title>Pump Manual</title><p>Prime the pump before start.</p>")
	calls = gemini(monkeypatch, sources, SUMMARY)
	sources.extract_source("KBS-00001")
	row = site.sources["KBS-00001"]
	assert row["extraction_state"] == "Ready"
	assert "Prime the pump before start." in row["extracted_text"]
	assert row["text_chars"] == len(row["extracted_text"])
	assert len(row["content_hash"]) == 64
	assert row["title"] == "Pump Manual"
	assert row["summary"] == "A pump manual." and row["summary_by_ai"] == 1
	assert row["keywords"] == "pump, manual"
	assert row["snapshot_file"].startswith("/private/files/")
	(saved_file,) = site.files
	assert saved_file["is_private"] == 1
	assert saved_file["attached_to_doctype"] == K.SOURCE_DOCTYPE
	assert saved_file["attached_to_field"] == "snapshot_file"
	((prompt, kwargs),) = calls
	assert "Prime the pump" in prompt
	assert kwargs["response_mime_type"] == "application/json"
	assert site.saved[-1][2].get(sources.JOB_FLAG) is True


def test_secret_shaped_text_is_never_stored_kept_or_sent_to_gemini(site, monkeypatch):
	sources = site.sources_module
	add_source(site)
	secret = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"
	fetched(monkeypatch, sources, f"<p>Our key is {secret}</p>".encode())
	calls = gemini(monkeypatch, sources, SUMMARY)
	sources.extract_source("KBS-00001")
	row = site.sources["KBS-00001"]
	assert row["extraction_state"] == "Refused"
	assert row["extracted_text"] == ""
	assert secret not in json.dumps(row, default=str)
	assert "line 1" in row["extraction_message"]
	assert calls == []
	assert site.files == []


def test_an_unreadable_kind_is_not_supported(site, monkeypatch):
	sources = site.sources_module
	add_source(site)

	def refuse(url):
		raise source_fetch.FetchRefused("unsupported", "The link is a video/mp4 the library cannot read.")

	monkeypatch.setattr(sources.source_fetch, "fetch", refuse)
	sources.extract_source("KBS-00001")
	row = site.sources["KBS-00001"]
	assert row["extraction_state"] == "Not supported"
	assert "video/mp4" in row["extraction_message"]


def test_a_blocked_link_fails_with_its_reason(site, monkeypatch):
	sources = site.sources_module
	add_source(site, url="http://internal.example/")

	def refuse(url):
		raise source_fetch.FetchRefused("blocked", "That link points inside the company's own network.")

	monkeypatch.setattr(sources.source_fetch, "fetch", refuse)
	sources.extract_source("KBS-00001")
	assert site.sources["KBS-00001"]["extraction_state"] == "Failed"


def test_gemini_down_still_leaves_the_text_ready(site, monkeypatch):
	sources = site.sources_module
	add_source(site)
	fetched(monkeypatch, sources, b"<p>Text</p>")
	gemini(monkeypatch, sources, error=RuntimeError("403"))
	sources.extract_source("KBS-00001")
	row = site.sources["KBS-00001"]
	assert row["extraction_state"] == "Ready"
	assert row["summary"] == ""
	assert "Gemini did not answer" in row["extraction_message"]


def test_a_typed_summary_is_never_replaced(site, monkeypatch):
	sources = site.sources_module
	add_source(site, summary="A note a person typed about this manual.", summary_by_ai=0)
	fetched(monkeypatch, sources, b"<p>Text</p>")
	calls = gemini(monkeypatch, sources, SUMMARY)
	sources.extract_source("KBS-00001")
	row = site.sources["KBS-00001"]
	assert row["summary"] == "A note a person typed about this manual."
	assert calls == []


def test_a_gemini_summary_is_written_again_on_a_re_read(site, monkeypatch):
	sources = site.sources_module
	add_source(site, summary="Old AI summary.", summary_by_ai=1)
	fetched(monkeypatch, sources, b"<p>Text</p>")
	gemini(monkeypatch, sources, SUMMARY)
	sources.extract_source("KBS-00001")
	assert site.sources["KBS-00001"]["summary"] == "A pump manual."


def test_a_source_changed_while_it_was_read_is_left_for_its_own_job(site, monkeypatch):
	sources = site.sources_module
	add_source(site)

	def fetch_and_edit(url):
		site.sources["KBS-00001"]["url"] = "https://example.com/other"
		return source_fetch.Fetched(b"<p>old</p>", "text/html", url, "a.html")

	monkeypatch.setattr(sources.source_fetch, "fetch", fetch_and_edit)
	gemini(monkeypatch, sources, SUMMARY)
	sources.extract_source("KBS-00001")
	assert site.sources["KBS-00001"]["extraction_state"] == "Pending"
	assert site.saved == []


def test_an_unexpected_failure_logs_its_type_only(site, monkeypatch):
	sources = site.sources_module
	add_source(site)

	def explode(url):
		raise KeyError("the document's own text")

	monkeypatch.setattr(sources.source_fetch, "fetch", explode)
	sources.extract_source("KBS-00001")
	row = site.sources["KBS-00001"]
	assert row["extraction_state"] == "Failed"
	(log,) = site.error_logs
	assert "KeyError" in log["error"]
	assert "the document's own text" not in json.dumps(log)
	assert log["reference_name"] == "KBS-00001"


def test_a_source_that_is_not_pending_is_not_read(site, monkeypatch):
	sources = site.sources_module
	add_source(site, extraction_state="Ready")
	monkeypatch.setattr(sources.source_fetch, "fetch", lambda url: pytest.fail("read a Ready source"))
	sources.extract_source("KBS-00001")
	sources.extract_source("KBS-99999")


def test_a_picture_is_read_through_gemini(site, monkeypatch):
	sources = site.sources_module
	add_source(site, url="https://example.com/plate.jpg")
	monkeypatch.setattr(
		sources.source_fetch,
		"fetch",
		lambda url: source_fetch.Fetched(b"\xff\xd8jpeg", "image/jpeg", url, "plate.jpg"),
	)
	answer = json.dumps(
		{"summary": "A pump nameplate.", "keywords": ["nameplate"], "text": "MODEL 3656 LH\n3 HP"}
	)
	calls = gemini(monkeypatch, sources, answer)
	sources.extract_source("KBS-00001")
	row = site.sources["KBS-00001"]
	assert row["extraction_state"] == "Ready"
	assert row["extracted_text"] == "MODEL 3656 LH\n3 HP"
	assert row["summary"] == "A pump nameplate."
	((_prompt, kwargs),) = calls
	assert kwargs["images"][0]["mime_type"] == "image/jpeg"


def test_a_picture_gemini_cannot_read_fails(site, monkeypatch):
	sources = site.sources_module
	add_source(site, url="https://example.com/plate.jpg")
	monkeypatch.setattr(
		sources.source_fetch,
		"fetch",
		lambda url: source_fetch.Fetched(b"\xff\xd8", "image/jpeg", url, "p.jpg"),
	)
	gemini(monkeypatch, sources, error=RuntimeError("down"))
	sources.extract_source("KBS-00001")
	assert site.sources["KBS-00001"]["extraction_state"] == "Failed"


def test_an_uploaded_public_file_is_made_private_and_read(site, monkeypatch):
	sources = site.sources_module
	add_source(site, source_type="File", url="", source_file="/files/notes.md")
	site.file_rows["FILE-1"] = {
		"name": "FILE-1",
		"file_url": "/files/notes.md",
		"file_name": "notes.md",
		"file_size": 20,
		"is_private": 0,
		"attached_to_doctype": K.SOURCE_DOCTYPE,
		"attached_to_name": "KBS-00001",
		"_content": "# Notes\nKeep the hopper dry.",
	}
	calls = gemini(monkeypatch, sources, SUMMARY)
	sources.extract_source("KBS-00001")
	row = site.sources["KBS-00001"]
	assert row["extraction_state"] == "Ready"
	assert "Keep the hopper dry." in row["extracted_text"]
	(copy,) = [f for f in site.files if f.get("attached_to_field") == "source_file"]
	assert copy["is_private"] == 1 and copy["attached_to_name"] == "KBS-00001"
	assert row["source_file"] == copy["file_url"]
	assert ("File", "FILE-1") in site.deleted
	assert all(d != "File" for d, _v, _f in site.saved), "the public File itself is never re-saved"
	assert row["snapshot_file"] == ""
	assert calls


def test_parse_summary_cuts_and_checks(site):
	parse = site.sources_module.parse_summary
	assert parse("not json") is None
	assert parse("[1, 2]") is None
	assert parse(json.dumps({"summary": 5, "text": ""})) is None
	long = parse(json.dumps({"summary": "x" * 5000, "keywords": ["a"] * 50 + [7, "b" * 99]}))
	assert len(long["summary"]) == K.SOURCE_SUMMARY_MAX_CHARS
	# Duplicates (any case) once, a non-string dropped, each cut to the keyword limit.
	assert long["keywords"] == ["a", "b" * K.SOURCE_KEYWORD_MAX_CHARS]
	fenced = parse('```json\n{"summary": "Fine."}\n```')
	assert fenced["summary"] == "Fine."


def test_requeue_picks_up_sources_left_pending_and_gives_up_on_old_ones(site):
	sources = site.sources_module
	add_source(site, "KBS-00001", extraction_state="Pending", modified="2026-11-25 11:00:00")
	add_source(site, "KBS-00002", extraction_state="Ready", modified="2026-11-25 08:00:00")
	add_source(site, "KBS-00003", extraction_state="Pending", modified="2026-11-25 11:55:00")
	add_source(site, "KBS-00004", extraction_state="Pending", modified="2026-11-25 08:00:00")
	sources.requeue_pending()
	assert [kwargs["name"] for _method, kwargs in site.enqueued] == ["KBS-00001"]
	((method, kwargs),) = site.enqueued
	assert method == sources.EXTRACT_METHOD
	assert kwargs["deduplicate"] is True
	assert kwargs["job_id"] == "kb-source::KBS-00001::2026-11-25 11:00:00"
	assert kwargs["enqueue_after_commit"] is True
	assert site.sources["KBS-00004"]["extraction_state"] == "Failed"
	assert site.sources["KBS-00004"]["extraction_message"] == sources.GAVE_UP_MESSAGE
	assert site.sources["KBS-00003"]["extraction_state"] == "Pending"


# ------------------------------------------------------------------ the controller and Fetch again


def controller(site):
	return importlib.import_module(
		"erpnext_enhancements.knowledge_base.doctype.knowledge_source.knowledge_source"
	)


def make(site, values, before=None, new=False):
	module = controller(site)
	return module.KnowledgeSource(values, before=before, new=new)


BASE = {
	"name": "KBS-00001",
	"title": "",
	"source_type": "Link",
	"url": " https://example.com/manual ",
	"source_file": "",
	"summary": "",
	"summary_by_ai": 0,
	"keywords": "",
	"notes": "",
	"extraction_state": "Ready",
	"extracted_text": "old text",
	"snapshot_file": "/private/files/old-copy.html",
	"modified": "2026-11-25 10:00:00",
	"title_from_source": 0,
}


def test_a_new_link_is_pending_with_a_default_title(site):
	doc = make(site, dict(BASE), new=True)
	doc.validate()
	assert doc.url == "https://example.com/manual"
	assert doc.extraction_state == "Pending"
	assert doc.extracted_text is None
	assert doc.title == "example.com/manual"
	assert doc.title_from_source == 1


def test_a_bad_link_is_refused_on_save(site):
	doc = make(site, dict(BASE, url="ftp://example.com/x"), new=True)
	with pytest.raises(Exception, match="Only web links"):
		doc.validate()


def test_a_file_source_needs_its_file(site):
	doc = make(site, dict(BASE, source_type="File", url=""), new=True)
	with pytest.raises(Exception, match="Attach the file"):
		doc.validate()


def test_changing_the_link_resets_it_and_drops_only_a_gemini_summary(site):
	ai_before = dict(BASE, url="https://example.com/old", summary="AI wrote this", summary_by_ai=1)
	ai = make(site, dict(BASE, summary="AI wrote this", summary_by_ai=1), before=ai_before)
	ai.validate()
	assert ai.extraction_state == "Pending" and ai.summary == "" and ai.summary_by_ai == 0
	typed = make(
		site, dict(BASE, summary="Typed by a person"), before=dict(BASE, url="https://example.com/old")
	)
	typed.validate()
	assert typed.extraction_state == "Pending" and typed.summary == "Typed by a person"


def test_an_unchanged_source_keeps_what_was_read(site):
	doc = make(
		site,
		dict(BASE, url="https://example.com/manual"),
		before=dict(BASE, url="https://example.com/manual"),
	)
	doc.validate()
	assert doc.extraction_state == "Ready" and doc.extracted_text == "old text"


def test_the_jobs_own_save_never_resets(site):
	doc = make(site, dict(BASE), before=dict(BASE, url="https://example.com/old"))
	doc.flags[site.sources_module.JOB_FLAG] = True
	doc.validate()
	assert doc.extraction_state == "Ready"


def test_a_secret_typed_into_the_notes_is_refused(site):
	secret = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"
	doc = make(site, dict(BASE, notes=f"key {secret}"), new=True)
	with pytest.raises(Exception) as caught:
		doc.validate()
	assert "Why it's here line 1" in str(caught.value)
	assert secret not in str(caught.value)


def test_saving_queues_a_read_only_when_pending_and_not_from_the_job(site):
	doc = make(site, dict(BASE, extraction_state="Pending"))
	doc.on_update()
	assert len(site.enqueued) == 1
	doc.flags[site.sources_module.JOB_FLAG] = True
	doc.on_update()
	make(site, dict(BASE, extraction_state="Ready")).on_update()
	assert len(site.enqueued) == 1


def test_fetch_again_checks_write_and_queues(site):
	api = importlib.import_module("erpnext_enhancements.api.knowledge_base_sources")
	add_source(site, extraction_state="Failed", extraction_message="old")
	checked = []
	original = site.frappe.get_doc

	def get_doc(doctype, name=None):
		doc = original(doctype, name)
		object.__setattr__(doc, "check_permission", lambda ptype: checked.append(ptype))
		return doc

	site.frappe.get_doc = get_doc
	assert api.refetch("KBS-00001") == {"source": "KBS-00001", "extraction_state": "Pending"}
	assert checked == ["write"]
	assert site.sources["KBS-00001"]["extraction_state"] == "Pending"
	assert site.enqueued
	with pytest.raises(Exception):
		api.refetch("KBS-404")


# ------------------------------------------------------------------ the schema and the wiring


def schema():
	return json.loads(SOURCE_JSON.read_text(encoding="utf-8"))


def fields():
	return {f["fieldname"]: f for f in schema()["fields"]}


def test_private_by_construction():
	meta = schema()
	for flag in (
		"has_web_view",
		"allow_guest_to_view",
		"allow_import",
		"make_attachments_public",
		"allow_rename",
	):
		assert meta.get(flag) == 0, flag
	assert not meta.get("show_name_in_global_search")
	assert [f["fieldname"] for f in meta["fields"] if f.get("in_global_search")] == []
	assert meta["module"] == "Knowledge Base"
	assert meta["name"] == K.SOURCE_DOCTYPE


def test_the_exact_permission_matrix():
	rows = sorted(
		(
			row["role"],
			row.get("permlevel", 0),
			tuple(sorted(k for k, v in row.items() if v == 1 and k != "permlevel")),
		)
		for row in schema()["permissions"]
	)
	assert rows == sorted(
		[
			("Desk User", 0, ("print", "read", "report")),
			("KB Author", 0, ("create", "print", "read", "report", "write")),
			("KB Approver", 0, ("create", "delete", "print", "read", "report", "write")),
			("System Manager", 0, ("create", "delete", "print", "read", "report", "write")),
			("Desk User", 1, ("read",)),
			("KB Author", 1, ("read",)),
			("KB Approver", 1, ("read",)),
			("System Manager", 1, ("read",)),
		]
	)
	for row in schema()["permissions"]:
		for right in ("share", "export", "import", "email", "submit", "amend", "cancel", "if_owner"):
			assert not row.get(right), (row["role"], right)
		assert row["role"] not in ("All", "Guest")


def test_what_the_server_reads_is_read_only_at_level_one():
	server_set = {
		"title_from_source",
		"extraction_state",
		"extraction_message",
		"summary_by_ai",
		"fetched_url",
		"fetched_on",
		"snapshot_file",
		"content_type",
		"text_chars",
		"content_hash",
		"extracted_text",
	}
	for name, field in fields().items():
		if field["fieldtype"] in ("Section Break", "Column Break"):
			continue
		if name in server_set:
			assert field.get("permlevel") == 1 and field.get("read_only") == 1, name
		else:
			assert not field.get("permlevel"), name


def test_select_options_are_the_constants():
	f = fields()
	assert tuple(f["source_type"]["options"].split("\n")) == K.SOURCE_TYPES
	assert tuple(f["status"]["options"].split("\n")) == K.SOURCE_STATUSES
	assert tuple(f["extraction_state"]["options"].split("\n")) == K.SOURCE_STATES
	assert tuple(f["department_block"]["options"].split("\n")) == K.DEPARTMENT_BLOCK_SELECT_OPTIONS
	assert f["url"]["length"] == K.SOURCE_MAX_URL_CHARS == f["fetched_url"]["length"]
	assert f["extraction_state"]["default"] == "Pending"


def test_field_order_lists_every_field_once():
	meta = schema()
	assert meta["field_order"] == [f["fieldname"] for f in meta["fields"]]
	assert len(set(meta["field_order"])) == len(meta["field_order"])


def test_files_on_a_source_are_forced_private_but_keep_their_own_delete_rules():
	assert K.SOURCE_DOCTYPE in K.PRIVATE_FILE_DOCTYPES
	assert K.SOURCE_DOCTYPE not in K.KB_DOCTYPES
	assert K.KB_DOCTYPES <= K.PRIVATE_FILE_DOCTYPES
	text = (APP / "knowledge_base" / "files.py").read_text(encoding="utf-8")
	body = text.split("def force_private", 1)[1].split("\ndef ", 1)[0]
	assert "PRIVATE_FILE_DOCTYPES" in body
	delete_rule = text.split("def file_has_permission", 1)[1].split("\ndef ", 1)[0]
	assert "PRIVATE_FILE_DOCTYPES" not in delete_rule


def _hook(name):
	for node in ast.parse(HOOKS.read_text(encoding="utf-8")).body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
			return ast.literal_eval(node.value)
	return None


def test_the_hooks():
	doctype_js = _hook("doctype_js")
	assert doctype_js[K.SOURCE_DOCTYPE] == "public/js/knowledge_base/knowledge_source.js"
	assert (APP / doctype_js[K.SOURCE_DOCTYPE]).exists()
	assert not (APP / "knowledge_base" / "doctype" / "knowledge_source" / "knowledge_source.js").exists()
	assert (
		"erpnext_enhancements.knowledge_base.sources.requeue_pending" in _hook("scheduler_events")["hourly"]
	)


def test_the_form_script_shares_no_top_level_name_with_the_article_scripts():
	ours = (APP / "public" / "js" / "knowledge_base" / "knowledge_source.js").read_text(encoding="utf-8")
	import re

	declared = set(re.findall(r"^(?:const|let|var|function)\s+([A-Za-z_$][\w$]*)", ours, re.MULTILINE))
	assert declared and all(name.lower().startswith("kbs_") for name in declared), declared
	assert "knowledge_base_sources.refetch" in ours and 'type: "POST"' in ours


def test_the_library_never_names_the_drafts():
	for path in (
		"sources.py",
		"source_fetch.py",
		"source_text.py",
		"source_tools.py",
		"doctype/knowledge_source/knowledge_source.py",
	):
		text = (APP / "knowledge_base" / path).read_text(encoding="utf-8")
		assert K.VERSION_DOCTYPE not in text, path


# ------------------------------------------------------------------ the AI tools (PR B)


def tools(site):
	return importlib.import_module("erpnext_enhancements.knowledge_base.source_tools")


def library(site):
	add_source(
		site,
		"KBS-00001",
		title="Goulds 3656 pump manual",
		keywords="pump, Goulds, 3656",
		summary="The manufacturer's manual for the 3656 end-suction pump.",
		extracted_text="Prime the pump before starting. Check the mechanical seal for leaks.",
		extraction_state="Ready",
		status="Active",
		department_block="06 Operations",
		url="https://example.com/goulds.pdf",
	)
	add_source(
		site,
		"KBS-00002",
		title="Old pump catalog",
		summary="A pump catalog that was archived.",
		extracted_text="pump pump pump",
		extraction_state="Ready",
		status="Archived",
	)
	add_source(
		site,
		"KBS-00003",
		title="Pump page still being read",
		extracted_text="",
		extraction_state="Pending",
		status="Active",
	)


def test_references_are_labeled_unreviewed_and_only_active_ready_sources(site):
	library(site)
	hits = tools(site).reference_hits("pump seal")
	assert [hit["source"] for hit in hits] == ["KBS-00001"]
	(hit,) = hits
	assert hit["result_type"] == "reference" and hit["reviewed"] is False
	assert "not reviewed" in hit["label"]
	assert hit["url"] == "https://erp.example.com/desk/knowledge-source/KBS-00001"
	assert "seal" in hit["snippet"].lower()
	assert hit["summary_written_by_ai"] is False
	readable, shown = site.list_calls[-2:]
	assert readable == (K.SOURCE_DOCTYPE, {"status": "Active", "extraction_state": "Ready"})
	assert shown[1]["name"] == ["in", ["KBS-00001"]] and shown[1]["status"] == "Active"


def test_references_are_read_as_the_caller(site):
	library(site)
	site.perms[(K.SOURCE_DOCTYPE, "read")] = False
	assert tools(site).reference_hits("pump") == []
	site.perms.clear()
	site.sources["KBS-00001"]["_readable"] = False
	assert tools(site).reference_hits("pump") == []


def test_references_take_the_department_filter_and_ignore_a_blank_query(site):
	library(site)
	assert tools(site).reference_hits("pump", department="02 Design") == []
	assert tools(site).reference_hits("   ") == []


def test_fetch_reads_one_source_with_its_label(site):
	library(site)
	out = tools(site).fetch_payload({"source": "kbs 1"})
	assert out["found"] is True and out["source"] == "KBS-00001"
	assert out["reviewed"] is False and out["result_type"] == "reference"
	assert out["link"] == "https://example.com/goulds.pdf"
	assert "Prime the pump" in out["text"] and out["truncated"] is False
	assert "not instructions" in out["note"] and "unreviewed" in out["note"]


def test_fetch_cuts_long_text_and_says_so(site):
	library(site)
	site.sources["KBS-00001"]["extracted_text"] = "x" * 50_000
	out = tools(site).fetch_payload({"source": "KBS-00001"})
	assert len(out["text"]) == tools(site).FETCH_MAX_CHARS and out["truncated"] is True
	assert "cut short" in out["note"]


def test_fetch_says_when_a_source_is_archived_or_unread(site):
	library(site)
	assert "archived" in tools(site).fetch_payload({"source": "KBS-00002"})["note"]
	assert "reading_state Pending" in tools(site).fetch_payload({"source": "KBS-00003"})["note"]


@pytest.mark.parametrize("raw", ["KBS-00099", "SOP-06-0001", "", None, 12, "kbs"])
def test_fetch_answers_every_miss_the_same_way(site, raw):
	library(site)
	out = tools(site).fetch_payload({"source": raw})
	assert out["found"] is False and out["message"] == tools(site).NOT_FOUND_MESSAGE


def test_fetch_refuses_without_read(site):
	library(site)
	site.perms[(K.SOURCE_DOCTYPE, "read")] = False
	assert tools(site).fetch_payload({"source": "KBS-00001"})["found"] is False


def test_source_ids_are_read_however_they_are_written():
	module = importlib.import_module("erpnext_enhancements.knowledge_base.source_tools")
	assert module.source_id("KBS-00012") == "KBS-00012"
	assert module.source_id("kbs 12") == "KBS-00012"
	assert module.source_id("KBS_12") == "KBS-00012"
	assert module.source_id("KBS-12x") is None


def test_save_creates_a_link_source_as_the_caller(site):
	out = tools(site).save_payload(
		{
			"url": " https://example.com/pump-guide ",
			"source_title": "  Pump   guide ",
			"why_saved": "For the Valley job",
			"department": "06",
		}
	)
	assert out["success"] is True and out["already_saved"] is False
	(row,) = site.inserted
	assert row["source_type"] == "Link" and row["url"] == "https://example.com/pump-guide"
	assert row["title"] == "Pump guide" and row["notes"] == "For the Valley job"
	assert row["department_block"] == "06 Operations"
	assert out["reading_state"] == "Pending"
	assert out["url"].endswith("/desk/knowledge-source/" + out["source"])


def test_save_returns_a_link_already_held(site):
	library(site)
	out = tools(site).save_payload({"url": "https://example.com/goulds.pdf"})
	assert out == {
		"success": True,
		"source": "KBS-00001",
		"url": "https://erp.example.com/desk/knowledge-source/KBS-00001",
		"reading_state": "Ready",
		"already_saved": True,
		"note": "The library already holds this link; nothing new was saved.",
	}
	assert site.inserted == []


@pytest.mark.parametrize(
	"args,fragment",
	[
		({"url": "ftp://example.com/x"}, "Only web links"),
		({"url": ""}, "Enter the link"),
		({"url": "https://example.com/", "department": "Accounting-ish"}, "department must be one of"),
	],
)
def test_save_refuses_a_bad_link_or_department_before_writing(site, args, fragment):
	out = tools(site).save_payload(args)
	assert out["success"] is False and fragment in out["error"]
	assert site.inserted == []


def test_save_refuses_without_create(site):
	site.perms[(K.SOURCE_DOCTYPE, "create")] = False
	out = tools(site).save_payload({"url": "https://example.com/"})
	assert out["success"] is False and "KB Author" in out["error"]
	assert site.inserted == []


def test_search_payload_puts_references_after_articles_and_never_with_a_kind(site, monkeypatch):
	library(site)
	ai_tools_name = "erpnext_enhancements.knowledge_base.ai_tools"
	search_service = types.ModuleType("erpnext_enhancements.knowledge_base.search_service")
	search_service.search = lambda query, department=None, kind=None, limit=5: {"results": [], "problems": []}
	search_service.read_filters = lambda department=None, kind=None: (department or None, None, [])
	markdown = types.ModuleType("erpnext_enhancements.knowledge_base.markdown")
	site.frappe.utils.getdate = lambda value: value
	site.frappe.utils.nowdate = lambda: "2026-11-25"
	monkeypatch.setitem(sys.modules, "erpnext_enhancements.knowledge_base.search_service", search_service)
	monkeypatch.setitem(sys.modules, "erpnext_enhancements.knowledge_base.markdown", markdown)
	monkeypatch.delitem(sys.modules, ai_tools_name, raising=False)
	package = importlib.import_module("erpnext_enhancements.knowledge_base")
	monkeypatch.setattr(package, "search_service", search_service, raising=False)
	monkeypatch.setattr(package, "markdown", markdown, raising=False)
	ai_tools = importlib.import_module(ai_tools_name)
	out = ai_tools.search_payload({"query": "pump seal"})
	assert out["results"] == [] and out["reference_count"] == 1
	assert out["references"][0]["source"] == "KBS-00001"
	assert out["note"].startswith(ai_tools.NO_MATCH_NOTE) and "unreviewed" in out["note"]
	assert ai_tools.search_payload({"query": "pump seal", "kind": "SOP"})["references"] == []
	assert ai_tools.search_payload({"query": "pump seal", "include_reference": False})["references"] == []

	def broken(*args, **kwargs):
		raise RuntimeError("index")

	monkeypatch.setattr(ai_tools.source_tools, "reference_hits", broken)
	failed = ai_tools.search_payload({"query": "pump"})
	assert failed["references"] == [] and ai_tools.REFERENCES_UNAVAILABLE in failed["problems"]
	monkeypatch.delitem(sys.modules, ai_tools_name, raising=False)


def test_the_tools_are_classified_and_the_card_shows_the_link(site, monkeypatch):
	monkeypatch.delitem(sys.modules, "erpnext_enhancements.assistant_tools._gate", raising=False)
	gate = importlib.import_module("erpnext_enhancements.assistant_tools._gate")
	assert "fetch_knowledge_source" in gate.EXPLICIT_READONLY
	assert "save_knowledge_source" in gate.APP_MUTATING and "save_knowledge_source" in gate.LOW_RISK
	assert "save_knowledge_source" not in gate.EXPLICIT_READONLY
	line = gate.summarize_tool_call("save_knowledge_source", {"url": "https://example.com/pump"})
	assert line == "Save link to the reference library: https://example.com/pump"
	long = gate.summarize_tool_call("save_knowledge_source", {"url": "https://example.com/" + "a" * 200})
	assert long.endswith("…") and len(long) < 140
	monkeypatch.delitem(sys.modules, "erpnext_enhancements.assistant_tools._gate", raising=False)


def test_the_tools_are_registered_and_search_names_the_references():
	tools_hook = _hook("assistant_tools")
	for path in (
		"erpnext_enhancements.assistant_tools.save_knowledge_source.SaveKnowledgeSource",
		"erpnext_enhancements.assistant_tools.fetch_knowledge_source.FetchKnowledgeSource",
	):
		assert path in tools_hook
	search = (APP / "assistant_tools" / "search_company_knowledge.py").read_text(encoding="utf-8")
	assert "include_reference" in search and "unreviewed" in search
	save = (APP / "assistant_tools" / "save_knowledge_source.py").read_text(encoding="utf-8")
	assert '"source_title"' in save and '"title"' not in save


# ------------------------------------------------------------------ review fixes (2026-11-25)


def test_a_file_not_attached_to_this_source_is_never_read(site, monkeypatch):
	"""Another record's private file, named by URL, is not this source's file."""
	sources = site.sources_module
	add_source(site, source_type="File", url="", source_file="/private/files/someone-elses.pdf")
	site.file_rows["FILE-9"] = {
		"name": "FILE-9",
		"file_url": "/private/files/someone-elses.pdf",
		"file_name": "someone-elses.pdf",
		"is_private": 1,
		"attached_to_doctype": "Employee",
		"attached_to_name": "HR-EMP-00001",
		"_content": b"private text that must never be published",
	}
	calls = gemini(monkeypatch, sources, SUMMARY)
	sources.extract_source("KBS-00001")
	row = site.sources["KBS-00001"]
	assert row["extraction_state"] == "Failed"
	assert "could not be found on this source" in row["extraction_message"]
	assert "private text" not in json.dumps(row, default=str)
	assert calls == []


@pytest.mark.parametrize(
	"value",
	[
		"https://example.com/x.pdf",
		"/private/files/../site_config.json",
		"private/files/x.pdf",
		"/api/method/x",
	],
)
def test_a_file_source_must_point_at_this_sites_files(site, value):
	doc = make(site, dict(BASE, source_type="File", url="", source_file=value), new=True)
	with pytest.raises(Exception, match="Attach the file with the Attach button"):
		doc.validate()


def test_a_file_source_clears_its_link_and_a_link_source_its_file(site):
	file_doc = make(site, dict(BASE, source_type="File", source_file="/private/files/a.pdf"), new=True)
	file_doc.validate()
	assert file_doc.url == ""
	link_doc = make(site, dict(BASE, source_file="/private/files/a.pdf"), new=True)
	link_doc.validate()
	assert link_doc.source_file == ""


def test_only_a_kb_approver_saves_a_google_link(site):
	google = "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit"
	author = make(site, dict(BASE, url=google), new=True)
	with pytest.raises(Exception, match="Only a KB Approver"):
		author.validate()
	site.roles = ["KB Approver"]
	make(site, dict(BASE, url=google), new=True).validate()


def test_an_unchanged_google_link_is_not_checked_again(site):
	google = "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit"
	doc = make(site, dict(BASE, url=google, summary="edited"), before=dict(BASE, url=google))
	doc.validate()
	assert doc.extraction_state == "Ready"


def test_a_person_editing_a_gemini_summary_makes_it_theirs(site):
	before = dict(BASE, url="https://example.com/manual", summary="Gemini's words", summary_by_ai=1)
	doc = make(site, dict(before, summary="A person's correction"), before=before)
	doc.validate()
	assert doc.summary_by_ai == 0 and doc.summary == "A person's correction"


def test_a_new_link_drops_the_old_saved_copy(site):
	doc = make(site, dict(BASE), before=dict(BASE, url="https://example.com/old"))
	doc.validate()
	assert doc.snapshot_file is None


def test_a_picture_too_large_for_gemini_says_so(site, monkeypatch):
	sources = site.sources_module
	add_source(site, url="https://example.com/plate.jpg")
	monkeypatch.setattr(sources, "MAX_IMAGE_BYTES", 10)
	monkeypatch.setattr(
		sources.source_fetch,
		"fetch",
		lambda url: source_fetch.Fetched(b"\xff\xd8" * 20, "image/jpeg", url, "p.jpg"),
	)
	calls = gemini(monkeypatch, sources, SUMMARY)
	sources.extract_source("KBS-00001")
	row = site.sources["KBS-00001"]
	assert row["extraction_state"] == "Failed" and "larger than" in row["extraction_message"]
	assert calls == []


def test_a_store_that_fails_marks_the_source_failed_not_pending(site, monkeypatch):
	sources = site.sources_module
	add_source(site)
	fetched(monkeypatch, sources, b"<p>Text</p>")
	gemini(monkeypatch, sources, SUMMARY)

	def explode(*args, **kwargs):
		raise RuntimeError("save failed")

	monkeypatch.setattr(sources, "_store", explode)
	sources.extract_source("KBS-00001")
	row = site.sources["KBS-00001"]
	assert row["extraction_state"] == "Failed" and row["extraction_message"] == sources.STORE_FAILED_MESSAGE
	assert site.rollbacks == 1
	assert "RuntimeError" in site.error_logs[-1]["error"]


def test_the_address_read_from_is_stored_quoted(site, monkeypatch):
	sources = site.sources_module
	add_source(site)
	fetched(monkeypatch, sources, b"<p>Text</p>", final="https://example.com/café")
	gemini(monkeypatch, sources, SUMMARY)
	sources.extract_source("KBS-00001")
	assert site.sources["KBS-00001"]["fetched_url"] == "https://example.com/caf%C3%A9"


def test_a_new_copy_replaces_every_older_copy(site, monkeypatch):
	sources = site.sources_module
	add_source(site, snapshot_file="/private/files/old.html")
	site.file_rows["FILE-OLD"] = {
		"name": "FILE-OLD",
		"file_url": "/private/files/old.html",
		"attached_to_doctype": K.SOURCE_DOCTYPE,
		"attached_to_name": "KBS-00001",
		"attached_to_field": "snapshot_file",
	}
	fetched(monkeypatch, sources, b"<p>New</p>")
	gemini(monkeypatch, sources, SUMMARY)
	sources.extract_source("KBS-00001")
	assert ("File", "FILE-OLD") in site.deleted


def _url_password_pattern():
	from erpnext_enhancements.knowledge_base import content

	text = (APP / "knowledge_base" / "content.py").read_text(encoding="utf-8")
	line = next(ln for ln in text.splitlines() if "://[^" in ln and "@" in ln)
	return content, line


def test_the_url_password_scheme_is_bounded_and_possessive():
	"""Structural: the scheme part may not use an unbounded quantifier, which is what made the old
	pattern slow; a bounded possessive one stays linear."""
	_content, line = _url_password_pattern()
	scheme = line.split("://", 1)[0]
	assert "{0,31}+" in scheme
	assert "*" not in scheme and "+[" not in scheme.replace("{0,31}+", "")


def test_the_url_password_scan_is_quick_on_long_ordinary_text():
	import time

	content, _line = _url_password_pattern()
	prose = " ".join(f"word{i} and-another.term" for i in range(20_000))
	start = time.perf_counter()
	content.secret_findings(prose)
	assert time.perf_counter() - start < 1.0


@pytest.mark.parametrize(
	"text,found",
	[
		("see https://" + "admin:" + "hunter2" + "@erp.example.com/ now", True),
		("--https://" + "u:" + "pw" + "@h", True),
		("(-ftp://" + "u:" + "pw" + "@h)", True),
		("x.git://" + "u:" + "pw" + "@h", True),
		("git+ssh://" + "u:" + "pw" + "@h", True),
		("_https://" + "u:" + "pw" + "@h", False),
		("see http://host/path", False),
	],
)
def test_the_url_password_scan_keeps_the_old_matches(text, found):
	content, _line = _url_password_pattern()
	kinds = [f.kind for f in content.secret_findings(text)]
	assert ("a password in a web address" in kinds) is found


def test_word_documents_are_capped_lower():
	assert source_text.MAX_DOCX_XML_BYTES <= 10 * 1024 * 1024


def test_the_reference_index_is_built_once_and_rebuilt_when_the_library_changes(site):
	library(site)
	module = tools(site)
	module.reference_hits("pump")
	module.reference_hits("seal")
	corpus = [q for q in site.sql_calls if "left(extracted_text" in q]
	assert len(corpus) == 1
	site.sources["KBS-00001"]["modified"] = "2026-11-25 11:59:00"
	module.reference_hits("pump")
	assert len([q for q in site.sql_calls if "left(extracted_text" in q]) == 2


def test_the_index_reads_text_cut_by_the_database(site):
	library(site)
	site.sources["KBS-00001"]["extracted_text"] = "pump " + "x" * 100_000 + " zebrafinch"
	module = tools(site)
	assert module.reference_hits("zebrafinch") == []
	assert [hit["source"] for hit in module.reference_hits("pump")] == ["KBS-00001"]


def test_a_source_the_caller_cannot_read_takes_no_slot(site):
	library(site)
	add_source(
		site,
		"KBS-00009",
		title="Pump seal pump seal",
		extracted_text="pump seal pump seal pump seal",
		extraction_state="Ready",
		status="Active",
		_readable=False,
	)
	hits = tools(site).reference_hits("pump seal", limit=1)
	assert [hit["source"] for hit in hits] == ["KBS-00001"]


def test_a_gemini_summary_is_marked_in_references(site):
	library(site)
	site.sources["KBS-00001"]["summary_by_ai"] = 1
	(hit,) = tools(site).reference_hits("seal")
	assert hit["summary_written_by_ai"] is True


@pytest.mark.parametrize("value", [False, 0, "false", "False", "0", "no", "off"])
def test_include_reference_switched_off_however_it_is_sent(value):
	sys_modules_ai = importlib.import_module("erpnext_enhancements.knowledge_base.search")
	assert sys_modules_ai  # the engine imports without frappe
	source = (APP / "knowledge_base" / "ai_tools.py").read_text(encoding="utf-8")
	body = source.split("def _off(value):", 1)[1].split("\ndef ", 1)[0]
	namespace = {}
	exec("def _off(value):" + body, namespace)
	assert namespace["_off"](value) is True


@pytest.mark.parametrize("value", [None, True, 1, "true", "yes", ""])
def test_include_reference_stays_on_otherwise(value):
	source = (APP / "knowledge_base" / "ai_tools.py").read_text(encoding="utf-8")
	body = source.split("def _off(value):", 1)[1].split("\ndef ", 1)[0]
	namespace = {}
	exec("def _off(value):" + body, namespace)
	assert namespace["_off"](value) is False


def test_the_library_is_never_exempt_from_a_card(site, monkeypatch):
	monkeypatch.delitem(sys.modules, "erpnext_enhancements.assistant_tools._gate", raising=False)
	gate = importlib.import_module("erpnext_enhancements.assistant_tools._gate")
	assert K.SOURCE_DOCTYPE in gate.NEVER_EXEMPT
	assert gate.TOOL_TARGET_DOCTYPES["save_knowledge_source"] == K.SOURCE_DOCTYPE
	monkeypatch.delitem(sys.modules, "erpnext_enhancements.assistant_tools._gate", raising=False)


def test_without_urllib3_the_host_check_has_nothing_to_disagree_with(monkeypatch):
	monkeypatch.setitem(sys.modules, "urllib3.util", None)
	assert source_fetch.url_problem("https://example.com/page") is None
	assert "cannot hold" in source_fetch.url_problem("https://example.com/a|b")


# ------------------------------------------------------------------ round-2 review fixes


def test_a_compressed_answer_is_refused_unread():
	packed = FakeResponse(headers={"Content-Type": "text/html", "Content-Encoding": "gzip"}, body=b"\x1f\x8b")
	session = FakeSession({"https://example.com/a": packed})
	with pytest.raises(source_fetch.FetchRefused) as caught:
		source_fetch.fetch("https://example.com/a", session=session, resolve=PUBLIC)
	assert caught.value.kind == "unsupported" and "compressed" in caught.value.message
	assert packed.read is False
	((_url, kwargs),) = session.calls
	assert kwargs["headers"]["Accept-Encoding"] == "identity"


def test_an_identity_encoding_is_read():
	plain = FakeResponse(headers={"Content-Type": "text/plain", "Content-Encoding": "identity"}, body=b"ok")
	session = FakeSession({"https://example.com/a": plain})
	assert source_fetch.fetch("https://example.com/a", session=session, resolve=PUBLIC).data == b"ok"


def test_the_guarded_session_asks_for_uncompressed_answers():
	assert source_fetch.guarded_session().headers["Accept-Encoding"] == "identity"


def test_a_percent_sign_in_the_host_is_refused():
	assert "not a web address" in source_fetch.url_problem("http://example%2ecom/")


@pytest.mark.parametrize(
	"address,public",
	[
		("::127.0.0.1", False),
		("::10.0.0.1", False),
		("64:ff9b::7f00:1", False),
		("64:ff9b::a9fe:a9fe", False),
		("2002:7f00:1::1", False),
		("::8.8.8.8", True),
		("64:ff9b::808:808", True),
	],
)
def test_ipv6_forms_carrying_an_ipv4_address_are_judged_by_it(address, public):
	assert source_fetch.address_is_public(address) is public


def test_each_hop_gets_only_the_time_left():
	session = FakeSession(
		{"https://example.com/a": FakeResponse(headers={"Content-Type": "text/plain"}, body=b"ok")}
	)
	source_fetch.fetch("https://example.com/a", session=session, resolve=PUBLIC)
	((_url, kwargs),) = session.calls
	connect, read = kwargs["timeout"]
	assert connect <= source_fetch.TIMEOUT[0] and read <= source_fetch.TIMEOUT[1]


def test_a_site_that_drips_is_cut_off_at_the_deadline_by_the_watchdog(monkeypatch):
	"""A real server that sends a byte every 0.2 s: the read would never time out, and the watchdog
	shuts the connection at the deadline instead."""
	import http.server
	import threading
	import time

	class Drip(http.server.BaseHTTPRequestHandler):
		protocol_version = "HTTP/1.0"

		def do_GET(self):
			self.send_response(200)
			self.send_header("Content-Type", "text/plain")
			self.end_headers()
			try:
				for _ in range(100):
					self.wfile.write(b"x")
					self.wfile.flush()
					time.sleep(0.2)
			except OSError:
				pass

		def log_message(self, *args):
			pass

	httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Drip)
	threading.Thread(target=httpd.serve_forever, daemon=True).start()
	try:
		port = httpd.server_address[1]
		monkeypatch.setattr(source_fetch, "ALLOWED_PORTS", (None, 80, 443, port))
		monkeypatch.setattr(source_fetch, "address_is_public", lambda address: True)
		monkeypatch.setattr(source_fetch, "DEADLINE_SECONDS", 1)
		start = time.monotonic()
		with pytest.raises(source_fetch.FetchRefused) as caught:
			source_fetch.fetch(f"http://127.0.0.1:{port}/drip")
		assert time.monotonic() - start < 5
		assert "too slowly" in caught.value.message
	finally:
		httpd.shutdown()
		httpd.server_close()


def test_the_store_locks_the_row_before_it_reads_it(site, monkeypatch):
	sources = site.sources_module
	add_source(site)
	fetched(monkeypatch, sources, b"<p>Text</p>")
	gemini(monkeypatch, sources, SUMMARY)
	sources.extract_source("KBS-00001")
	assert "KBS-00001" in site.locked


def test_a_save_that_lost_a_race_leaves_the_source_alone(site, monkeypatch):
	sources = site.sources_module
	add_source(site)
	fetched(monkeypatch, sources, b"<p>Text</p>")
	gemini(monkeypatch, sources, SUMMARY)

	def lost(*args, **kwargs):
		raise site.frappe.TimestampMismatchError("changed meanwhile")

	monkeypatch.setattr(sources, "_store", lost)
	sources.extract_source("KBS-00001")
	assert site.sources["KBS-00001"]["extraction_state"] == "Pending"
	assert site.rollbacks == 1 and site.error_logs == []


def test_a_page_title_replaces_the_default_but_never_a_typed_one(site, monkeypatch):
	sources = site.sources_module
	add_source(site, title="example.com/manual", title_from_source=1)
	fetched(monkeypatch, sources, b"<title>Pump Manual</title><p>Text</p>")
	gemini(monkeypatch, sources, SUMMARY)
	sources.extract_source("KBS-00001")
	assert site.sources["KBS-00001"]["title"] == "Pump Manual"
	add_source(site, "KBS-00002", title="Typed by a person", title_from_source=0)
	sources.extract_source("KBS-00002")
	assert site.sources["KBS-00002"]["title"] == "Typed by a person"


def test_a_person_editing_the_title_makes_it_theirs(site):
	before = dict(BASE, url="https://example.com/manual", title="example.com/manual", title_from_source=1)
	doc = make(site, dict(before, title="Pump manual, typed"), before=before)
	doc.validate()
	assert doc.title_from_source == 0 and doc.title == "Pump manual, typed"


def test_a_new_link_drops_a_title_taken_from_the_old_page(site):
	before = dict(BASE, url="https://example.com/old", title="Old Page", title_from_source=1)
	doc = make(site, dict(before, url="https://example.com/manual"), before=before)
	doc.validate()
	assert doc.title == "example.com/manual" and doc.title_from_source == 1


# ------------------------------------------------------------------ round-2 review fixes (PR B)


def test_a_google_link_from_a_kb_author_is_refused_before_any_card(site):
	problems = tools(site).save_problems({"url": "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit"})
	assert any("only a KB Approver" in p for p in problems)
	site.roles = ["KB Approver"]
	assert (
		tools(site).save_problems({"url": "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit"}) == []
	)


def test_a_secret_in_the_note_or_title_is_refused_without_quoting_it(site):
	secret = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"
	problems = tools(site).save_problems(
		{"url": "https://example.com/", "why_saved": f"key {secret}", "source_title": "fine"}
	)
	assert problems and all(secret not in p for p in problems)
	assert any(p.startswith("why_saved line 1") for p in problems)


def test_save_says_why_rather_than_failing_deep_inside(site):
	out = tools(site).save_payload({"url": "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit"})
	assert out["success"] is False and "only a KB Approver" in out["error"]
	assert site.inserted == []


def test_save_checks_types_and_create_permission(site):
	assert "why_saved must be text" in tools(site).save_problems(
		{"url": "https://example.com/", "why_saved": 5}
	)
	site.perms[(K.SOURCE_DOCTYPE, "create")] = False
	assert any("cannot add" in p for p in tools(site).save_problems({"url": "https://example.com/"}))


def test_references_and_fetch_say_where_the_title_came_from(site):
	library(site)
	site.sources["KBS-00001"]["title_from_source"] = 1
	(hit,) = tools(site).reference_hits("seal")
	assert hit["title_from_source"] is True
	assert tools(site).fetch_payload({"source": "KBS-00001"})["title_from_source"] is True


def test_the_save_tool_is_prechecked_and_its_refused_rows_keep_no_text(site, monkeypatch):
	monkeypatch.delitem(sys.modules, "erpnext_enhancements.assistant_tools._gate", raising=False)
	gate = importlib.import_module("erpnext_enhancements.assistant_tools._gate")
	assert "save_knowledge_source" in gate.APP_PRECHECKED_TOOLS
	assert set(gate.WITHHELD_WHEN_UNQUEUED["save_knowledge_source"]) == {"url", "source_title", "why_saved"}
	assert gate.KEPT_WHEN_UNQUEUED["save_knowledge_source"] == ("department",)
	monkeypatch.delitem(sys.modules, "erpnext_enhancements.assistant_tools._gate", raising=False)
	tool_source = (APP / "assistant_tools" / "save_knowledge_source.py").read_text(encoding="utf-8")
	assert "def precheck" in tool_source and "save_problems" in tool_source
