#!/usr/bin/env node
/**
 * Guards the capture annotator's pure helpers (WI-079 slice 2): geometry, the undo list, crop
 * arithmetic and — the one that matters most — the blur mosaic.
 *
 * **Why the blur gets the most assertions.** The acceptance line is "a blurred region is
 * unrecoverable in the uploaded file". The annotator burns a mosaic into the exported pixels,
 * so the property to hold is arithmetic: every block inside the rectangle is one flat color,
 * nothing outside it moves, the edge blocks are covered too, and the noise that defeats
 * font-matching de-pixelation is deterministic per op (so undo/replay redraws the same
 * mosaic) but never zero. A regression here does not look like a bug — the preview still
 * shows a mosaic — which is exactly why it is pinned in CI rather than eyeballed.
 *
 * Loads `annotate.js` directly: its helpers take plain numbers and typed arrays and it
 * touches no DOM at import time. If it ever grows a top-level browser dependency this script
 * fails loudly at import rather than silently asserting nothing (CLAUDE.md: a suite that
 * quietly stops running is worse than none).
 *
 * Run: node scripts/test_capture_annotate.js
 */

const path = require("path");
const { pathToFileURL } = require("url");

const TARGET = path.join(__dirname, "..", "erpnext_enhancements", "public", "js", "capture", "annotate.js");

let failures = 0;

function check(label, actual, expected) {
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a === e) {
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.error(`  FAIL ${label}: got ${a}, want ${e}`);
	}
}

function truthy(label, value) {
	check(label, !!value, true);
}

/** RGBA buffer filled by fn(x, y) -> [r, g, b, a]. */
function image(width, height, fn) {
	const data = new Uint8ClampedArray(width * height * 4);
	for (let y = 0; y < height; y++) {
		for (let x = 0; x < width; x++) {
			const [r, g, b, a] = fn(x, y);
			const i = (y * width + x) * 4;
			data[i] = r;
			data[i + 1] = g;
			data[i + 2] = b;
			data[i + 3] = a;
		}
	}
	return data;
}

function px(data, width, x, y) {
	const i = (y * width + x) * 4;
	return [data[i], data[i + 1], data[i + 2], data[i + 3]];
}

