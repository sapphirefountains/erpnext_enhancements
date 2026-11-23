# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Backfill the v1.142.0 issue/readiness fields on existing Water Feature Designs (retired).

Until v1.771.0 this rebuilt each design's typed issues from its schema-1 child rows with
``issues.build_issues`` / ``build_readiness``. Phase H of the Fountain Designer rebuild removed
those child DocTypes and both producers; a schema-2 design writes its issues on every save
(``derive.derive_model``). The patch ran on every site in v1.144.0, and a fresh install marks every
patch done without running it, so it has nothing left to do. It stays in ``patches.txt`` as a
no-op so the log keeps its name.
"""


def execute():
	return
