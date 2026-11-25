"""Fetch a link for the reference library without letting the server reach anything internal.

Nik, 2026-11-25: "submit links for the AI to fetch and save the articles information to ERPNext".
The fetch runs **on the production server**, inside Google Cloud, so a URL is an instruction to that
server to make a request. Without care, a link could have ERPNext read its own infrastructure (the
cloud's metadata service, the bench itself, a private address) and store the answer as a "source"
every staff user can open. That is a server-side request forgery, and this module is what stops it.

**The rules:**

- **A link must be well formed.** ``http`` or ``https`` only, on the default port, with no user name
  or password, an ASCII host, and no character outside RFC 3986 (:data:`_FORBIDDEN`). Two parsers
  must also agree on the host: the standard library's and urllib3's, which is the one that connects.
  HTTP libraries disagree about malformed URLs, so a malformed one is refused, never interpreted.
- **The address is checked at connect time, before anything is sent.** :func:`guarded_session` mounts
  connection classes whose ``_new_conn`` checks the socket's peer address against
  :func:`address_is_public` the moment the TCP connection opens, and closes it otherwise. So whatever
  address a name resolves to when the connection is made, and whichever host the connecting library
  settled on, nothing reaches an internal address: the check is on the socket, not on a lookup made
  earlier. ``is_global`` refuses loopback, private, link-local, carrier-grade NAT, multicast, reserved
  and unspecified addresses and their IPv6 counterparts, and an IPv4 address written as IPv6 is judged
  as the IPv4 address it is.
- **The host's addresses are also checked before connecting** (:func:`resolve_public`), so an internal
  link gets a clear message instead of a connection error.
- **Redirects are followed by this code**, at most five, and each hop is checked again from the top.
- **Nothing of the server's leaves with a request**: no cookies (the session's cookie jar accepts none,
  and is cleared between hops), no ``Authorization``, no proxy from the environment.
- **Bounded**: at most :data:`constants.SOURCE_MAX_BYTES`, within :data:`DEADLINE_SECONDS` of wall
  clock in all, and only the content types the library reads (:data:`ALLOWED_TYPES`), refused before
  the body is downloaded.

Google Docs and Drive links are not fetched here: :func:`google_file` recognizes them and
``sources.py`` reads them through the Drive API, for KB Approvers only.

Standard library, plus ``requests`` and ``urllib3`` imported where they are used, so the rules are
tested bench-free (``tests/test_knowledge_base_sources.py``, which runs the guarded session against a
real local server).
"""

import ipaddress
import re
import socket
import time
from collections import namedtuple
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from erpnext_enhancements.knowledge_base import constants

#: What a fetch returns: the body, its content type (lower case, no parameters), the address that
#: answered after redirects, and a file name for the saved copy.
Fetched = namedtuple("Fetched", "data content_type final_url filename")

#: The content types the library reads (``source_text.kind_of``). Anything else is refused before
#: its body is downloaded, as "Not supported".
ALLOWED_TYPES = frozenset(
	{
		"text/html",
		"application/xhtml+xml",
		"text/plain",
		"text/markdown",
		"text/x-markdown",
		"text/csv",
		"application/pdf",
		"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
		"image/png",
		"image/jpeg",
		"image/webp",
	}
)

#: The ports a link may name: none (the scheme's default), 80 or 443.
ALLOWED_PORTS = (None, 80, 443)

#: The most redirects followed for one link.
MAX_REDIRECTS = 5

#: Seconds to connect, and to wait between bytes once connected.
TIMEOUT = (5, 20)

#: The most seconds one fetch may take in all, redirects and download included.
DEADLINE_SECONDS = 60

#: Sent with every request. Plain, honest, and no cookies or credentials of any kind.
HEADERS = {
	"User-Agent": "SapphireFountains-KnowledgeBase/1.0 (+reference library)",
	"Accept": "text/html,application/xhtml+xml,application/pdf,text/plain,*/*;q=0.5",
}

BLOCKED_MESSAGE = (
	"That link points inside the company's own network or servers, which the reference library never "
	"reads. Save a public web page, or upload the file instead."
)

