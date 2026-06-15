/**
 * Desk page: Drive Link Manager (route /app/drive-link-manager).
 *
 * System-Manager-only dashboard for bulk-linking existing Google Drive folders
 * to ERPNext records (Customer / Project / Opportunity) before the two-way sync
 * takes over. Workflow: Scan (fuzzy-rank folders per unlinked record) → review
 * (approve / override the folder / search Drive / ask for a new folder) → Apply
 * (writes custom_drive_folder_id, or provisions a fresh folder, one row at a
 * time so a single failure never stops the rest). All data comes from
 * crm_enhancements.drive_link_manager (whitelisted, System-Manager only).
 *
 * Theming follows the app convention: Frappe CSS vars for surfaces/text (works
 * in Frappe Light + Timeless Night); confidence/status colors are semantic and
 * stay literal. Every server-supplied string is escaped with escape_html.
 */
frappe.pages['drive-link-manager'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Drive Link Manager'),
		single_column: true,
	});

	const esc = frappe.utils.escape_html;
	const TONE = {
		High: 'var(--green-500)',
		Medium: 'var(--yellow-500)',
		Low: 'var(--orange-500, #f59e0b)',
		None: 'var(--gray-400, #9ca3af)',
	};
	const STATUS_TONE = {
		Linked: 'var(--green-500)',
		Failed: 'var(--red-500)',
		Skipped: 'var(--gray-400, #9ca3af)',
		Suggested: 'var(--text-muted)',
	};
	const ROUTE = { Customer: 'customer', Project: 'project', Opportunity: 'opportunity' };

	let state = { candidates: [], summary: {}, filterType: 'All', filterTier: 'All' };

	const $body = $(`
		<div class="drive-link-manager">
			<style>
				.drive-link-manager { padding: 10px 4px 40px; }
				.dlm-meta { color: var(--text-muted); font-size: 12px; margin: 0 6px 12px; }
				.dlm-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 0 6px 14px; }
				.dlm-toolbar .dlm-spacer { flex: 1 1 auto; }
				.dlm-toolbar select { width: auto; min-width: 130px; }
				.dlm-counts { display: flex; flex-wrap: wrap; gap: 14px; margin: 0 6px 14px; font-size: 12.5px; }
				.dlm-chip { display: flex; align-items: center; gap: 6px; color: var(--text-muted); }
				.dlm-chip b { color: var(--text-color); font-weight: 600; }
				.dlm-dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; }
				.dlm-group-title { font-weight: 600; font-size: 13px; margin: 18px 6px 8px; color: var(--text-color); }
				.dlm-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
				.dlm-table th { text-align: left; font-weight: 600; color: var(--text-muted); border-bottom: 1px solid var(--border-color); padding: 6px 8px; font-size: 11.5px; text-transform: uppercase; letter-spacing: .03em; }
				.dlm-table td { padding: 8px; border-bottom: 1px solid var(--border-color); vertical-align: middle; }
				.dlm-rec-label { font-weight: 600; color: var(--text-color); }
				.dlm-rec-sub { color: var(--text-muted); font-size: 11.5px; }
				.dlm-match { display: flex; flex-direction: column; gap: 3px; }
				.dlm-match-label { color: var(--text-color); word-break: break-word; }
				.dlm-bar { height: 5px; border-radius: 3px; background: var(--control-bg, #eee); overflow: hidden; max-width: 180px; }
				.dlm-bar > span { display: block; height: 100%; }
				.dlm-tier { font-size: 11px; font-weight: 600; }
				.dlm-controls { display: flex; flex-direction: column; gap: 6px; min-width: 220px; }
				.dlm-controls select { width: 100%; }
				.dlm-status { font-weight: 600; }
				.dlm-conflict { color: var(--red-500); font-size: 11.5px; margin-top: 3px; }
				.dlm-empty { color: var(--text-muted); padding: 40px; text-align: center; }
				.dlm-loading { color: var(--text-muted); padding: 40px; text-align: center; }
				.dlm-row-linked { opacity: .62; }
				.dlm-apply-count { font-weight: 600; }
			</style>
			<div class="dlm-meta"></div>
			<div class="dlm-content"><div class="dlm-loading">${__('Loading…')}</div></div>
		</div>
	`).appendTo(page.body);

	page.set_primary_action(__('Scan Drive folders'), scan, 'refresh');
	page.set_secondary_action(__('Apply approved'), apply);

	// ---------------------------------------------------------------- data load
	function load() {
		const $content = $body.find('.dlm-content');
		$content.html(`<div class="dlm-loading">${__('Loading…')}</div>`);
		frappe.call({
			method: 'erpnext_enhancements.crm_enhancements.drive_link_manager.get_candidates',
			callback: (r) => {
				if (!r || !r.message) {
					$content.html(`<div class="dlm-empty">${__('No data returned.')}</div>`);
					return;
				}
				state.candidates = r.message.candidates || [];
				state.summary = r.message.summary || {};
				render();
			},
			error: () => $content.html(`<div class="dlm-empty">${__('Failed to load — you may not have permission.')}</div>`),
		});
	}

	function scan() {
		frappe.confirm(
			__('Re-scan the Shared Drive and rebuild match suggestions? Existing un-applied reviews will be replaced (already-linked rows are kept).'),
			() => {
				frappe.dom.freeze(__('Scanning Drive folders and ranking matches…'));
				frappe.call({
					method: 'erpnext_enhancements.crm_enhancements.drive_link_manager.scan_drive_links',
					callback: (r) => {
						frappe.dom.unfreeze();
						const m = r && r.message;
						if (m) {
							frappe.show_alert({
								message: __('Scanned {0} folders — {1} records to review ({2} skipped).',
									[m.folders_scanned, m.total, m.skipped || 0]),
								indicator: 'green',
							});
						}
						load();
					},
					error: () => {
						frappe.dom.unfreeze();
						frappe.msgprint({ title: __('Scan failed'), message: __('See the Error Log. Check the Drive service account and Shared Drive ID in settings.'), indicator: 'red' });
					},
				});
			}
		);
	}

	// ------------------------------------------------------------------- render
	function render() {
		const rows = state.candidates;
		$body.find('.dlm-meta').text(
			rows.length
				? __('{0} records staged. High-confidence matches are pre-approved — review, adjust, then Apply.', [rows.length])
				: __('No candidates yet. Click “Scan Drive folders” to find matches.')
		);

		const $content = $body.find('.dlm-content');
		if (!rows.length) {
			$content.html(`<div class="dlm-empty">${__('Nothing staged. Run a scan to begin.')}</div>`);
			updateApplyCount();
			return;
		}

		const filtered = rows.filter((c) =>
			(state.filterType === 'All' || c.reference_doctype === state.filterType) &&
			(state.filterTier === 'All' || (c.match_tier || 'None') === state.filterTier));

		const groups = ['Customer', 'Project', 'Opportunity'];
		let html = toolbar() + counts();
		groups.forEach((g) => {
			const gr = filtered.filter((c) => c.reference_doctype === g);
			if (!gr.length) return;
			html += `<div class="dlm-group-title">${esc(g)}s <span class="text-muted">(${gr.length})</span></div>`;
			html += table(gr);
		});
		if (!filtered.length) html += `<div class="dlm-empty">${__('No rows match the current filter.')}</div>`;
		$content.html(html);
		bind();
		updateApplyCount();
	}

	function counts() {
		const tier = (state.summary.by_tier) || {};
		const status = (state.summary.by_status) || {};
		const chip = (label, val, color) =>
			`<span class="dlm-chip">${color ? `<span class="dlm-dot" style="background:${color}"></span>` : ''}<b>${esc(String(val || 0))}</b> ${esc(label)}</span>`;
		return `<div class="dlm-counts">
			${chip(__('High'), tier.High, TONE.High)}
			${chip(__('Medium'), tier.Medium, TONE.Medium)}
			${chip(__('Low'), tier.Low, TONE.Low)}
			${chip(__('No match'), tier.None, TONE.None)}
			${chip(__('Linked'), status.Linked, STATUS_TONE.Linked)}
			${chip(__('Failed'), status.Failed, STATUS_TONE.Failed)}
		</div>`;
	}

	function toolbar() {
		const opt = (v, cur) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(v)}</option>`;
		return `<div class="dlm-toolbar">
			<select class="form-control dlm-filter-type">
				${['All', 'Customer', 'Project', 'Opportunity'].map((v) => opt(v, state.filterType)).join('')}
			</select>
			<select class="form-control dlm-filter-tier">
				${['All', 'High', 'Medium', 'Low', 'None'].map((v) => opt(v, state.filterTier)).join('')}
			</select>
			<button class="btn btn-xs btn-default dlm-approve-all">${__('Approve all suggested')}</button>
			<button class="btn btn-xs btn-default dlm-reject-rest">${__('Reject remaining')}</button>
			<span class="dlm-spacer"></span>
			<button class="btn btn-sm btn-primary dlm-apply">${__('Apply approved')} (<span class="dlm-apply-count">0</span>)</button>
		</div>`;
	}

	function table(rows) {
		const head = `<thead><tr>
			<th style="width:26%">${__('Record')}</th>
			<th style="width:34%">${__('Suggested match')}</th>
			<th style="width:28%">${__('Decision')}</th>
			<th style="width:12%">${__('Status')}</th>
		</tr></thead>`;
		return `<table class="dlm-table">${head}<tbody>${rows.map(row).join('')}</tbody></table>`;
	}

	function row(c) {
		const linked = c.status === 'Linked';
		const route = ROUTE[c.reference_doctype] || 'app';
		const rec = `<div class="dlm-rec-label">${esc(c.record_label || c.reference_name)}</div>
			<div class="dlm-rec-sub"><a href="/app/${esc(route)}/${encodeURIComponent(c.reference_name)}" target="_blank">${esc(c.reference_name)}</a>${c.context ? ' · ' + esc(c.context) : ''}</div>`;

		const tier = c.match_tier || 'None';
		const score = Math.round(c.score || 0);
		const match = c.suggested_folder_id
			? `<div class="dlm-match">
					<span class="dlm-match-label">${esc(c.suggested_folder_label || '')}</span>
					<div class="dlm-bar"><span style="width:${score}%;background:${TONE[tier]}"></span></div>
					<span class="dlm-tier" style="color:${TONE[tier]}">${esc(tier)} · ${score}%</span>
				</div>`
			: `<span class="text-muted">${__('No confident match')}</span>`;

		const controls = linked
			? `<span class="text-muted">${__('Linked to')} ${esc(c.chosen_folder_label || c.suggested_folder_label || '')}</span>`
			: decisionControls(c);

		const stone = STATUS_TONE[c.status] || 'var(--text-muted)';
		const statusHtml = `<span class="dlm-status" style="color:${stone}">${esc(c.status || 'Suggested')}</span>` +
			(c.status === 'Failed' && c.error ? `<div class="dlm-conflict" title="${esc(c.error)}">${__('error — hover')}</div>` : '') +
			(c.conflict && !linked ? `<div class="dlm-conflict">${__('⚠ folder also chosen elsewhere')}</div>` : '');

		return `<tr data-name="${esc(c.name)}" class="${linked ? 'dlm-row-linked' : ''}">
			<td>${rec}</td><td>${match}</td><td>${controls}</td><td>${statusHtml}</td></tr>`;
	}

	function decisionControls(c) {
		const decisions = ['Pending', 'Approve', 'Reject', 'Create New'];
		const decSel = `<select class="form-control input-xs dlm-decision">
			${decisions.map((d) => `<option value="${d}"${d === (c.decision || 'Pending') ? ' selected' : ''}>${esc(d)}</option>`).join('')}
		</select>`;

		let folderSel = '';
		if (c.decision === 'Approve') {
			const alts = Array.isArray(c.alternatives) ? c.alternatives.slice() : [];
			const chosenId = c.chosen_folder_id || c.suggested_folder_id;
			// Ensure the currently-chosen folder is present as an option (it may
			// have come from a manual Drive search, not the ranked alternatives).
			if (chosenId && !alts.some((a) => a.id === chosenId)) {
				alts.unshift({ id: chosenId, label: c.chosen_folder_label || c.suggested_folder_label, score: null });
			}
			const optionHtml = alts.map((a) =>
				`<option value="${esc(a.id)}"${a.id === chosenId ? ' selected' : ''}>${esc(a.label || a.name || a.id)}${a.score != null ? ' (' + Math.round(a.score) + '%)' : ''}</option>`).join('');
			folderSel = `<select class="form-control input-xs dlm-folder">
				${optionHtml}
				<option value="__search__">${__('🔍 Search Drive…')}</option>
			</select>`;
		} else if (c.decision === 'Create New') {
			folderSel = `<span class="text-muted" style="font-size:11.5px">${__('A new folder will be created and linked.')}</span>`;
		}
		return `<div class="dlm-controls">${decSel}${folderSel}</div>`;
	}

	// ------------------------------------------------------------------- events
	function bind() {
		$body.find('.dlm-filter-type').off('change').on('change', function () { state.filterType = $(this).val(); render(); });
		$body.find('.dlm-filter-tier').off('change').on('change', function () { state.filterTier = $(this).val(); render(); });
		$body.find('.dlm-approve-all').off('click').on('click', () => bulk('Approve'));
		$body.find('.dlm-reject-rest').off('click').on('click', () => bulk('Reject', true));
		$body.find('.dlm-apply').off('click').on('click', apply);

		$body.find('.dlm-decision').off('change').on('change', function () {
			const name = $(this).closest('tr').data('name');
			setDecision(name, $(this).val());
		});
		$body.find('.dlm-folder').off('change').on('change', function () {
			const $sel = $(this);
			const name = $sel.closest('tr').data('name');
			const val = $sel.val();
			if (val === '__search__') { openSearch(name); $sel.val($sel.find('option:not([value="__search__"])').first().val()); return; }
			const label = $sel.find('option:selected').text();
			setDecision(name, 'Approve', val, label);
		});
	}

	function local(name) { return state.candidates.find((c) => c.name === name); }

	function setDecision(name, decision, folderId, folderLabel) {
		frappe.call({
			method: 'erpnext_enhancements.crm_enhancements.drive_link_manager.set_decision',
			args: { name, decision, chosen_folder_id: folderId || null, chosen_folder_label: folderLabel || null },
			callback: () => {
				const c = local(name);
				if (c) {
					c.decision = decision;
					if (decision === 'Approve') {
						c.chosen_folder_id = folderId || c.suggested_folder_id;
						c.chosen_folder_label = folderLabel || c.suggested_folder_label;
					} else {
						c.chosen_folder_id = null;
						c.chosen_folder_label = decision === 'Create New' ? '↳ create new folder' : null;
					}
				}
				render();
			},
		});
	}

	function bulk(decision, onlyPending) {
		let targets = state.candidates.filter((c) => c.status !== 'Linked');
		if (onlyPending) targets = targets.filter((c) => (c.decision || 'Pending') === 'Pending');
		else if (decision === 'Approve') targets = targets.filter((c) => c.suggested_folder_id);
		const names = targets.map((c) => c.name);
		if (!names.length) { frappe.show_alert(__('Nothing to update.')); return; }
		frappe.call({
			method: 'erpnext_enhancements.crm_enhancements.drive_link_manager.bulk_decision',
			args: { names: JSON.stringify(names), decision },
			callback: () => load(),
		});
	}

	function openSearch(name) {
		const d = new frappe.ui.Dialog({
			title: __('Search Drive folders'),
			fields: [
				{ fieldtype: 'Data', fieldname: 'q', label: __('Folder name contains'), reqd: 1 },
				{ fieldtype: 'Button', fieldname: 'go', label: __('Search') },
				{ fieldtype: 'HTML', fieldname: 'results' },
			],
		});
		const runSearch = () => {
			const q = d.get_value('q');
			if (!q) return;
			d.fields_dict.results.$wrapper.html(`<div class="text-muted">${__('Searching…')}</div>`);
			frappe.call({
				method: 'erpnext_enhancements.crm_enhancements.drive_link_manager.search_folders',
				args: { query: q },
				callback: (r) => {
					const items = (r && r.message) || [];
					if (!items.length) { d.fields_dict.results.$wrapper.html(`<div class="text-muted">${__('No folders found.')}</div>`); return; }
					const html = items.map((it) =>
						`<div style="padding:6px 0;border-bottom:1px solid var(--border-color);display:flex;justify-content:space-between;gap:10px;align-items:center">
							<span>${esc(it.name)}</span>
							<button class="btn btn-xs btn-default dlm-pick" data-id="${esc(it.id)}" data-label="${esc(it.label || it.name)}">${__('Use')}</button>
						</div>`).join('');
					d.fields_dict.results.$wrapper.html(html);
					d.$wrapper.find('.dlm-pick').on('click', function () {
						setDecision(name, 'Approve', $(this).data('id'), $(this).data('label'));
						d.hide();
					});
				},
			});
		};
		d.fields_dict.go.$input.on('click', runSearch);
		d.show();
	}

	function updateApplyCount() {
		const n = state.candidates.filter((c) => ['Approve', 'Create New'].includes(c.decision) && c.status !== 'Linked').length;
		$body.find('.dlm-apply-count').text(n);
		$body.find('.dlm-apply').prop('disabled', n === 0);
	}

	function apply() {
		const targets = state.candidates.filter((c) => ['Approve', 'Create New'].includes(c.decision) && c.status !== 'Linked');
		if (!targets.length) { frappe.show_alert(__('Nothing approved to apply.')); return; }
		frappe.confirm(
			__('Link {0} record(s) to their Drive folders now? This writes the folder link and lets the two-way sync begin.', [targets.length]),
			() => {
				frappe.dom.freeze(__('Linking records…'));
				frappe.call({
					method: 'erpnext_enhancements.crm_enhancements.drive_link_manager.apply_links',
					callback: (r) => {
						frappe.dom.unfreeze();
						const m = (r && r.message) || {};
						frappe.msgprint({
							title: __('Apply complete'),
							indicator: (m.failed ? 'orange' : 'green'),
							message: __('Linked: {0} · Created: {1} · Failed: {2} · Skipped: {3}', [m.linked || 0, m.created || 0, m.failed || 0, m.skipped || 0]),
						});
						load();
					},
					error: () => { frappe.dom.unfreeze(); frappe.msgprint({ title: __('Apply failed'), message: __('See the Error Log.'), indicator: 'red' }); },
				});
			}
		);
	}

	load();
};
