"""Run the technician rebuild again, now that it can actually delete a lesson.

``rebuild_technician_course_drafts`` shipped in v1.468.0 and **failed on all ten courses**. It
deleted the draft's lessons before releasing the questions those lessons had minted, and a
``Training Question`` carries ``source_lesson`` as a Link -- so ``check_if_doc_is_linked`` threw
``LinkExistsError`` on the very first delete of every course. The per-course ``except`` caught it,
wrote an Error Log and moved on, which is the behaviour that keeps one bad course from taking the
migrate down; what it also did was leave ``Patch Log`` recording the patch as applied, and the
printed summary saying ``0 rebuilt, 0 left alone``. Measured on production afterwards: all ten
courses still carried their v1.467.0 lessons, ``creation`` and ``modified`` both stamped at the
original seed, and the 31 chapters the same release was supposed to have cleared.

**A patch runs once.** ``executed()`` looks the patch string up in ``Patch Log`` and skips it when
it is there, whether or not it did anything -- so the fixed code would never have reached the site
it was written for without a second entry. This is that entry. It calls the same ``execute``, which
is idempotent by construction: a rebuild makes the draft match the spec, so running it on a course
that was already rebuilt makes it match again.

On a fresh install this is a second no-op after a first: the seeder has just built the courses from
these very specs, so there is nothing to bring up to date. Wasted work on a new site is a fair price
for the rewrite reaching the one site that has the courses.
"""

from erpnext_enhancements.patches import rebuild_technician_course_drafts


def execute() -> None:
	rebuild_technician_course_drafts.execute()
