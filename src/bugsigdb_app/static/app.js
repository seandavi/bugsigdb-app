// Progressive enhancement only: every page works without this file.

// Search-as-you-type on inputs marked data-suggest: taxa, conditions and studies, grouped.
// Arrow keys move through results; Enter without a selection submits the form (full results page).
function suggest(input) {
  const list = document.createElement("ul");
  list.className = "suggest";
  list.id = input.id + "-list";
  list.setAttribute("role", "listbox");
  list.hidden = true;
  input.after(list);
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-controls", list.id);
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-autocomplete", "list");

  const LABELS = { taxon: "Taxa", condition: "Conditions", study: "Studies" };
  const italic = new Set(["genus", "species", "strain"]);
  let options = [], active = -1, timer, seq = 0;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
  const highlight = (i) => {
    active = i;
    options.forEach((o, j) => o.li.setAttribute("aria-selected", j === i ? "true" : "false"));
    if (i >= 0) { input.setAttribute("aria-activedescendant", options[i].li.id); options[i].li.scrollIntoView({ block: "nearest" }); }
  };
  const option = (url, children) => {
    const li = el("li");
    li.id = `${list.id}-${options.length}`;
    li.setAttribute("role", "option");
    li.append(...children);
    li.addEventListener("mousedown", (e) => { e.preventDefault(); location.href = url; });
    options.push({ li, url });
    return li;
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return close();
    timer = setTimeout(async () => {
      const mine = ++seq;
      const res = await fetch("/api/search?per_kind=4&q=" + encodeURIComponent(q));
      if (!res.ok || mine !== seq) return;
      const r = await res.json();
      options = [];
      const items = [];
      if (r.redirect) items.push(option(r.redirect, [el("span", "lbl", "Go to " + q)]));
      for (const g of r.groups) {
        const head = el("li", "grp", `${LABELS[g.kind]} · ${g.total.toLocaleString()}`);
        head.setAttribute("role", "presentation");
        items.push(head);
        for (const x of g.results) {
          // study titles may carry <i> markup; show them as plain text here
          const label = el("span", "lbl" + (x.kind === "taxon" && italic.has(x.sub) ? " it" : ""), x.label.replace(/<[^>]+>/g, ""));
          const extra = x.kind === "taxon" ? el("span", "rank rank-" + (x.sub || ""), x.sub || "")
            : el("span", "n", x.kind === "condition" ? x.key : (x.sub || ""));
          items.push(option(x.url, [label, extra, el("span", "n", x.weight.toLocaleString() + " sig.")]));
        }
      }
      if (r.groups.length) items.push(option("/search?q=" + encodeURIComponent(q), [el("span", "all", "All results for “" + q + "” →")]));
      list.replaceChildren(...items);
      list.hidden = !items.length;
      input.setAttribute("aria-expanded", String(!!items.length));
      active = -1;
    }, 120);
  });
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); highlight(Math.min(active + 1, options.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); highlight(Math.max(active - 1, -1)); }
    else if (e.key === "Enter" && active >= 0) { e.preventDefault(); location.href = options[active].url; }
    else if (e.key === "Escape") close();
  });
  input.addEventListener("blur", () => setTimeout(close, 100));
}

// Filterable tables ([data-sigtable] or [data-filtertable]): text filter, plus on the taxon
// card a direction segment and a "this taxon only" toggle. All controls are optional.
function filterable(root) {
  const rows = [...root.querySelectorAll("tbody tr")];
  const text = rows.map((r) => r.textContent.toLowerCase());
  const q = root.querySelector("[data-filter]");
  const direct = root.querySelector("[data-direct]");
  const count = root.querySelector("[data-count]");
  const original = count && count.innerHTML;
  const buttons = [...root.querySelectorAll("button[data-dir]")];
  let dir = "";
  const apply = () => {
    const needle = q ? q.value.trim().toLowerCase() : "";
    let shown = 0;
    rows.forEach((r, i) => {
      const ok = (!needle || text[i].includes(needle))
        && (!dir || r.dataset.dir === dir)
        && (!direct || !direct.checked || r.dataset.direct === "1");
      r.hidden = !ok;
      shown += ok;
    });
    if (count) {
      const filtered = needle || dir || (direct && direct.checked);
      count.innerHTML = filtered ? `${shown.toLocaleString()} of ${rows.length.toLocaleString()} rows match.` : original;
    }
  };
  if (q) q.addEventListener("input", apply);
  if (direct) direct.addEventListener("change", apply);
  buttons.forEach((b) => b.addEventListener("click", () => {
    dir = b.dataset.dir;
    buttons.forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    apply();
  }));
}

document.querySelectorAll("[data-suggest]").forEach(suggest);
document.querySelectorAll("[data-sigtable], [data-filtertable]").forEach(filterable);
