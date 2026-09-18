import frappe

def find_overlaps(employee, start, end, exclude=None):
	"""
	Job Interval names for `employee` whose time range intersects [start, end).
	Two intervals A and B intersect when: A.start < B.end AND A.end > B.start.
	"""
	# A NULL end_time in the database means the interval is open and still running.
	# In SQL, comparing `NULL > value` yields NULL (not True), which would silently 
	# fail to match, causing an open interval to wrongly avoid overlapping anything.
	# We use `(end_time IS NULL OR end_time > %(start)s)` to treat an open interval
	# as extending infinitely into the future, correctly overlapping with the test range.
	query = """
		SELECT name, project, start_time, end_time
		FROM `tabJob Interval`
		WHERE employee = %(employee)s
		  AND start_time < %(end)s
		  AND (end_time IS NULL OR end_time > %(start)s)
	"""
	
	# If the incoming interval is open (end is None), it extends into the far future.
	# We use a synthetic far-future date to represent its end in the query.
	params = {
		"employee": employee,
		"start": start,
		"end": end or "2999-12-31 23:59:59"
	}
	
	if exclude:
		query += " AND name != %(exclude)s"
		params["exclude"] = exclude
		
	return frappe.db.sql(query, params, as_dict=True)

def assert_no_overlap(doc):
	"""frappe.throw with a readable message when `doc` overlaps an existing interval."""
	if not doc.start_time:
		return
		
	overlaps = find_overlaps(doc.employee, doc.start_time, doc.end_time, exclude=doc.name)
	if overlaps:
		conflicting = overlaps[0]
		end_str = conflicting.end_time or "now"
		frappe.throw(
			f"Time overlap: this interval overlaps with {conflicting.name} "
			f"({conflicting.project}) from {conflicting.start_time} to {end_str}."
		)
