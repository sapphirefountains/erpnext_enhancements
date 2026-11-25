"""Fetch a link for the reference library without letting the server reach anything internal.

Nik, 2026-11-25: "submit links for the AI to fetch and save the articles information to ERPNext".
The fetch runs **on the production server**, inside Google Cloud, so a URL is an instruction to that
server to make a request. Without care, a link to ``http://169.254.169.254/`` (the cloud metadata
server), ``http://localhost:8000/`` (the bench itself) or a private address would have ERPNext read
its own infrastructure and store the answer as a "source" every staff user can open. That is a
server-side request forgery, and this module is what stops it.

**The rules, every one applied to every hop of a redirect chain:**

- ``http`` or ``https`` only, on the default port (80 or 443). No ``user:password@`` in the address.
- The host is resolved first, and **every** address it resolves to must be a global (public) one:
  ``ipaddress``'s ``is_global`` refuses loopback, private (10/8, 172.16/12, 192.168/16), link-local
  (169.254/16, where the metadata server lives), carrier-grade NAT (100.64/10), multicast, reserved
  and unspecified addresses, and their IPv6 counterparts. An IPv4 address written inside IPv6
  (``::ffff:127.0.0.1``) is judged as the IPv4 address it is.
- Redirects are followed **by this code**, at most five, and each new address is checked again, so a
  public page cannot bounce the request to an internal one.
- **After connecting, the address actually connected to is checked too**, when the HTTP library
  exposes it, so a hostname that answers a public address to the check and a private one to the
  connection (DNS rebinding) is refused before its body is read. Where the library does not expose
  the socket, the resolve-time check stands alone; the metadata server also refuses any request
  without its ``Metadata-Flavor`` header, which this never sends.
- At most :data:`constants.SOURCE_MAX_BYTES` are read, and only the content types the library can
  read (:data:`ALLOWED_TYPES`); anything else is refused before its body is downloaded.

Google Docs and Drive links are not fetched here: :func:`google_file` recognizes them and
``sources.py`` reads them through the Drive API as the company's service account, because a Docs
page fetched anonymously is a sign-in page.

Standard library plus ``requests``, which is imported only when :func:`fetch` runs without an
injected session, so the rules are tested bench-free (``tests/test_knowledge_base_sources.py``).
"""

import ipaddress
import re
import socket
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

#: The most redirects followed for one link.
MAX_REDIRECTS = 5

#: Seconds to connect, and to wait between bytes once connected.
TIMEOUT = (5, 20)

#: Sent with every request. Plain, honest, and no cookies or credentials of any kind.
HEADERS = {
	"User-Agent": "SapphireFountains-KnowledgeBase/1.0 (+reference library)",
	"Accept": "text/html,application/xhtml+xml,application/pdf,text/plain,*/*;q=0.5",
}

_CHUNK = 64 * 1024

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

	The form calls this on save, so a typo is refused at once; :func:`fetch` checks the addresses.
	"""
	if not isinstance(url, str) or not url.strip():
		return "Enter the link to save."
	url = url.strip()
	if len(url) > constants.SOURCE_MAX_URL_CHARS:
		return f"The link is longer than {constants.SOURCE_MAX_URL_CHARS} characters."
	if any(ch.isspace() for ch in url):
		return "The link has a space in it. Copy it from the browser's address bar."
	try:
		parts = urlsplit(url)
		port = parts.port
	except ValueError:
		return "That is not a web address. Copy it from the browser's address bar."
	if parts.scheme.lower() not in ("http", "https"):
		return "Only web links (starting https:// or http://) can be saved."
	if not parts.hostname:
		return "That is not a web address. Copy it from the browser's address bar."
	if parts.username or parts.password:
		return "The link has a user name or password in it. Save the link without it."
	if port not in (None, 80, 443):
		return "Only links on the standard web ports can be saved."
	return None


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
	:class:`FetchRefused` if one is not, or if it does not resolve."""
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
			raise FetchRefused(
				"blocked",
				"That link points inside the company's own network or servers, which the "
				"reference library never reads. Save a public web page, or upload the file instead.",
			)
	return addresses


def fetch(url, *, session=None, resolve=None, max_bytes=None):
	"""Read ``url`` under the rules in the module docstring and return a :class:`Fetched`.

	Raises :class:`FetchRefused` with a message for the person, for every expected failure.
	"""
	problem = url_problem(url)
	if problem:
		raise FetchRefused("blocked", problem)
	max_bytes = max_bytes or constants.SOURCE_MAX_BYTES
	if session is None:
		import requests

		session = requests.Session()
		session.trust_env = False
	current = url.strip()
	for _hop in range(MAX_REDIRECTS + 1):
		problem = url_problem(current)
		if problem:
			raise FetchRefused("blocked", problem)
		parts = urlsplit(current)
		port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
		resolve_public(parts.hostname, port, resolve=resolve)
		response = _get(session, current)
		try:
			_check_peer(response)
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
			data = _read_capped(response, max_bytes)
			return Fetched(data, content_type, current, _filename(response, current, content_type))
		finally:
			close = getattr(response, "close", None)
			if callable(close):
				close()
	raise FetchRefused("http", "The link redirects too many times.")


def _get(session, url):
	try:
		return session.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=False)
	except Exception as exc:
		name = type(exc).__name__
		if "Timeout" in name:
			raise FetchRefused("network", "The site took too long to answer. Try again later.") from None
		raise FetchRefused(
			"network", "Could not reach the site. Check the link, or try again later."
		) from None


def _check_peer(response):
	"""Refuse when the socket actually connected to is not public (DNS rebinding); see the module
	docstring. Silent when the HTTP library does not expose the socket."""
	address = peer_address(response)
	if address is not None and not address_is_public(address):
		raise FetchRefused(
			"blocked",
			"That link points inside the company's own network or servers, which the reference "
			"library never reads.",
		)


def peer_address(response):
	"""The IP address ``response`` came from, or ``None`` when it cannot be read.

	urllib3 2.x keeps the connection on the streamed response (``HTTPResponse.connection``).
	"""
	raw = getattr(response, "raw", None)
	for attr in ("connection", "_connection"):
		conn = getattr(raw, attr, None)
		sock = getattr(conn, "sock", None)
		if sock is not None:
			try:
				return sock.getpeername()[0]
			except (OSError, IndexError, TypeError):
				return None
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


def _read_capped(response, max_bytes):
	declared = response.headers.get("Content-Length")
	if declared and declared.isdigit() and int(declared) > max_bytes:
		raise FetchRefused("too_large", _too_large(max_bytes))
	chunks = []
	total = 0
	for chunk in response.iter_content(_CHUNK):
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