(async () => {
	let A;
	try {
		A = await import(pathToFileURL(TARGET).href);
	} catch (err) {
		console.error("COULD NOT LOAD annotate.js — it must stay importable without a DOM.");
		console.error(err && err.message);
		process.exit(2);
	}

	const needed = [
		"openAnnotator",
		"exportSize",
		"fitScale",
		"normRect",
		"clampRect",
		"toImagePoint",
		"arrowHead",
		"pixelate",
		"mulberry32",
		"buildOp",
		"dimsAfter",
		"createHistory",
		"inkWidth",
		"textSize",
		"blurBlock",
	];
	for (const name of needed) {
		if (typeof A[name] !== "function") {
			console.error(`MARKER NOT FOUND: annotate.js no longer exports ${name}()`);
			process.exit(2);
		}
	}

	console.log("\nexport is capped at 2560px on the long edge, aspect kept");
	check("small image unchanged", A.exportSize(1280, 720), { w: 1280, h: 720, scale: 1 });
	check("wide image capped", A.exportSize(5120, 2880), { w: 2560, h: 1440, scale: 0.5 });
	check("tall image capped", A.exportSize(1000, 4000), { w: 640, h: 2560, scale: 0.64 });
	check("exactly the cap is unchanged", A.exportSize(2560, 100).w, 2560);
	check("empty image", A.exportSize(0, 10), { w: 0, h: 0, scale: 0 });
	check("NaN image", A.exportSize(NaN, 10), { w: 0, h: 0, scale: 0 });
	check("MAX_EXPORT_EDGE", A.MAX_EXPORT_EDGE, 2560);

	console.log("\ndisplay scale fits the box and never enlarges");
	check("fits width", A.fitScale(2000, 1000, 500, 1000), 0.25);
	check("fits height", A.fitScale(1000, 2000, 1000, 500), 0.25);
	check("small image stays 1:1", A.fitScale(300, 200, 1000, 1000), 1);
	check("missing box is 1", A.fitScale(300, 200, 0, 0), 1);

	console.log("\nrectangles");
	check("drag up-left normalizes", A.normRect(50, 40, 10, 5), { x: 10, y: 5, w: 40, h: 35 });
	check("snaps outward", A.clampRect({ x: 1.5, y: 2.2, w: 3, h: 3 }, 100, 100), { x: 1, y: 2, w: 4, h: 4 });
	check("clipped to the image", A.clampRect({ x: -10, y: 90, w: 30, h: 30 }, 100, 100), { x: 0, y: 90, w: 20, h: 10 });
	check("fully outside is null", A.clampRect({ x: 200, y: 0, w: 10, h: 10 }, 100, 100), null);
	check("zero size is null", A.clampRect({ x: 5, y: 5, w: 0, h: 10 }, 100, 100), null);
	check("NaN is null", A.clampRect({ x: NaN, y: 0, w: 1, h: 1 }, 100, 100), null);

	console.log("\npointer to image pixels, through any CSS scale");
	const box = { left: 100, top: 50, width: 400, height: 200 };
	check("center of a half-size display", A.toImagePoint(300, 150, box, 800, 400), { x: 400, y: 200 });
	check("clamped left/top", A.toImagePoint(0, 0, box, 800, 400), { x: 0, y: 0 });
	check("clamped right/bottom", A.toImagePoint(10000, 10000, box, 800, 400), { x: 800, y: 400 });
	check("zero-size box does not divide by zero", A.toImagePoint(5, 5, { left: 0, top: 0, width: 0, height: 0 }, 10, 10), {
		x: 10,
		y: 10,
	});

	console.log("\narrowhead points back from the tip");
	const head = A.arrowHead(0, 0, 100, 0, 20);
	truthy("left corner behind the tip", head.left[0] < 100);
	truthy("right corner behind the tip", head.right[0] < 100);
	check("corners mirror each other", Math.round(head.left[1] + head.right[1]), 0);
	check("shaft stops short of the tip", Math.round(head.base[0]), 84);

	console.log("\nsizes scale with the image, inside sane bounds");
	check("ink on a 1280 screenshot", A.inkWidth(1280, 720), 5);
	check("ink floor", A.inkWidth(200, 100), 3);
	check("ink ceiling", A.inkWidth(100000, 10), 12);
	check("text floor", A.textSize(200, 100), 16);
	check("text ceiling", A.textSize(100000, 10), 72);
	check("blur block floor is 10", A.blurBlock(320, 200), 10);
	check("blur block on a 2560 capture", A.blurBlock(2560, 1440), 40);
	check("blur block ceiling", A.blurBlock(100000, 10), 48);

	console.log("\nthe PRNG is seeded and bounded");
	const r1 = A.mulberry32(42);
	const r2 = A.mulberry32(42);
	const seqA = [r1(), r1(), r1()];
	const seqB = [r2(), r2(), r2()];
	check("same seed, same sequence", seqA, seqB);
	truthy("values in [0, 1)", seqA.every((v) => v >= 0 && v < 1));
	truthy("different seed, different sequence", A.mulberry32(43)() !== seqA[0]);

	console.log("\nblur: plain mosaic arithmetic (no noise)");
	{
		const W = 20;
		const H = 20;
		// A 1px checkerboard: the highest-frequency detail an image can hold, i.e. text.
		const data = image(W, H, (x, y) => ((x + y) % 2 ? [255, 255, 255, 255] : [0, 0, 0, 255]));
		const before = Uint8ClampedArray.from(data);
		const blocks = A.pixelate(data, W, H, { x: 5, y: 5, w: 10, h: 10 }, 5, null);
		check("four 5px blocks in a 10px square", blocks, 4);
		check("block is the average (12 white of 25 -> 122)", px(data, W, 5, 5), [122, 122, 122, 255]);
		let flat = true;
		for (const [bx, by] of [
			[5, 5],
			[10, 5],
			[5, 10],
			[10, 10],
		]) {
			const first = JSON.stringify(px(data, W, bx, by));
			for (let y = by; y < by + 5; y++) for (let x = bx; x < bx + 5; x++) if (JSON.stringify(px(data, W, x, y)) !== first) flat = false;
		}
		truthy("every block is one flat color", flat);
		let outsideUntouched = true;
		for (let y = 0; y < H; y++) {
			for (let x = 0; x < W; x++) {
				if (x >= 5 && x < 15 && y >= 5 && y < 15) continue;
				const i = (y * W + x) * 4;
				for (let c = 0; c < 4; c++) if (data[i + c] !== before[i + c]) outsideUntouched = false;
			}
		}
		truthy("nothing outside the rectangle moved", outsideUntouched);
	}

	console.log("\nblur: edge blocks are covered, alpha averaged, out-of-range clipped");
	{
		const W = 12;
		const H = 3;
		const data = image(W, H, (x) => (x < 6 ? [0, 0, 0, 0] : [200, 100, 50, 255]));
		const blocks = A.pixelate(data, W, H, { x: 0, y: 0, w: 7, h: 3 }, 5, null);
		check("a 7px-wide rectangle is two blocks (5 + a 2px remainder)", blocks, 2);
		check("the remainder block is flattened too", px(data, W, 5, 0), px(data, W, 6, 2));
		check("remainder averages transparent + opaque alpha", px(data, W, 6, 0)[3], 128);
		check("pixel past the rectangle untouched", px(data, W, 7, 0), [200, 100, 50, 255]);
		const clipped = image(4, 4, () => [10, 20, 30, 255]);
		check("rectangle hanging off the image is clipped, not thrown", A.pixelate(clipped, 4, 4, { x: 2, y: 2, w: 50, h: 50 }, 10), 1);
		check("rectangle fully outside writes nothing", A.pixelate(clipped, 4, 4, { x: 9, y: 9, w: 5, h: 5 }, 10), 0);
		check("short buffer is refused, not overrun", A.pixelate(new Uint8ClampedArray(8), 4, 4, { x: 0, y: 0, w: 4, h: 4 }, 2), 0);
	}

	console.log("\nblur: the noise that defeats de-pixelation");
	{
		const W = 40;
		const H = 40;
		const make = () => image(W, H, (x, y) => ((x * 7 + y * 3) % 11 < 5 ? [240, 240, 240, 255] : [20, 20, 20, 255]));
		const rect = { x: 0, y: 0, w: 40, h: 40 };
		const plain = make();
		A.pixelate(plain, W, H, rect, 10, null);
		const noisyA = make();
		A.pixelate(noisyA, W, H, rect, 10, A.mulberry32(1234));
		const noisyB = make();
		A.pixelate(noisyB, W, H, rect, 10, A.mulberry32(1234));
		check("same seed replays the same mosaic (undo/redo is stable)", Array.from(noisyA), Array.from(noisyB));

		let within = true;
		let shifted = 0;
		for (let by = 0; by < H; by += 10) {
			for (let bx = 0; bx < W; bx += 10) {
				const p = px(plain, W, bx, by);
				const n = px(noisyA, W, bx, by);
				if (n[0] !== p[0]) shifted += 1;
				for (let c = 0; c < 3; c++) if (Math.abs(n[c] - p[c]) > A.BLUR_JITTER + 1) within = false;
				// One offset across the color channels: texture, not confetti.
				if (n[0] - p[0] !== n[1] - p[1] || n[1] - p[1] !== n[2] - p[2]) within = false;
			}
		}
		truthy("noise stays within BLUR_JITTER and is the same on r, g and b", within);
		truthy("most blocks are shifted away from the exact average", shifted >= 12);
		let blocksFlat = true;
		for (let by = 0; by < H; by += 10) {
			for (let bx = 0; bx < W; bx += 10) {
				const first = JSON.stringify(px(noisyA, W, bx, by));
				for (let y = by; y < by + 10; y++) for (let x = bx; x < bx + 10; x++) if (JSON.stringify(px(noisyA, W, x, y)) !== first) blocksFlat = false;
			}
		}
		truthy("noisy blocks are still flat inside", blocksFlat);
	}

	console.log("\nbuilding ops from a drag");
	const dims = { w: 1000, h: 500 };
	const p = (x, y) => ({ x, y });
	check("a stray tap with the box tool is nothing", A.buildOp("box", p(10, 10), p(12, 11), [], dims, 0), null);
	const boxOp = A.buildOp("box", p(100, 100), p(20, 40), [], dims, 0);
	check("box normalized", boxOp && boxOp.rect, { x: 20, y: 40, w: 80, h: 60 });
	check("box carries a stroke width", boxOp && boxOp.width, A.inkWidth(1000, 500));
	const blurOp = A.buildOp("blur", p(-50, -50), p(30, 30), [], dims, 99);
	check("blur clipped to the image", blurOp && blurOp.rect, { x: 0, y: 0, w: 30, h: 30 });
	check("blur keeps its seed for replay", blurOp && blurOp.seed, 99);
	check("blur block sized to the image", blurOp && blurOp.block, A.blurBlock(1000, 500));
	check("crop below 16px is nothing", A.buildOp("crop", p(0, 0), p(15, 100), [], dims, 0), null);
	check("crop to the whole image is nothing", A.buildOp("crop", p(-5, -5), p(2000, 2000), [], dims, 0), null);
	check("crop", A.buildOp("crop", p(100, 50), p(500, 250), [], dims, 0), { type: "crop", rect: { x: 100, y: 50, w: 400, h: 200 } });
	check("an arrow shorter than 8px is nothing", A.buildOp("arrow", p(0, 0), p(3, 3), [], dims, 0), null);
	check("arrow keeps its direction", A.buildOp("arrow", p(10, 10), p(90, 10), [], dims, 0).to, { x: 90, y: 10 });
	check("pen tap is a dot", A.buildOp("pen", p(5.55, 5), p(5.55, 5), [], dims, 0).points, [{ x: 5.6, y: 5 }]);
	check("unknown tool is nothing", A.buildOp("laser", p(0, 0), p(50, 50), [], dims, 0), null);
	check("no image is nothing", A.buildOp("box", p(0, 0), p(50, 50), [], { w: 0, h: 0 }, 0), null);

	console.log("\ncrops compose: each is relative to the image after the previous one");
	const crops = [
		{ type: "crop", rect: { x: 100, y: 100, w: 800, h: 300 } },
		{ type: "box", rect: { x: 0, y: 0, w: 10, h: 10 } },
		{ type: "crop", rect: { x: 700, y: 0, w: 500, h: 500 } },
	];
	check("second crop clipped to the first crop's result", A.dimsAfter(1000, 500, crops), { w: 100, h: 300 });
	check("no crops, no change", A.dimsAfter(640, 480, [{ type: "blur" }]), { w: 640, h: 480 });

	console.log("\nundo is a list, and nothing outside can change it");
	const history = A.createHistory();
	history.push({ type: "box" });
	history.push({ type: "blur" });
	history.push(null);
	check("null ops are ignored", history.size, 2);
	const snapshot = history.list();
	snapshot.push({ type: "crop" });
	check("list() is a copy", history.size, 2);
	check("undo pops the newest", history.undo(), { type: "blur" });
	check("then the next", history.undo(), { type: "box" });
	check("empty undo is null", history.undo(), null);

	console.log("\nthe stylesheet stays inside the ee-cap- prefix");
	const classes = (A.ANNOTATOR_CSS.match(/\.[a-zA-Z][\w-]*/g) || []).filter((c) => !/^\.\d/.test(c));
	const stray = classes.filter((c) => !c.startsWith(".ee-cap-"));
	check("every class selector is ee-cap-*", stray, []);
	truthy("controls are at least the shared tap size", /min-width:\s*var\(--ee-cap-tap\)/.test(A.ANNOTATOR_CSS));

	console.log("");
	if (failures) {
		console.error(failures + " assertion(s) failed");
		process.exit(1);
	}
	console.log("capture annotator: all assertions passed");
})();
