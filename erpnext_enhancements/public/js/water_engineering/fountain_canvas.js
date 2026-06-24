/**
 * Shared "fountain design canvas" — pure functions that render a Water Feature
 * Design as an SVG from a plain state object. Used by the Water Feature Design
 * desk form (live preview) and intended to be reused verbatim by the Triton chat
 * walkthrough, so the desk and the AI show the SAME picture.
 *
 * No dependencies. Theme-aware: text uses Frappe CSS vars (so it flips with
 * Light / Timeless Night); structural + semantic colors are literal mid-tones
 * that read on either background.
 */
(function () {
	function esc(s) {
		return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
	}
	function num(v, dp) {
		return v == null || v === "" || v === 0 ? "—" : Number(v).toFixed(dp == null ? 2 : dp);
	}
	function statusColor(s) {
		s = (s || "").toLowerCase();
		if (s.indexOf("exceed") >= 0) return "#E24B4A";
		if (s.indexOf("increase") >= 0) return "#EF9F27";
		if (s.indexOf("okay") >= 0) return "#1D9E75";
		return "#888780";
	}
	function headAtFlow(curve, f) {
		const pts = (curve || [])
			.map((p) => [Number(p.flow_gpm) || 0, Number(p.head_ft) || 0])
			.sort((a, b) => a[0] - b[0]);
		if (!pts.length) return null;
		if (f > pts[pts.length - 1][0]) return null;
		if (f <= pts[0][0]) return pts[0][1];
		for (let i = 0; i < pts.length - 1; i++) {
			const [f0, h0] = pts[i],
				[f1, h1] = pts[i + 1];
			if (f0 <= f && f <= f1) return f1 > f0 ? h0 + (h1 - h0) * ((f - f0) / (f1 - f0)) : h0;
		}
		return pts[pts.length - 1][1];
	}

	// --- the fountain schematic -------------------------------------------------
	function canvasSvg(st) {
		st = st || {};
		const W = 680;
		const H = 250;
		const TX = "var(--text-color, #1f272e)";
		const TM = "var(--text-muted, #6c7680)";
		const STRUCT = "#9b9890";
		const WATER = "#5e9bd6";
		const JET = "#378ADD";
		const hasFeature = (st.feature_count || 0) > 0;
		const supply = statusColor(st.worst_status);

		// basin geometry
		const bx = 150,
			bw = 380,
			topY = 140,
			depth = 56;
		const botY = topY + depth;
		const cx = bx + bw / 2;
		const SPRAY = "#85B7EB";
		const kind = st.feature_kind || (hasFeature ? "jet" : null);
		const kindLabel = kind ? kind.replace(/_/g, " ") : "";

		let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Fountain design schematic">`;

		// metric strip
		s += `<text x="16" y="24" font-size="13" fill="${TX}">`;
		s += `<tspan font-weight="600">${esc(num(st.basin_gallons, 0))}</tspan><tspan fill="${TM}"> gal basin</tspan>`;
		s += `<tspan font-weight="600">   ·   ${esc(num(st.flow_gpm))}</tspan><tspan fill="${TM}"> GPM</tspan>`;
		s += `<tspan font-weight="600">   ·   ${esc(st.pump || "—")}</tspan><tspan fill="${TM}"> pump</tspan>`;
		s += `<tspan font-weight="600">   ·   ${esc(num(st.tdh_ft))}</tspan><tspan fill="${TM}"> ft TDH</tspan>${kind ? `<tspan fill="${TM}">   ·   ${esc(kindLabel)}</tspan>` : ""}</text>`;

		// ---- feature schematic + basin (drawn per feature kind) ----
		if (kind === "splash_pad") {
			// flat deck with ground jets — no deep basin
			s += `<rect x="${bx - 6}" y="${topY}" width="${bw + 12}" height="8" rx="2" fill="${STRUCT}"/>`;
			const heights = [34, 50, 38, 54, 36, 46, 40];
			for (let i = 0; i < heights.length; i++) {
				const jx = bx + 44 + (i * (bw - 88)) / (heights.length - 1);
				s += `<line x1="${jx}" y1="${topY}" x2="${jx}" y2="${topY - heights[i]}" stroke="${JET}" stroke-width="2.5" stroke-linecap="round"/>`;
				s += `<circle cx="${jx}" cy="${topY - heights[i] - 3}" r="2.5" fill="${SPRAY}"/>`;
				s += `<ellipse cx="${jx}" cy="${topY}" rx="7" ry="2.5" fill="${SPRAY}" opacity="0.5"/>`;
			}
			if (st.basin_label) s += `<text x="${cx}" y="${topY + 28}" font-size="11" fill="${TM}" text-anchor="middle">${esc(st.basin_label)}</text>`;
		} else {
			// feature above the basin
			if (kind === "tiered") {
				// a tiered (cascading) fountain — a stack of bowls, largest at the
				// bottom, water spilling tier to tier. Drawn from st.tiers (table top
				// = fountain top), so tier count + diameters are fully variable.
				const tiers = (st.tiers || []).filter((t) => (t.diameter_in || 0) > 0);
				const n = tiers.length;
				if (n) {
					const maxD = Math.max(...tiers.map((t) => t.diameter_in || 0)) || 1;
					const spacing = Math.min(26, (topY - 54) / n);
					const colTop = topY - 10 - (n - 1) * spacing - 6;
					s += `<rect x="${cx - 3}" y="${colTop}" width="6" height="${topY - colTop}" fill="${STRUCT}"/>`;
					for (let j = 0; j < n; j++) {
						const td = tiers[n - 1 - j];
						const ty = topY - 10 - j * spacing;
						const rx = Math.max(20, ((td.diameter_in || 0) / maxD) * 110);
						const below = j === 0 ? topY + 4 : topY - 10 - (j - 1) * spacing;
						s += `<path d="M${cx - rx} ${ty} Q ${cx} ${ty + 11} ${cx + rx} ${ty} Z" fill="${STRUCT}" opacity="0.45"/>`;
						s += `<ellipse cx="${cx}" cy="${ty}" rx="${rx}" ry="5" fill="${WATER}" opacity="0.85" stroke="#185FA5" stroke-width="1.5"/>`;
						s += `<path d="M${cx - rx} ${ty + 2} C ${cx - rx - 6} ${ty + 10} ${cx - rx + 2} ${below - 8} ${cx - rx + 8} ${below}" stroke="${WATER}" stroke-width="2.5" fill="none" opacity="0.7"/>`;
						s += `<path d="M${cx + rx} ${ty + 2} C ${cx + rx + 6} ${ty + 10} ${cx + rx - 2} ${below - 8} ${cx + rx - 8} ${below}" stroke="${WATER}" stroke-width="2.5" fill="none" opacity="0.7"/>`;
					}
					s += `<circle cx="${cx}" cy="${colTop - 2}" r="2.5" fill="${SPRAY}"/>`;
				}
			} else if (kind === "waterwall") {
				const wy = 42;
				s += `<rect x="${bx}" y="${wy}" width="${bw}" height="${topY - wy}" fill="${STRUCT}" opacity="0.4"/>`;
				s += `<rect x="${bx}" y="${wy}" width="${bw}" height="4" fill="#185FA5"/>`;
				for (let i = 0; i < 11; i++) {
					const lx = bx + 16 + (i * (bw - 32)) / 10;
					s += `<line x1="${lx}" y1="${wy + 4}" x2="${lx}" y2="${topY}" stroke="${WATER}" stroke-width="3" opacity="0.5"/>`;
				}
			} else if (kind === "rain_curtain") {
				const ry = 50;
				s += `<rect x="${bx}" y="${ry}" width="${bw}" height="8" rx="2" fill="${STRUCT}"/>`;
				for (let i = 0; i < 20; i++) {
					const lx = bx + 12 + (i * (bw - 24)) / 19;
					s += `<line x1="${lx}" y1="${ry + 8}" x2="${lx}" y2="${topY}" stroke="${WATER}" stroke-width="1.5" opacity="0.7"/>`;
				}
			} else if (kind === "weir" || kind === "spilling_weir") {
				const wallX = bx + bw - 10;
				const crestY = topY - 42;
				s += `<rect x="${wallX - 6}" y="${crestY}" width="12" height="${botY - crestY}" rx="1" fill="${STRUCT}"/>`;
				s += `<path d="M${wallX - 6} ${crestY} C ${wallX - 28} ${crestY + 12} ${wallX - 40} ${topY - 20} ${wallX - 46} ${topY}" stroke="${WATER}" stroke-width="6" fill="none" stroke-linecap="round" opacity="0.85"/>`;
				s += `<path d="M${wallX - 6} ${crestY + 5} C ${wallX - 24} ${crestY + 14} ${wallX - 34} ${topY - 18} ${wallX - 40} ${topY}" stroke="${SPRAY}" stroke-width="2" fill="none"/>`;
				s += `<g fill="${SPRAY}"><circle cx="${wallX - 46}" cy="${topY}" r="3"/><circle cx="${wallX - 52}" cy="${topY - 3}" r="2"/></g>`;
			} else if (kind === "jet") {
				const jetTop = 56;
				s += `<g stroke="${JET}" stroke-width="2.5" fill="none" stroke-linecap="round">`;
				s += `<line x1="${cx}" y1="${topY}" x2="${cx}" y2="${jetTop}"/>`;
				s += `<path d="M${cx} ${jetTop + 26} C ${cx - 20} ${jetTop} ${cx - 38} ${jetTop + 12} ${cx - 42} ${jetTop + 48}"/>`;
				s += `<path d="M${cx} ${jetTop + 26} C ${cx + 20} ${jetTop} ${cx + 38} ${jetTop + 12} ${cx + 42} ${jetTop + 48}"/>`;
				s += `</g>`;
				s += `<g fill="${SPRAY}"><circle cx="${cx - 42}" cy="${jetTop + 50}" r="3"/><circle cx="${cx + 42}" cy="${jetTop + 50}" r="3"/><circle cx="${cx}" cy="${jetTop - 4}" r="3"/></g>`;
				if (st.jet_height_ft) {
					s += `<line x1="${cx + 64}" y1="${jetTop}" x2="${cx + 64}" y2="${topY}" stroke="${STRUCT}" stroke-width="1" stroke-dasharray="4 4"/>`;
					s += `<text x="${cx + 70}" y="${(jetTop + topY) / 2}" font-size="11" fill="${TM}">jet ≈ ${esc(num(st.jet_height_ft, 1))} ft</text>`;
				}
			}
			// basin (coping, water, walls, floor)
			s += `<rect x="${bx - 6}" y="${topY - 6}" width="${bw + 12}" height="6" rx="2" fill="${STRUCT}"/>`;
			s += `<rect x="${bx}" y="${topY}" width="${bw}" height="${depth}" fill="${WATER}" opacity="0.85"/>`;
			s += `<line x1="${bx}" y1="${topY}" x2="${bx + bw}" y2="${topY}" stroke="#185FA5" stroke-width="2"/>`;
			s += `<rect x="${bx - 6}" y="${topY - 6}" width="6" height="${depth + 12}" rx="2" fill="${STRUCT}"/>`;
			s += `<rect x="${bx + bw}" y="${topY - 6}" width="6" height="${depth + 12}" rx="2" fill="${STRUCT}"/>`;
			s += `<rect x="${bx - 6}" y="${botY}" width="${bw + 12}" height="6" rx="2" fill="${STRUCT}"/>`;
			if (kind === "jet") s += `<rect x="${cx - 7}" y="${topY - 4}" width="14" height="6" rx="2" fill="#5F5E5A"/>`;
			if (st.basin_label) s += `<text x="${cx}" y="${botY + 22}" font-size="11" fill="${TM}" text-anchor="middle">${esc(st.basin_label)}</text>`;
		}

		// pump + supply riser + suction
		const px = 30,
			py = botY + 4,
			pw = 104,
			ph = 28;
		s += `<rect x="${px}" y="${py}" width="${pw}" height="${ph}" rx="5" fill="none" stroke="${STRUCT}" stroke-width="1.5"/>`;
		s += `<text x="${px + pw / 2}" y="${py + 18}" font-size="11" font-weight="600" fill="${TX}" text-anchor="middle">${esc(st.pump || "Pump —")}</text>`;
		s += `<path d="M${px + pw / 2} ${py} V ${botY + 2} H ${cx} V ${topY}" stroke="${supply}" stroke-width="5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`;
		s += `<path d="M${bx + bw + 0} ${botY} V ${py + ph + 6} H ${px + pw} V ${py + ph}" stroke="${STRUCT}" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="2 5"/>`;

		// legend
		const ly = H - 10;
		s += `<g font-size="11" fill="${TM}">`;
		s += `<circle cx="${bx + 6}" cy="${ly - 3}" r="5" fill="#1D9E75"/><text x="${bx + 16}" y="${ly}">Okay</text>`;
		s += `<circle cx="${bx + 70}" cy="${ly - 3}" r="5" fill="#EF9F27"/><text x="${bx + 80}" y="${ly}">Increase</text>`;
		s += `<circle cx="${bx + 158}" cy="${ly - 3}" r="5" fill="#E24B4A"/><text x="${bx + 168}" y="${ly}">Exceeds legal</text>`;
		s += `</g>`;

		s += `</svg>`;
		return s;
	}

	// --- pump duty-point on its performance curve -------------------------------
	function dutySvg(st) {
		st = st || {};
		const curve = (st.curve || []).slice().sort((a, b) => (a.flow_gpm || 0) - (b.flow_gpm || 0));
		const W = 320;
		const H = 190;
		const TX = "var(--text-color, #1f272e)";
		const TM = "var(--text-muted, #6c7680)";
		const m = { l: 38, r: 12, t: 14, b: 28 };
		const pw = W - m.l - m.r;
		const ph = H - m.t - m.b;

		if (!curve.length) {
			return (
				`<svg viewBox="0 0 ${W} ${H}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="No pump curve on file">` +
				`<text x="${W / 2}" y="${H / 2 - 6}" font-size="12" fill="${TM}" text-anchor="middle">No curve on file for ${esc(st.pump || "this pump")}.</text>` +
				`<text x="${W / 2}" y="${H / 2 + 12}" font-size="11" fill="${TM}" text-anchor="middle">Add Pump Curve points on the Item to size on the curve.</text></svg>`
			);
		}

		const maxF = Math.max(curve[curve.length - 1].flow_gpm || 0, st.duty_flow || 0) * 1.1 || 1;
		const maxH = Math.max(...curve.map((p) => p.head_ft || 0), st.duty_head || 0) * 1.15 || 1;
		const X = (f) => m.l + (f / maxF) * pw;
		const Y = (h) => m.t + ph - (h / maxH) * ph;

		let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Pump duty point on its curve">`;
		// axes
		s += `<line x1="${m.l}" y1="${m.t}" x2="${m.l}" y2="${m.t + ph}" stroke="${TM}" stroke-width="1"/>`;
		s += `<line x1="${m.l}" y1="${m.t + ph}" x2="${m.l + pw}" y2="${m.t + ph}" stroke="${TM}" stroke-width="1"/>`;
		s += `<text x="${m.l - 4}" y="${m.t + 8}" font-size="10" fill="${TM}" text-anchor="end">${num(maxH, 0)}</text>`;
		s += `<text x="${m.l}" y="${H - 8}" font-size="10" fill="${TM}" text-anchor="middle">0</text>`;
		s += `<text x="${m.l + pw}" y="${H - 8}" font-size="10" fill="${TM}" text-anchor="middle">${num(maxF, 0)} GPM</text>`;
		// curve
		const path = curve.map((p, i) => `${i ? "L" : "M"}${X(p.flow_gpm).toFixed(1)} ${Y(p.head_ft).toFixed(1)}`).join(" ");
		s += `<path d="${path}" stroke="#185FA5" stroke-width="2" fill="none"/>`;
		curve.forEach((p) => (s += `<circle cx="${X(p.flow_gpm).toFixed(1)}" cy="${Y(p.head_ft).toFixed(1)}" r="2.5" fill="#185FA5"/>`));
		// duty point
		if (st.duty_flow && st.duty_head) {
			const hAt = headAtFlow(curve, st.duty_flow);
			const ok = hAt != null && hAt >= st.duty_head;
			const col = ok ? "#1D9E75" : "#E24B4A";
			const dx = X(st.duty_flow),
				dy = Y(st.duty_head);
			s += `<line x1="${m.l}" y1="${dy.toFixed(1)}" x2="${dx.toFixed(1)}" y2="${dy.toFixed(1)}" stroke="${col}" stroke-width="1" stroke-dasharray="3 3"/>`;
			s += `<line x1="${dx.toFixed(1)}" y1="${(m.t + ph).toFixed(1)}" x2="${dx.toFixed(1)}" y2="${dy.toFixed(1)}" stroke="${col}" stroke-width="1" stroke-dasharray="3 3"/>`;
			s += `<circle cx="${dx.toFixed(1)}" cy="${dy.toFixed(1)}" r="5" fill="${col}"/>`;
			s += `<text x="${Math.min(dx + 8, W - 4).toFixed(1)}" y="${Math.max(dy - 8, m.t + 10).toFixed(1)}" font-size="11" font-weight="600" fill="${TX}" text-anchor="end">duty ${num(st.duty_flow, 0)} GPM @ ${num(st.duty_head, 1)} ft</text>`;
			s += `<text x="${W - 4}" y="${H - 8}" font-size="11" fill="${col}" text-anchor="end" font-weight="600">${ok ? "on curve" : "below curve"}</text>`;
		}
		s += `</svg>`;
		return s;
	}

	window.WaterFountain = { canvasSvg, dutySvg, statusColor };
})();
