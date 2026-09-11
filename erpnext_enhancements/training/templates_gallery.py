# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Starter courses — the answer to "an empty page is where authoring stops".

Nik's ask was that building a training be simple enough that anyone can do it.
Most of what stops people is not the editor: it is opening a blank course and
having to invent both the *content* and the *shape* at once. A starter gives away
the shape.

**A template is a Course Spec and nothing else.** The same dict
``assistant_tools/draft_course_spec`` produces and ``validate_course_spec``
polices, built through the same ``author_course_from_spec`` path. That is the
whole design: no second scaffolder, no second validator, no second set of block
types that drift from what the builder can render. If a template could express
something the AI path cannot, one of the two would be wrong.

Every starter is deliberately **skeletal**. The prose is instructions to the
author — "replace this with…" — not filler pretending to be content. Filler is
worse than an empty page: it gets published.

Blocks are limited to the same set a Course Spec allows, which excludes uploaded
media: a template is validated by ``validate_course_spec`` exactly like an
AI-drafted course, and widening the block list for templates would mean two
validators or one weaker one. Where a starter wants a photo or a PDF it says so
in an Info callout instead, which the author replaces with the real thing.

Four shapes, chosen from what this company actually trains on:

* **Safety Talk** — the ten-minute tailgate briefing. One lesson, a callout, a
  checklist, three questions.
* **Equipment Walkthrough** — how one machine works, in the order you touch it.
* **SOP Walkthrough** — turning a written procedure into something with a record
  attached.
