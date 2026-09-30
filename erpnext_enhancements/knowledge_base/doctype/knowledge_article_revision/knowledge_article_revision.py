# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Knowledge Article Revision: one line of an article's Revision History (2026-09-30).

A child table of Knowledge Article, so it is read and written only through its article: every staff
user reads it with the article, and nothing writes it but ``publish.publish``, under the article's
own ``flags.kb_action`` (the Article controller refuses every other save of the article, rows
included). It holds only what was approved: a version's number, when it was published, who wrote
and who approved it, and its change note. The printed article's Revision History is drawn from it
(``knowledge_base/printing.py``).
"""

from frappe.model.document import Document


class KnowledgeArticleRevision(Document):
	pass