#: Characters RFC 3986 does not allow anywhere in a URI, plus every control character.
_FORBIDDEN = re.compile(r'[\\"<>^`{|}\x00-\x20\x7f]')

_CHUNK = 64 * 1024

#: The clock the deadline reads (a module attribute, so a test can move time).
_clock = time.monotonic

#: Google Docs, Sheets, Slides and Drive file links, and the id in each.
_GOOGLE_PATH = re.compile(r"^/(document|spreadsheets|presentation|file)/(?:u/\d+/)?d/([A-Za-z0-9_-]{10,})")


class FetchRefused(Exception):
	"""A link the library will not read, with a message for the person who saved it.

	``kind`` is ``"blocked"`` (an address it must not reach), ``"unsupported"`` (a kind of content it
	cannot read), ``"too_large"``, ``"http"`` (the site answered with an error) or ``"network"``.
	"""

	def __init__(self, kind, message):
		super().__init__(message)
		self.kind = kind
		self.message = message


def url_problem(url):
	"""Why ``url`` cannot be saved as a link, in a sentence, or ``None``. Syntax only: no lookup.

	The form calls this on save, so a typo is refused at once; :func:`fetch` calls it on every hop.
	"""
	if not isinstance(url, str) or not url.strip():
		return "Enter the link to save."
	url = url.strip()
	if len(url) > constants.SOURCE_MAX_URL_CHARS:
		return f"The link is longer than {constants.SOURCE_MAX_URL_CHARS} characters."
	if _FORBIDDEN.search(url):
		return (
			"The link has a space or a character a web address cannot hold. Copy it from the "
			"browser's address bar."
		)
	try:
		parts = urlsplit(url)
		port = parts.port
	except ValueError:
		return "That is not a web address. Copy it from the browser's address bar."
	if parts.scheme.lower() not in ("http", "https"):
		return "Only web links (starting https:// or http://) can be saved."
	if not parts.hostname:
		return "That is not a web address. Copy it from the browser's address bar."
	if not parts.hostname.isascii():
		return "The site's name has letters a web address cannot hold as typed. Copy the link from the browser's address bar."
	if parts.username or parts.password or "@" in parts.netloc:
		return "The link has a user name or password in it. Save the link without it."
	if port not in ALLOWED_PORTS:
		return "Only links on the standard web ports can be saved."
	if _connecting_host(url) != parts.hostname.lower():
		return "That is not a web address. Copy it from the browser's address bar."
	return None


def _connecting_host(url):
	"""The host urllib3, the library that connects, reads from ``url``, lower case and unbracketed."""
	try:
		from urllib3.util import parse_url

		host = parse_url(url).host or ""
	except Exception:
		return None
	return host.strip("[]").lower()


def google_file(url):
	"""``(kind, file_id)`` for a Google Docs, Sheets, Slides or Drive link, else ``None``.

	``kind`` is ``"document"``, ``"spreadsheets"``, ``"presentation"`` or ``"file"``.
	"""
	try:
		parts = urlsplit((url or "").strip())
	except ValueError:
		return None
	host = (parts.hostname or "").lower()
	if host not in ("docs.google.com", "drive.google.com"):
		return None
	match = _GOOGLE_PATH.match(parts.path or "")
	if match:
		return match.group(1), match.group(2)
	if host == "drive.google.com" and parts.path in ("/open", "/uc"):
		ids = parse_qs(parts.query).get("id") or []
		if ids and re.fullmatch(r"[A-Za-z0-9_-]{10,}", ids[0]):
			return "file", ids[0]
	return None


def address_is_public(address):
	"""Whether ``address`` (an IP address as text) is one the library may connect to."""
	try:
		ip = ipaddress.ip_address(str(address).split("%", 1)[0])
	except ValueError:
		return False
	mapped = getattr(ip, "ipv4_mapped", None)
	if mapped is not None:
		ip = mapped
	return bool(ip.is_global) and not ip.is_multicast


