# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Shared pieces every Technician Program course spec uses.

Its own module rather than ``__init__``: the ten course modules import from here, and
``__init__`` imports the ten. Putting the notice in ``__init__`` would be a cycle.

Indentation is tabs, matching the ``training/`` package.
"""

#: Shown at the top of the first lesson of every course. The learner is told what they are
#: reading before they read it -- the same courtesy the four Quality drafts carry.
#:
#: The second paragraph is the one that matters for a trade course. A number that belongs to a
#: product (a cure time, a torque, a dose rate, a test pressure) is wrong on the next job, and a
#: technician who learned it here and did not check the label is the failure this whole module
#: is trying not to cause.
DRAFT_NOTICE = (
	"<p><b>This course is a draft.</b> It was written from general practice in fountain and "
	"aquatic-system work, against an outline Sapphire supplied. It has <b>not yet been reviewed "
	"or adopted</b> by anyone here. Read it as a starting point to correct.</p>"
	"<p>Where a number belongs to a product, a manufacturer or a particular job — cure and set "
	"times, torque figures, dose rates, test pressures, chemical targets, anchor embedments — "
	"this course tells you <b>where to read it</b> instead of printing a figure that would be "
	"wrong on the next job. The label, the data sheet, the submittal and the engineer's "
	"specification beat anything written here, every time.</p>"
	"<p>It also does not replace certified training. Confined space entry, lock-out/tag-out, "
	"electrical work and first aid each have a formal standard and a written program behind "
	"them. This course tells you what those are for and when to stop and get the person who "
	"holds them.</p>"
)


def notice_block():
	"""The draft notice, as the first block of a course's first lesson."""
	return {
		"block_type": "Callout",
		"callout_tone": "Warning",
		"heading": "Draft — not yet adopted",
		"content": DRAFT_NOTICE,
	}


def ask_block(heading, content):
	"""A 'this one is Sapphire's call, not the course's' callout.

	Used wherever a lesson reaches the edge of what general practice can answer: how often
	Sapphire wants something done, which product is on the truck, what a given client's
	specification says. Inventing those is the same error as inventing a checklist.
	"""
	return {
		"block_type": "Callout",
		"callout_tone": "Info",
		"heading": heading,
		"content": content,
	}
