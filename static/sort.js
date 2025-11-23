// static/sort.js
(function () {
  function parseDate(v) {
    // expecting "YYYY-MM-DD" or empty
    if (!v) return 0;
    const d = new Date(v);
    return d.getTime() || 0;
  }

  function cmp(a, b, type, dir) {
    let va = a.trim();
    let vb = b.trim();

    if (type === "number") {
      va = parseFloat(va) || 0;
      vb = parseFloat(vb) || 0;
    } else if (type === "date") {
      va = parseDate(va);
      vb = parseDate(vb);
    } else {
      va = va.toLowerCase();
      vb = vb.toLowerCase();
    }

    if (va < vb) return dir === "asc" ? -1 : 1;
    if (va > vb) return dir === "asc" ? 1 : -1;
    return 0;
  }

  function makeSortable(table) {
    const ths = table.querySelectorAll("thead th");
    ths.forEach((th, idx) => {
      th.addEventListener("click", () => {
        const type = th.getAttribute("data-sort") || "string";
        const currentDir = th.classList.contains("sort-asc") ? "asc"
                         : th.classList.contains("sort-desc") ? "desc"
                         : null;
        const newDir = currentDir === "asc" ? "desc" : "asc";

        // reset arrows
        ths.forEach(h => h.classList.remove("sort-asc", "sort-desc"));
        th.classList.add(newDir === "asc" ? "sort-asc" : "sort-desc");

        const tbody = table.querySelector("tbody");
        const rows = Array.from(tbody.querySelectorAll("tr"));

        rows.sort((r1, r2) => {
          const c1 = r1.children[idx]?.textContent || "";
          const c2 = r2.children[idx]?.textContent || "";
          return cmp(c1, c2, type, newDir);
        });

        rows.forEach(r => tbody.appendChild(r));
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("table.sortable").forEach(makeSortable);
  });
})();