def resolve_public(host, port, resolve=None):
	"""Every address ``host`` resolves to, after checking each is public; raises
	:class:`FetchRefused` if one is not, or if it does not resolve. An early, readable refusal: the
	enforcement is the connect-time check in :func:`guarded_session`."""
	resolve = resolve or socket.getaddrinfo
	try:
		infos = resolve(host, port, proto=socket.IPPROTO_TCP)
	except (OSError, UnicodeError):
		raise FetchRefused("network", f"Could not find the site {host}. Check the link.") from None
	addresses = sorted({info[4][0] for info in infos or ()})
	if not addresses:
		raise FetchRefused("network", f"Could not find the site {host}. Check the link.")
	for address in addresses:
		if not address_is_public(address):
			raise FetchRefused("blocked", BLOCKED_MESSAGE)
	return addresses


def require_public_socket(sock):
	"""Close ``sock`` and refuse unless its peer is a public address. Called the moment a connection
	opens, before a byte is sent; fails closed when the peer cannot be read."""
	try:
		peer = sock.getpeername()[0]
	except (OSError, IndexError, TypeError):
		peer = None
	if peer is None or not address_is_public(peer):
		try:
			sock.close()
		finally:
			raise FetchRefused("blocked", BLOCKED_MESSAGE)


def guarded_session():
	"""A ``requests`` session whose every connection is checked at connect time (see the module
	docstring), that accepts no cookies, retries nothing and uses no proxy from the environment."""
	from http.cookiejar import DefaultCookiePolicy

	import requests
	from requests.adapters import HTTPAdapter
	from urllib3.connection import HTTPConnection, HTTPSConnection
	from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

	class _GuardedHTTPConnection(HTTPConnection):
		def _new_conn(self):
			sock = super()._new_conn()
			require_public_socket(sock)
			return sock

	class _GuardedHTTPSConnection(HTTPSConnection):
		def _new_conn(self):
			sock = super()._new_conn()
			require_public_socket(sock)
			return sock

	class _GuardedHTTPPool(HTTPConnectionPool):
		ConnectionCls = _GuardedHTTPConnection

	class _GuardedHTTPSPool(HTTPSConnectionPool):
		ConnectionCls = _GuardedHTTPSConnection

	class _GuardedAdapter(HTTPAdapter):
		def init_poolmanager(self, *args, **kwargs):
			super().init_poolmanager(*args, **kwargs)
			self.poolmanager.pool_classes_by_scheme = {"http": _GuardedHTTPPool, "https": _GuardedHTTPSPool}

		def proxy_manager_for(self, *args, **kwargs):
			raise FetchRefused("blocked", BLOCKED_MESSAGE)

	session = requests.Session()
	session.trust_env = False
	session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
	adapter = _GuardedAdapter(max_retries=0)
	session.mount("http://", adapter)
	session.mount("https://", adapter)
	return session


def fetch(url, *, session=None, resolve=None, max_bytes=None):
	"""Read ``url`` under the rules in the module docstring and return a :class:`Fetched`.

	``session`` is for tests only; production always uses :func:`guarded_session`. Raises
	:class:`FetchRefused` with a message for the person, for every expected failure.
	"""
	problem = url_problem(url)
	if problem:
		raise FetchRefused("blocked", problem)
	max_bytes = max_bytes or constants.SOURCE_MAX_BYTES
	session = session if session is not None else guarded_session()
	deadline = _clock() + DEADLINE_SECONDS
	current = url.strip()
	for _hop in range(MAX_REDIRECTS + 1):
		problem = url_problem(current)
		if problem:
			raise FetchRefused("blocked", problem)
		parts = urlsplit(current)
		port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
		resolve_public(parts.hostname, port, resolve=resolve)
		_clear_cookies(session)
		response = _get(session, current)
		try:
			if 300 <= response.status_code < 400 and response.headers.get("Location"):
				current = urljoin(current, response.headers["Location"])
				continue
			if response.status_code >= 400:
				raise FetchRefused(
					"http", f"The site answered with an error ({response.status_code}). Check the link."
				)
			content_type = _content_type(response, current)
			if content_type not in ALLOWED_TYPES:
				raise FetchRefused(
					"unsupported",
					f"The link is a {content_type or 'kind of file'} the library cannot read. A web "
					"page, PDF, Word document, text file or picture can be saved.",
				)
			data = _read_capped(response, max_bytes, deadline)
			return Fetched(data, content_type, current, _filename(response, current, content_type))
		finally:
			close = getattr(response, "close", None)
			if callable(close):
				close()
	raise FetchRefused("http", "The link redirects too many times.")


