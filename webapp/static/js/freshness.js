// Shared "how fresh is this?" helpers. Every timestamp the API sends is a real
// UTC instant; it is shown in the viewer's own timezone with the zone name, plus
// a live-updating age ("12 min ago"). Ages are measured against the SERVER's
// clock (offset captured from each API response), so a wrong laptop clock cannot
// make fresh data look stale or vice versa.
const Freshness = (() => {
  let serverOffsetMs = 0; // serverNow - clientNow

  function syncServerClock(serverNowIso) {
    const t = Date.parse(serverNowIso);
    if (!Number.isNaN(t)) serverOffsetMs = t - Date.now();
    document.querySelectorAll("[data-ts]").forEach(render); // re-measure ages already on screen
  }

  const nowMs = () => Date.now() + serverOffsetMs;

  function fmtDateTime(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "unknown time";
    return d.toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", timeZoneName: "short",
    });
  }

  function fmtAge(iso) {
    const secs = Math.max(0, Math.round((nowMs() - Date.parse(iso)) / 1000));
    if (secs < 45) return "just now";
    const mins = Math.round(secs / 60);
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 48) return `${hrs} h ${mins % 60} min ago`;
    return `${Math.floor(hrs / 24)} d ${hrs % 24} h ago`;
  }

  // Fills `el` with "<label> <local time> (<age>)" and keeps the age current.
  // iso == null renders an explicit "not recorded yet", never a guess.
  function stamp(el, iso, { label = "", overdue = false } = {}) {
    if (!el) return;
    el.dataset.ts = iso || "";
    el.dataset.label = label;
    el.classList.toggle("stale", !!overdue && !!iso);
    render(el);
  }

  function render(el) {
    const iso = el.dataset.ts;
    const label = el.dataset.label ? `${el.dataset.label} ` : "";
    if (!iso) {
      el.textContent = `${label}not recorded yet`;
      el.removeAttribute("title");
      return;
    }
    el.textContent = `${label}${fmtDateTime(iso)} · ${fmtAge(iso)}`;
    el.title = new Date(iso).toISOString();
  }

  setInterval(() => document.querySelectorAll("[data-ts]").forEach(render), 30000);

  return { syncServerClock, fmtDateTime, fmtAge, stamp };
})();