* **New Hire Orientation** — the first-week course, chaptered.
"""

import copy

import frappe
from frappe import _

TEMPLATES = {
	"safety-talk": {
		"label": "Safety Talk",
		"blurb": "A ten-minute briefing before a job. One lesson, a hazard callout, "
		"the steps, and three questions to prove it landed.",
		"spec": {
			"course": {
				"course_title": "Safety Talk: [subject]",
				"summary": "What to watch for and what to do, before we start.",
				"weight": "Required",
				"audience": "Internal Staff",
			},
			"lessons": [
				{
					"lesson_title": "The briefing",
					"estimated_minutes": 10,
					"blocks": [
						{
							"block_type": "Callout",
							"callout_tone": "Danger",
							"heading": "The hazard",
							"content": "<p>Replace this with the one thing most likely to hurt "
							"somebody on this job, said plainly.</p>",
						},
						{
							"block_type": "Rich Text",
							"heading": "Why it matters here",
							"content": "<p>Two or three sentences. What has gone wrong before, or "
							"could. Specific beats general — a real near-miss is worth a page of "
							"policy.</p>",
						},
						{
							"block_type": "Checklist",
							"heading": "Before you start",
							"items": [
								"Replace with the first check",
								"Replace with the second check",
								"Replace with the third check",
							],
						},
						{
							"block_type": "Callout",
							"callout_tone": "Warning",
							"heading": "Stop the job if",
							"content": "<p>The conditions under which nobody carries on. Name them, "
							"so that stopping is the expected thing rather than somebody's "
							"judgment call.</p>",
						},
					],
					"quiz": {
						"questions": [
							{
								"question": "Replace with a question about the hazard itself.",
								"type": "Single Choice",
								"explanation": "Say why the right answer is right.",
								"options": [
									{"text": "The correct answer", "is_correct": 1},
									{"text": "A plausible wrong answer", "is_correct": 0},
									{"text": "Another plausible wrong answer", "is_correct": 0},
								],
							}
						]
					},
				}
			],
		},
	},
	"equipment-walkthrough": {
		"label": "Equipment Walkthrough",
		"blurb": "How one pump, filter or controller works — in the order somebody "
		"actually touches it. Three lessons.",
		"spec": {
			"course": {
				"course_title": "[Equipment name]: how it works",
				"summary": "What it does, how to run it, and what to do when it stops.",
				"weight": "Optional",
				"audience": "Internal Staff",
			},
			"chapters": [
				{"title": "Know it", "description": "What it is and what it is for."},
				{"title": "Run it", "description": "Normal operation, start to finish."},
				{"title": "When it goes wrong", "description": "Faults, and who to call."},
			],
			"lessons": [
				{
					"lesson_title": "What this equipment does",
					"chapter": 0,
					"estimated_minutes": 8,
					"blocks": [
						{
							"block_type": "Rich Text",
							"heading": "In one paragraph",
							"content": "<p>What it is, where it sits in the system, and what breaks "
							"downstream if it stops.</p>",
						},
						{
							"block_type": "Callout",
							"callout_tone": "Info",
							"heading": "Add a photo here",
							"content": "<p>Open this lesson in the visual editor and drop in a "
							"picture of the unit, labelled. A photo of the actual machine beats "
							"a manufacturer diagram every time.</p>",
						},
					],
				},
				{
					"lesson_title": "Running it",
					"chapter": 1,
					"estimated_minutes": 12,
					"blocks": [
						{
							"block_type": "Checklist",
							"heading": "Start-up, in order",
							"items": [
								"Replace with step one",
								"Replace with step two",
								"Replace with step three",
							],
						},
						{
							"block_type": "Callout",
							"callout_tone": "Tip",
							"heading": "What good looks like",
							"content": "<p>The reading, the sound, the flow — whatever tells "
							"somebody it is running properly rather than merely running.</p>",
						},
					],
				},
				{
					"lesson_title": "Faults and what to do",
					"chapter": 2,
					"estimated_minutes": 10,
					"blocks": [
						{
							"block_type": "Accordion",
							"heading": "Common faults",
							"panels": [
								{"title": "Replace with a symptom", "body": "What it usually means, and what to try."},
								{"title": "Replace with another symptom", "body": "What it usually means, and what to try."},
							],
						},
						{
							"block_type": "Callout",
							"callout_tone": "Danger",
							"heading": "Do not attempt",
							"content": "<p>The things that need an electrician, the manufacturer, or "
							"somebody senior. Being explicit here is what stops improvisation.</p>",
						},
					],
				},
			],
		},
	},
	"sop-walkthrough": {
		"label": "SOP Walkthrough",
		"blurb": "Turn a written procedure into a course with a record attached — "
		"so you can show who has actually read it.",
		"spec": {
			"course": {
				"course_title": "[Procedure name]",
				"summary": "The procedure, and why each step is there.",
				"weight": "Required",
				"audience": "Internal Staff",
			},
			"lessons": [
				{
					"lesson_title": "Why we do it this way",
					"estimated_minutes": 6,
					"blocks": [
						{
							"block_type": "Rich Text",
							"heading": "The point of the procedure",
							"content": "<p>A procedure somebody understands is a procedure somebody "
							"follows when the situation does not quite match. Say what it is "
							"protecting against.</p>",
						}
					],
				},
				{
					"lesson_title": "The steps",
					"estimated_minutes": 12,
					"blocks": [
						{
							"block_type": "Checklist",
							"heading": "In order",
							"items": ["Replace with step one", "Replace with step two"],
						},
						{
							"block_type": "Callout",
							"callout_tone": "Info",
							"heading": "Attach the written procedure",
							"content": "<p>Open this lesson in the visual editor and add a PDF "
							"block with the document itself, so the course and the paperwork "
							"cannot drift apart.</p>",
						},
					],
					"quiz": {
						"questions": [
							{
								"question": "Replace with a question about the step people skip.",
								"type": "Single Choice",
								"explanation": "Say what goes wrong when it is skipped.",
								"options": [
									{"text": "The correct answer", "is_correct": 1},
									{"text": "A plausible wrong answer", "is_correct": 0},
								],
							}
						]
					},
				},
			],
		},
	},
	"new-hire-orientation": {
		"label": "New Hire Orientation",
		"blurb": "The first-week course. Who we are, how we work, and what to do "
		"on day one.",
		"spec": {
			"course": {
				"course_title": "Welcome to Sapphire Fountains",
				"summary": "What you need in your first week.",
				"weight": "Required",
				"audience": "Internal Staff",
			},
			"chapters": [
				{"title": "Who we are"},
				{"title": "How we work"},
				{"title": "Your first week"},
			],
			"lessons": [
				{
					"lesson_title": "What we build",
					"chapter": 0,
					"estimated_minutes": 8,
					"blocks": [
						{
							"block_type": "Rich Text",
							"heading": "The work",
							"content": "<p>Design, build, service and rental — in plain terms, with "
							"a couple of real jobs as examples.</p>",
						}
					],
				},
				{
					"lesson_title": "How a job runs",
					"chapter": 1,
					"estimated_minutes": 10,
					"blocks": [
						{
							"block_type": "Rich Text",
							"heading": "From enquiry to handover",
							"content": "<p>The shape of a job, so a new starter can place whatever "
							"they are asked to do inside it.</p>",
						},
						{
							"block_type": "Flashcards",
							"heading": "Words you will hear",
							"cards": [
								{"front": "Replace with a term", "back": "What it means here"},
								{"front": "Replace with another", "back": "What it means here"},
							],
						},
					],
				},
				{
					"lesson_title": "Day one",
					"chapter": 2,
					"estimated_minutes": 6,
					"blocks": [
						{
							"block_type": "Checklist",
							"heading": "Get these sorted",
							"items": [
								"Replace with the first thing",
								"Replace with the second thing",
							],
						}
					],
				},
			],
		},
	},
}


def list_templates():
	"""``[{key, label, blurb, lessons, chapters}]`` for the gallery."""
	out = []
	for key, template in TEMPLATES.items():
		spec = template["spec"]
		out.append(
			{
				"key": key,
				"label": template["label"],
				"blurb": template["blurb"],
				"lessons": len(spec.get("lessons") or []),
				"chapters": len(spec.get("chapters") or []),
			}
		)
	return out


def spec_for(key, course_title=None):
	"""A **deep copy** of one template's spec, optionally retitled.

	The copy is not defensive housekeeping — ``TEMPLATES`` is module-level state
	shared by every request in the worker, and handing out the live dict would let
	one author's retitle leak into the next author's course. A cheap mistake to
	make and a very confusing one to find.
	"""
	template = TEMPLATES.get(key)
	if not template:
		frappe.throw(_("{0} is not a starter this app has.").format(key))
	spec = copy.deepcopy(template["spec"])
	if course_title:
		spec["course"]["course_title"] = course_title
	return spec
