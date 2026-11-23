// Progressive enhancement only: every page works without this file.

// Search-as-you-type on inputs marked data-suggest. Enter without a selection submits the form.
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

  let items = [], active = -1, timer, seq = 0;
  const italic = new Set(["genus", "species", "strain"]);
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
  const go = (t) => { location.href = "/taxon/" + t.tax_id; };
  const highlight = (i) => {
    active = i;
    [...list.children].forEach((li, j) => li.setAttribute("aria-selected", j === i ? "true" : "false"));
    if (i >= 0) input.setAttribute("aria-activedescendant", list.children[i].id);
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return close();
    timer = setTimeout(async () => {
      const mine = ++seq;
      const res = await fetch("/api/taxa?limit=8&q=" + encodeURIComponent(q));
      if (!res.ok || mine !== seq) return;
      items = await res.json();
      list.replaceChildren(...items.map((t, i) => {
        const li = document.createElement("li");
        li.id = list.id + "-" + i;
        li.setAttribute("role", "option");
        const name = document.createElement("span");
        name.textContent = t.name;
        if (italic.has(t.rank)) name.className = "it";
        const rank = document.createElement("span");
        rank.className = "rank rank-" + (t.rank || "");
        rank.textContent = t.rank || "";
        const n = document.createElement("span");
        n.className = "n";
        n.textContent = t.signatures.toLocaleString() + " sig.";
        li.append(name, rank, n);
        li.addEventListener("mousedown", (e) => { e.preventDefault(); go(t); });
        return li;
      }));
      list.hidden = !items.length;
      input.setAttribute("aria-expanded", String(!!items.length));
      active = -1;
    }, 120);
  });
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); highlight(Math.min(active + 1, items.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); highlight(Math.max(active - 1, -1)); }
    else if (e.key === "Enter" && active >= 0) { e.preventDefault(); go(items[active]); }
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
