# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Batch — a cohort of learners moving through a set of courses together.

The foundation of the LMS-expansion (WI-071, Phase A). A batch is deliberately a
*thin* record: it names the cohort, its course set, and its members, and it leans
on the **existing** Training Assignment engine to raise each member's assignments
rather than inventing a second assignment path — so a batch cannot drift out of
step with the individual-assignment model the rest of the module already trusts.

Controller logic (raising assignments when a batch goes Active, and the learner
"my cohort" surface) is added in the following Phase-A commits; this file is the
data model those build on.
"""

import frappe
from frappe.model.document import Document


class TrainingBatch(Document):
	pass
