"""The text of a reference-library source: a web page, PDF, Word document, Markdown or text file.

``sources.py`` hands this the bytes of a fetched link or an uploaded file; this returns the text a
person or an AI reads, as plain, Markdown-flavored text (headings as ``#``, list items as ``-``,
table rows as ``a | b | c``), so a table survives as something an AI can read. Pictures carry no text
here: ``sources.py`` asks Gemini to describe a picture and read any words in it.

**What it reads, and how:**

- **HTML** with the standard library's ``HTMLParser``: the visible text of the page, without
  ``script``, ``style``, ``noscript``, ``template``, ``svg``, ``iframe`` or the ``head`` (apart from
  the ``title``, which becomes the source's title). The charset comes from the HTTP header or the
  page's own ``meta``, else UTF-8.
- **PDF** with ``pypdf``, which Frappe itself depends on (so it is on the server, where nothing can be
  pip-installed), page by page, at most :data:`MAX_PDF_PAGES` pages. A scanned PDF has no text layer
  and comes back empty, which ``sources.py`` reports.
- **Word (.docx)** with ``zipfile`` and ``ElementTree``: the paragraphs and table rows of
  ``word/document.xml``, refusing a file whose document part unpacks past :data:`MAX_DOCX_XML_BYTES`
  (a "zip bomb").
- **Markdown, plain text and CSV**, decoded as UTF-8 (a byte-order mark is dropped; a bad byte becomes
  a replacement character, never an error).

Anything else is ``None`` from :func:`kind_of`, which ``sources.py`` stores as "Not supported".
Standard library only at import time (``pypdf`` is imported inside :func:`pdf_text`), so it is
tested bench-free.
"""

import io
import re
import zipfile
from collections import namedtuple
from html.parser import HTMLParser
from xml.etree import ElementTree

#: The text, the title found in the content (an HTML ``title`` or the first heading), and the kind.
Extracted = namedtuple("Extracted", "text title kind")

#: The most PDF pages read.
MAX_PDF_PAGES = 300

#: The largest ``word/document.xml`` unpacked.
MAX_DOCX_XML_BYTES = 50 * 1024 * 1024

_TYPE_KINDS = {
	"text/html": "html",
	"application/xhtml+xml": "html",
	"application/pdf": "pdf",
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
	"text/plain": "text",
	"text/markdown": "text",
	"text/x-markdown": "text",
	"text/csv": "text",
	"image/png": "image",
	"image/jpeg": "image",
	"image/webp": "image",
}

_EXTENSION_KINDS = {
	".html": "html",
	".htm": "html",
	".pdf": "pdf",
	".docx": "docx",
	".md": "text",
	".markdown": "text",
	".txt": "text",
	".csv": "text",
	".png": "image",
	".jpg": "image",
	".jpeg": "image",
	".webp": "image",
}

#: The content type for each kind's file extension, for an upload whose type the browser did not say.
EXTENSION_TYPES = {
	".html": "text/html",
	".htm": "text/html",
	".pdf": "application/pdf",
	".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	".md": "text/markdown",
	".markdown": "text/markdown",
	".txt": "text/plain",
	".csv": "text/csv",
	".png": "image/png",
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".webp": "image/webp",
}


def kind_of(content_type, filename=""):
	"""``"html"``, ``"pdf"``, ``"docx"``, ``"text"``, ``"image"``, or ``None`` when unsupported. The
	content type decides; the file name's extension only when the type is missing or generic."""
	value = (content_type or "").split(";", 1)[0].strip().lower()
	if value in _TYPE_KINDS:
		return _TYPE_KINDS[value]
	if value in ("", "application/octet-stream", "binary/octet-stream"):
		return _EXTENSION_KINDS.get(_extension(filename))
	return None


def content_type_for(filename):
	"""The content type for ``filename``'s extension, or ``""``."""
	return EXTENSION_TYPES.get(_extension(filename), "")


def _extension(filename):
	match = re.search(r"(\.[A-Za-z0-9]+)$", filename or "")
	return match.group(1).lower() if match else ""


def extract(data, content_type, filename="", charset=None):
	"""The :class:`Extracted` text of ``data``. An image's text is ``""`` (Gemini reads it).

	Raises ``ValueError`` with a message for the person when the content is damaged or unsupported.
	"""
	kind = kind_of(content_type, filename)
	if kind is None:
		raise ValueError("The library cannot read this kind of file.")
	if kind == "image":
		return Extracted("", "", kind)
	if kind == "html":
		text, title = html_text(data, charset)
		return Extracted(text, title, kind)
	if kind == "pdf":
		text, title = pdf_text(data)
		return Extracted(text, title, kind)
	if kind == "docx":
		text = docx_text(data)
		return Extracted(text, _first_heading(text), kind)
	text = tidy(decode(data, charset))
	return Extracted(text, _first_heading(text), kind)


def decode(data, charset=None):
	"""``data`` as text: the given charset if Python knows it, else UTF-8; a BOM is dropped."""
	if isinstance(data, str):
		return data
	for encoding in (charset, "utf-8-sig"):
		if not encoding:
			continue
		try:
			return data.decode(encoding, errors="replace")
		except LookupError:
			continue
	return data.decode("utf-8", errors="replace")


def tidy(text):
	"""Normalize line ends, strip trailing spaces, and collapse three or more blank lines to one."""
	text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
	lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.split("\n")]
	text = "\n".join(lines)
	return re.sub(r"\n{3,}", "\n\n", text).strip()


def _first_heading(text):
	for line in (text or "").split("\n"):
		line = line.strip().lstrip("#").strip()
		if line:
			return line[:140]
	return ""


