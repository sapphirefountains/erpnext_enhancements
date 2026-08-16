#!/usr/bin/env node
/**
 * Differential fuzz, half one: generate hostile URLs and record what the CLIENT says.
 *
 * Piped into `scripts/fuzz_url_safety_check.py`, which computes the server verdict and fails
 * the build on the vulnerability direction only — server says safe, browser does not.
 *
 * WHY THIS EXISTS, AND WHY A CORPUS IS NOT ENOUGH
 * ----------------------------------------------
 * The bug this whole boundary answers is a parser-differential bug, and the pre-v1.282.3
 * check HAD a corpus. The corpus simply did not contain `/\`. Hand-picked rows prove the
 * cases somebody already thought of, which is exactly the set that does not contain the next
 * vulnerability.
 *
 * That is not a hypothetical here. While building the server predicate, a "faithful port" of
 * isSafeUrl passed 31 hand-picked rows and was then fuzzed against this file's `isSafeUrl`:
 * **685 inputs where the server said safe and the browser did not**, almost all malformed
 * authorities like `HTTP:\user@evil.example/`, where Python reports no userinfo and the
 * browser reads a host. The corpus caught none of them. This did.
 *
 * The verdict oracle is the REAL `isSafeUrl` imported from the shipping client source, not a
 * copy — a copy would drift from the thing it is supposed to be measuring, which is the
 * failure mode one layer up.
 *
 * Deterministic: the seed is fixed and printed, so a red build is replayable. Pass a seed as
 * argv[2] to reproduce one.
 */
import { isSafeUrl } from "../erpnext_enhancements/public/js/chat/citations.js";

const SEED = Number(process.argv[2] || 20260815);

// xorshift32 — tiny, deterministic, and no dependency. Randomness quality is irrelevant here;
// reproducibility is the whole point.
let state = SEED >>> 0 || 1;
function rnd() {
	state ^= state << 13;
	state ^= state >>> 17;
	state ^= state << 5;
	state >>>= 0;
	return state / 0x100000000;
}
function pick(arr) {
	return arr[Math.floor(rnd() * arr.length)];
}

// The alphabet is deliberately the set of characters a URL parser TREATS SPECIALLY rather
// than a random unicode sample: separators, the ones that get stripped, and the ones that
// change how an authority is read.
const SCHEMES = ["", "http:", "https:", "HTTP:", "HttPs:", "javascript:", "JaVaScRiPt:", "data:", "vbscript:", "file:", "blob:", "mailto:", "ftp:"];
const SLASHES = ["", "/", "//", "///", "\\", "/\\", "\\/", "/\\/", "\\\\", "//\\", "/\\\\"];
const HOSTS = ["", "ok.example", "evil.example", "user@evil.example", "user:pw@evil.example", "127.0.0.1", "0x7f.1", "[::1]", "xn--", "xn--a", "url-safety-check.invalid", "ok.example.", "-ok.example", "ok..example"];
const PORTS = ["", ":80", ":443", ":8000", ":99999", ":abc", ":"];
const TAILS = ["", "/", "/a", "/a?b=c", "/a#f", "?q=1", "#f", "/%2f%2fevil.example", "/a\\b"];
const NOISE = ["", " ", "\t", "\n", "\r", "\x00", "\x01", "\x7f", " ", "﻿", "。", "／", "＃"];

function generate() {
	let value = pick(SCHEMES) + pick(SLASHES) + pick(HOSTS) + pick(PORTS) + pick(TAILS);
	// Splice noise in at a random offset a third of the time — leading noise is stripped by
	// the parser, interior noise sometimes is too, and that asymmetry is where bugs live.
	if (rnd() < 0.34) {
		const at = Math.floor(rnd() * (value.length + 1));
		value = value.slice(0, at) + pick(NOISE) + value.slice(at);
	}
	return value;
}

const COUNT = Number(process.env.FUZZ_COUNT || 200000);
const seen = new Set();
const lines = [];

for (let i = 0; i < COUNT; i++) {
	const value = generate();
	if (seen.has(value)) continue;
	seen.add(value);
	// Codepoints, never a raw string: this output crosses a pipe into Python, and the whole
	// corpus is made of the characters that get mangled when they do.
	lines.push(JSON.stringify({ cp: Array.from(value, (c) => c.codePointAt(0)), js: isSafeUrl(value) }));
}

process.stdout.write(`#seed ${SEED}\n`);
process.stdout.write(lines.join("\n") + "\n");