def _clear_cookies(session):
	cookies = getattr(session, "cookies", None)
	clear = getattr(cookies, "clear", None)
	if callable(clear):
		clear()


def _get(session, url):
	try:
		return session.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=False)
	except FetchRefused:
		raise
	except Exception as exc:
		refused = _refusal_inside(exc)
		if refused is not None:
			raise refused from None
		if "Timeout" in type(exc).__name__:
			raise FetchRefused("network", "The site took too long to answer. Try again later.") from None
		raise FetchRefused(
			"network", "Could not reach the site. Check the link, or try again later."
		) from None


def _refusal_inside(exc):
	"""A :class:`FetchRefused` an HTTP library wrapped on its way up, or ``None``."""
	seen = set()
	while exc is not None and id(exc) not in seen:
		if isinstance(exc, FetchRefused):
			return exc
		seen.add(id(exc))
		inner = None
		for arg in getattr(exc, "args", ()):
			if isinstance(arg, BaseException):
				inner = arg
				break
		exc = getattr(exc, "reason", None) or inner or exc.__cause__ or exc.__context__
	return None


def _content_type(response, url):
	value = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
	if value in ("", "application/octet-stream", "binary/octet-stream"):
		path = urlsplit(url).path.lower()
		for suffix, guessed in (
			(".pdf", "application/pdf"),
			(".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
			(".md", "text/markdown"),
			(".txt", "text/plain"),
			(".csv", "text/csv"),
		):
			if path.endswith(suffix):
				return guessed
	return value


def _read_capped(response, max_bytes, deadline=None):
	declared = response.headers.get("Content-Length")
	if declared and declared.isdigit() and int(declared) > max_bytes:
		raise FetchRefused("too_large", _too_large(max_bytes))
	chunks = []
	total = 0
	for chunk in response.iter_content(_CHUNK):
		if deadline is not None and _clock() > deadline:
			raise FetchRefused("network", "The site sent the page too slowly. Try again later.")
		if not chunk:
			continue
		total += len(chunk)
		if total > max_bytes:
			raise FetchRefused("too_large", _too_large(max_bytes))
		chunks.append(chunk)
	return b"".join(chunks)


def _too_large(max_bytes):
	return f"The link is larger than {max_bytes // (1024 * 1024)} MB, the most the library reads."


_EXTENSIONS = {
	"text/html": ".html",
	"application/xhtml+xml": ".html",
	"text/plain": ".txt",
	"text/markdown": ".md",
	"text/x-markdown": ".md",
	"text/csv": ".csv",
	"application/pdf": ".pdf",
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
	"image/png": ".png",
	"image/jpeg": ".jpg",
	"image/webp": ".webp",
}


def _filename(response, url, content_type):
	"""A safe file name for the saved copy: the server's, the URL's last part, or the host."""
	disposition = response.headers.get("Content-Disposition") or ""
	match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disposition, re.IGNORECASE)
	name = unquote(match.group(1)) if match else ""
	if not name:
		parts = urlsplit(url)
		name = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1]) or (parts.hostname or "page")
	return safe_filename(name, _EXTENSIONS.get(content_type, ""))


def safe_filename(name, extension=""):
	"""``name`` reduced to letters, digits, dots, dashes and underscores, at most 100 characters,
	ending in ``extension`` when it does not already."""
	cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name or "").strip(".-") or "source"
	cleaned = cleaned[:100]
	if extension and not cleaned.lower().endswith(extension):
		cleaned = f"{cleaned[: 100 - len(extension)]}{extension}"
	return cleaned
