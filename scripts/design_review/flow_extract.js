// Pulls the click-through tables out of the claude.ai Concept Viewer script as JSON, regexes as source strings.
// The tables (NEXT .. NOINHERIT) are one contiguous run of literal `var` declarations, so the run is
// evaluated on its own, with nothing from the page around it.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const start = src.indexOf("var NEXT = {");
const stop = src.indexOf("\n", src.indexOf("var NOINHERIT = "));
if (start < 0 || stop < 0) throw new Error("rule tables not found");
const tables = new Function(src.slice(start, stop) + "\nreturn { NEXT, CHAIN, BACKTO, NAV, TEXT, ICON, NOINHERIT };")();
const conv = (v) =>
  v instanceof RegExp ? v.source
  : Array.isArray(v) ? v.map(conv)
  : v && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, conv(x)]))
  : v;
process.stdout.write(JSON.stringify({
  next: conv(tables.NEXT), chain: conv(tables.CHAIN), backto: conv(tables.BACKTO),
  nav: conv(tables.NAV), text: conv(tables.TEXT), icon: conv(tables.ICON), noinherit: tables.NOINHERIT,
}));
