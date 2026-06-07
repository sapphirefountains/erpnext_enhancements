frappe.ui.form.on("Opportunity", {
	refresh: function (frm) {
		if (!frm.is_new()) {
			var field = frm.get_field("custom_reminder_action");

			if (field) {
				var $btn = $(
					'<button class="btn btn-default btn-sm icon-btn"><span class="icon icon-sm"><svg class="es-icon es-line  icon-sm" aria-hidden="true"><use href="#es-line-bell"></use></svg></span> Set Reminder</button>'
				);

				$btn.on("click", function () {
					var d = new frappe.ui.Dialog({
						title: __("Create a Reminder"),
						fields: [
							{
								label: "Remind Me In",
								fieldname: "remind_in",
								fieldtype: "Select",
								options: [
									{ label: "30 Minutes", value: 30 },
									{ label: "1 Hour", value: 60 },
									{ label: "2 Hours", value: 120 },
									{ label: "4 Hours", value: 240 },
									{ label: "Tomorrow Morning", value: "tomorrow" },
								],
								onchange: function () {
									var choice = this.get_value();
									if (!choice) return;

									var new_time;
									if (choice === "tomorrow") {
										new_time = moment()
											.add(1, "days")
											.set({ hour: 9, minute: 0, second: 0 })
											.format("YYYY-MM-DD HH:mm:ss");
									} else {
										new_time = moment()
											.add(choice, "minutes")
											.format("YYYY-MM-DD HH:mm:ss");
									}
									d.set_value("remind_at", new_time);
								},
							},
							{
								fieldtype: "Column Break",
							},
							{
								label: "Remind At",
								fieldname: "remind_at",
								fieldtype: "Datetime",
								reqd: 1,
								default: frappe.datetime.now_datetime(),
							},
							{
								fieldtype: "Section Break",
							},
							{
								label: "Description",
								fieldname: "description",
								fieldtype: "Small Text",
								reqd: 1,
								default:
									"Reminder for Opportunity: " + (frm.doc.title || frm.doc.name),
							},
						],
						primary_action_label: __("Create"),
						primary_action: function (values) {
							frappe.db
								.insert({
									doctype: "ToDo",
									reference_type: frm.doc.doctype,
									reference_name: frm.doc.name,
									description: values.description,
									date: values.remind_at,
									allocated_to: frappe.session.user,
									status: "Open",
								})
								.then((doc) => {
									d.hide();
									frappe.show_alert({
										message: __("Reminder created successfully"),
										indicator: "green",
									});
								});
						},
					});

					d.show();
				});

				field.$wrapper.empty().append($btn);
			}
		}
	},
});
