// Keyboard shortcuts: a pure key → action mapping (unit-tested under Node) used by app.js.
// Actions: {focus:"#q"} | {go:"#/…"} | {help:true} | {blur:true} | {review:"reveal"|grade} | null.
const KEYS_NAV = { h: "#/", a: "#/ask", g: "#/graph", d: "#/doubts", r: "#/review", w: "#/digest" };
const KEYS_HELP = [
  ["/", "focus search"], ["Esc", "leave a text box / close this help"], ["?", "show or hide this help"],
  ["h", "home"], ["a", "ask the desk"], ["g", "graph"], ["d", "doubts"], ["r", "review cards"], ["w", "weekly digest"],
  ["Space / Enter", "review: show answer"], ["0–5", "review: grade the card"],
];
function keyAction(ev) {
  // ev: {key, ctrl, meta, alt, typing, path, reviewing, shown}
  if (ev.ctrl || ev.meta || ev.alt) return null;
  if (ev.key === "Escape") return ev.typing ? { blur: true } : { help: false };
  if (ev.typing) return null;
  if (ev.key === "/") return { focus: "#q" };
  if (ev.key === "?") return { help: "toggle" };
  if (ev.path === "/review" && ev.reviewing) {
    if ((ev.key === " " || ev.key === "Enter") && !ev.shown) return { review: "reveal" };
    if (/^[0-5]$/.test(ev.key) && ev.shown) return { review: +ev.key };
  }
  if (Object.prototype.hasOwnProperty.call(KEYS_NAV, ev.key)) return { go: KEYS_NAV[ev.key] };
  return null;
}
if (typeof module !== "undefined") module.exports = { keyAction, KEYS_NAV, KEYS_HELP };
