// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Training Question Analytics"] = {
	filters: [
		{
			fieldname: "course",
			label: __("Course"),
			fieldtype: "Link",
			options: "Training Course",
		},
		{
			fieldname: "course_version",
			label: __("Course Version"),
			fieldtype: "Link",
			options: "Training Course Version",
			get_query() {
				const course = frappe.query_report.get_filter_value("course");
				return course ? { filters: { course } } : {};
			},
		},
		{
			fieldname: "current_version_only",
			label: __("Live Versions Only"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "lesson",
			label: __("Lesson"),
			fieldtype: "Link",
			options: "Training Lesson",
		},
		{
			fieldname: "source",
			label: __("Source"),
			fieldtype: "Select",
			options: "All\nQuiz\nCheckpoint",
			default: "All",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -6),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "min_answers",
			label: __("Answers Needed Before Reading Anything Into It"),
			fieldtype: "Int",
			default: 5,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		// Only the headline is coloured. Colouring the reading text as well turns
		// the row into a warning stripe and stops anyone reading the sentence.
		if (column.fieldname === "correct_percent" && data) {
			const shade = data.correct_percent < 40 ? "red" : data.correct_percent < 60 ? "orange" : "green";
			return `<span style="color: var(--${shade}-500); font-weight: 600;">${formatted}</span>`;
		}
		return formatted;
	},
};
