# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What one person has chosen to show. One row per user, created on first change.

Deliberately **not** fields on ``Training Learner Stat``. That row says of itself
that "nothing in it is evidence of anything, and it can be thrown away and
rebuilt" — ``rebuild_learner_stat`` does exactly that — so a preference stored
there would be silently reset by a routine recalculation, and the person would
find out by being back on a leaderboard they had left.

Both settings default to **on**, and absence means the default. An opt-in feed in
a sixteen-person company is an empty feed, and an empty feed reads as broken
rather than as private.
"""

from frappe.model.document import Document


class TrainingProfilePreference(Document):
	pass