# ------------------------------------------------------------------ HTML


_SKIPPED = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "head", "object", "canvas"})
_BLOCKS = frozenset(
	{
		"p",
		"div",
		"section",
		"article",
		"main",
		"header",
		"footer",
		"aside",
		"nav",
		"blockquote",
		"pre",
		"form",
		"figure",
		"figcaption",
		"address",
		"dl",
		"dt",
		"dd",
		"ul",
		"ol",
		"table",
		"thead",
		"tbody",
		"tfoot",
		"hr",
		"br",
	}
)
_HEADINGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### "}
_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_-]+)", re.IGNORECASE)


class _TextParser(HTMLParser):
	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.out = []
		self.title = []
		self.skip = 0
		self.in_title = False
		self.row = None
		self.cell = None

	def handle_starttag(self, tag, attrs):
		if tag == "title":
			self.in_title = True
			return
		if tag in _SKIPPED:
			self.skip += 1
			return
		if self.skip:
			return
		if tag in _HEADINGS:
			self._newline()
			self.out.append(_HEADINGS[tag])
		elif tag == "li":
			self._newline()
			self.out.append("- ")
		elif tag == "tr":
			self._newline()
			self.row = []
		elif tag in ("td", "th"):
			self.cell = []
		elif tag in _BLOCKS:
			self._newline()

	def handle_endtag(self, tag):
		if tag == "title":
			self.in_title = False
			return
		if tag in _SKIPPED:
			self.skip = max(0, self.skip - 1)
			return
		if self.skip:
			return
		if tag in ("td", "th") and self.row is not None and self.cell is not None:
			self.row.append(" ".join("".join(self.cell).split()))
			self.cell = None
		elif tag == "tr" and self.row is not None:
			cells = [c for c in self.row]
			if any(cells):
				self.out.append(" | ".join(cells))
			self.row = None
			self._newline()
		elif tag in _HEADINGS or tag == "li" or tag in _BLOCKS:
			self._newline()

	def handle_data(self, data):
		if self.in_title:
			self.title.append(data)
			return
		if self.skip:
			return
		if self.cell is not None:
			self.cell.append(data)
			return
		if self.row is not None:
			return
		self.out.append(data)

	def _newline(self):
		self.out.append("\n")


def html_text(data, charset=None):
	"""``(text, title)`` of an HTML page."""
	if isinstance(data, bytes) and not charset:
		match = _META_CHARSET.search(data[:4096])
		charset = match.group(1).decode("ascii", "ignore") if match else None
	parser = _TextParser()
	try:
		parser.feed(decode(data, charset))
		parser.close()
	except Exception:
		raise ValueError("The page could not be read.") from None
	text = tidy(re.sub(r"[ \t]*\n[ \t]*", "\n", "".join(parser.out)))
	title = " ".join("".join(parser.title).split())[:140] or _first_heading(text)
	return text, title


# ------------------------------------------------------------------ PDF


def pdf_text(data):
	"""``(text, title)`` of a PDF: each page's text, separated by a blank line."""
	try:
		from pypdf import PdfReader

		reader = PdfReader(io.BytesIO(data))
		if reader.is_encrypted and not reader.decrypt(""):
			raise ValueError("The PDF is password-protected. Upload a copy without the password.")
		pages = []
		for page in reader.pages[:MAX_PDF_PAGES]:
			pages.append(page.extract_text() or "")
		title = ""
		meta = reader.metadata
		if meta is not None and getattr(meta, "title", None):
			title = " ".join(str(meta.title).split())[:140]
	except ValueError:
		raise
	except Exception:
		raise ValueError("The PDF could not be read; it may be damaged.") from None
	text = tidy("\n\n".join(pages))
	return text, title or _first_heading(text)


# ------------------------------------------------------------------ Word


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(data):
	"""The paragraphs and table rows of a ``.docx``, in document order."""
	try:
		with zipfile.ZipFile(io.BytesIO(data)) as archive:
			info = archive.getinfo("word/document.xml")
			if info.file_size > MAX_DOCX_XML_BYTES:
				raise ValueError("The Word document is too large to read.")
			root = ElementTree.fromstring(archive.read(info))
	except ValueError:
		raise
	except Exception:
		raise ValueError("The Word document could not be read; it may be damaged.") from None
	body = root.find(f"{_W}body")
	if body is None:
		return ""
	lines = []
	for child in body:
		if child.tag == f"{_W}p":
			lines.append(_paragraph(child))
		elif child.tag == f"{_W}tbl":
			for row in child.iter(f"{_W}tr"):
				cells = [
					" ".join(_paragraph(p) for p in cell.iter(f"{_W}p")).strip()
					for cell in row.findall(f"{_W}tc")
				]
				if any(cells):
					lines.append(" | ".join(cells))
			lines.append("")
	return tidy("\n".join(lines))


def _paragraph(element):
	parts = []
	for node in element.iter():
		if node.tag == f"{_W}t" and node.text:
			parts.append(node.text)
		elif node.tag == f"{_W}tab":
			parts.append(" ")
		elif node.tag in (f"{_W}br", f"{_W}cr"):
			parts.append("\n")
	style = element.find(f"{_W}pPr/{_W}pStyle")
	text = "".join(parts).strip()
	value = style.get(f"{_W}val", "") if style is not None else ""
	match = re.match(r"Heading([1-6])$", value)
	if match and text:
		return "#" * int(match.group(1)) + " " + text
	if element.find(f"{_W}pPr/{_W}numPr") is not None and text:
		return "- " + text
	return text
