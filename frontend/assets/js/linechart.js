// Mire — courbes mois par mois (SVG, sans bibliothèque), partagées par la page Analytics et la démo de la landing.
//
// lineChart(el, { months, series, selected, unit, note })
//   months : [{ label, low_data? }]          axe des X (un point par mois)
//   series : [{ key, label, values: [0..1 | null] }]   parts (0,42 = 42 %), dans l'ordre de classement
//   selected : clés affichées au départ (4 au plus)
//
// Règles de lecture (consignes dataviz) : 4 séries au plus à la fois, chacune garde sa couleur tant qu'elle reste
// affichée (la couleur suit la série, pas son rang) ; le sélecteur sert de légende (trait de couleur + nom) ;
// valeur en bout de ligne ; réticule + infobulle qui listent toutes les séries du mois survolé (souris, doigt,
// flèches du clavier) ; tableau des chiffres réservé aux lecteurs d'écran (pas de doublon visible) ; un mois sur
// peu de données = point creux.

const SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]; // palette de référence, 4 premiers rangs
const MAX = SLOTS.length;
const H = 260;
const PAD = { top: 16, right: 64, bottom: 30, left: 40 };
const SVGNS = "http://www.w3.org/2000/svg";

const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)} %`);

function injectStyles() {
  if (document.getElementById("lc-styles")) return;
  const style = document.createElement("style");
  style.id = "lc-styles";
  style.textContent = `
.lc { --lc-ink: #0b1b33; --lc-ink-2: #3f5a80; --lc-grid: #e3ebf7; --lc-surface: #fff; position: relative; display: grid; gap: 12px; }
.lc-picker { display: flex; flex-wrap: wrap; gap: 6px; }
.lc-picker button { display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px; border-radius: 100px;
  border: 1px solid var(--lc-grid); background: var(--lc-surface); color: var(--lc-ink-2); font: inherit; font-size: 13px; cursor: pointer; }
.lc-picker button[aria-pressed="true"] { color: var(--lc-ink); border-color: var(--lc-ink); }
.lc-picker button:disabled { opacity: .45; cursor: not-allowed; }
.lc-key { width: 14px; height: 2px; border-radius: 2px; background: var(--lc-grid); }
.lc-svg { width: 100%; height: auto; display: block; overflow: visible; touch-action: pan-y; }
.lc-svg:focus-visible { outline: 2px solid #2a78d6; outline-offset: 4px; border-radius: 8px; }
.lc-svg text { font: 11px/1 system-ui, sans-serif; fill: var(--lc-ink-2); }
.lc-svg .lc-end { font-size: 12px; font-weight: 600; fill: var(--lc-ink); }
.lc-tip { position: absolute; pointer-events: none; min-width: 150px; padding: 10px 12px; border-radius: 12px;
  background: var(--lc-surface); border: 1px solid var(--lc-grid); box-shadow: 0 12px 30px -12px rgba(11,27,51,.35);
  font-size: 12px; color: var(--lc-ink-2); z-index: 5; }
.lc-tip p { margin: 0 0 6px; font-weight: 600; color: var(--lc-ink); }
.lc-tip div { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
.lc-tip b { color: var(--lc-ink); min-width: 38px; font-variant-numeric: tabular-nums; }
.lc-note { font-size: 12px; color: var(--lc-ink-2); margin: 0; }
.lc-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.lc-scroll { overflow-x: auto; }
`;
  document.head.append(style);
}

function el(tag, attrs = {}, parent) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.append(node);
  return node;
}

export function lineChart(root, { months, series, selected = [], unit = "des offres", note = "" }) {
  injectStyles();
  root.textContent = "";
  root.classList.add("lc");
  // Dessin à la largeur réelle (texte à sa vraie taille, pas agrandi avec le graphique)
  const W = Math.max(320, Math.round(root.getBoundingClientRect().width) || 640);
  if (!months.length || !series.length) {
    const p = document.createElement("p");
    p.className = "lc-note";
    p.textContent = "Pas encore assez de données pour tracer des courbes.";
    root.append(p);
    return;
  }

  // Couleur attribuée à la sélection et gardée tant que la série reste affichée
  const color = new Map();
  const free = () => SLOTS.find((c) => ![...color.values()].includes(c));
  for (const key of (selected.length ? selected : series.slice(0, 3).map((s) => s.key)).slice(0, MAX)) {
    if (series.some((s) => s.key === key)) color.set(key, free());
  }

  const picker = document.createElement("div");
  picker.className = "lc-picker";
  picker.setAttribute("role", "group");
  picker.setAttribute("aria-label", `Séries affichées (${MAX} au plus)`);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "lc-svg", tabindex: "0", role: "img" });
  const tip = document.createElement("div");
  tip.className = "lc-tip";
  tip.hidden = true;
  const caption = document.createElement("p");
  caption.className = "lc-note";
  caption.textContent = note;
  const table = document.createElement("div");
  table.className = "lc-sr";
  root.append(picker, svg, tip, ...(note ? [caption] : []), table);

  const x = (i) => PAD.left + (months.length === 1 ? (W - PAD.left - PAD.right) / 2
    : (i * (W - PAD.left - PAD.right)) / (months.length - 1));
  let y;
  let active = -1;

  function drawPicker() {
    picker.textContent = "";
    for (const s of series) {
      const on = color.has(s.key);
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("aria-pressed", String(on));
      b.disabled = !on && color.size >= MAX;
      b.title = b.disabled ? `${MAX} courbes au plus : retirez-en une` : "";
      const key = document.createElement("span");
      key.className = "lc-key";
      if (on) key.style.background = color.get(s.key);
      b.append(key, document.createTextNode(s.label));
      b.addEventListener("click", () => {
        if (on && color.size === 1) return; // toujours au moins une courbe
        if (on) color.delete(s.key);
        else color.set(s.key, free());
        render();
      });
      picker.append(b);
    }
  }

  function drawTable(shown) {
    table.textContent = "";
    const wrap = document.createElement("div");
    wrap.className = "lc-scroll";
    const t = document.createElement("table");
    const head = t.insertRow();
    for (const h of ["Mois", ...shown.map((s) => s.label)]) {
      const th = document.createElement("th");
      th.textContent = h;
      head.append(th);
    }
    months.forEach((m, i) => {
      const row = t.insertRow();
      row.insertCell().textContent = m.low_data ? `${m.label} (peu de données)` : m.label;
      for (const s of shown) row.insertCell().textContent = pct(s.values[i]);
    });
    wrap.append(t);
    table.append(wrap);
  }

  function render() {
    const shown = series.filter((s) => color.has(s.key));
    drawPicker();
    drawTable(shown);
    svg.textContent = "";
    svg.setAttribute("aria-label", `Évolution mensuelle : ${shown.map((s) => s.label).join(", ")}. Chiffres détaillés dans le tableau qui suit.`);
    const peak = Math.max(0.05, ...shown.flatMap((s) => s.values.filter((v) => v != null)));
    const step = peak > 0.5 ? 0.25 : peak > 0.2 ? 0.1 : 0.05;
    const top = Math.min(1, Math.ceil(peak / step) * step);
    y = (v) => PAD.top + (1 - v / top) * (H - PAD.top - PAD.bottom);

    // Grille horizontale discrète et axe des Y en %
    for (let v = 0; v <= top + 1e-9; v += step) {
      el("line", { x1: PAD.left, x2: W - PAD.right, y1: y(v), y2: y(v), stroke: "var(--lc-grid)", "stroke-width": 1 }, svg);
      el("text", { x: PAD.left - 8, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = `${Math.round(v * 100)} %`;
    }
    // Mois (un sur deux si la place manque)
    const every = months.length > 8 ? 2 : 1;
    months.forEach((m, i) => {
      if (i % every && i !== months.length - 1) return;
      el("text", { x: x(i), y: H - 8, "text-anchor": "middle" }, svg).textContent = m.label;
    });

    const cross = el("line", { y1: PAD.top, y2: H - PAD.bottom, stroke: "var(--lc-ink-2)", "stroke-width": 1, opacity: 0 }, svg);
    const ends = [];
    for (const s of shown) {
      const c = color.get(s.key);
      const pts = s.values.map((v, i) => (v == null ? null : [x(i), y(v)]));
      // Ligne coupée sur les mois sans valeur
      let d = "";
      pts.forEach((p, i) => { if (p) d += `${d && pts[i - 1] ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`; });
      el("path", { d, fill: "none", stroke: c, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
      pts.forEach((p, i) => {
        if (!p) return;
        const low = months[i].low_data;
        el("circle", { cx: p[0], cy: p[1], r: 4, fill: low ? "var(--lc-surface)" : c, stroke: c, "stroke-width": 2,
                       "data-i": i, class: "lc-pt" }, svg);
      });
      const last = s.values.map((v, i) => [v, i]).filter(([v]) => v != null).pop();
      if (last) ends.push({ y: y(last[0]), x: x(last[1]), text: pct(last[0]) });
    }
    // Valeur en bout de ligne ; si deux bouts se chevauchent, l'infobulle prend le relais
    ends.sort((a, b) => a.y - b.y).forEach((e, i, all) => {
      if (i && Math.abs(e.y - all[i - 1].y) < 13 && !all[i - 1].hidden) { e.hidden = true; return; }
      el("text", { x: e.x + 8, y: e.y + 4, class: "lc-end" }, svg).textContent = e.text;
    });
    // Zone de survol : tout le graphique (on vise un mois, pas une ligne de 2 px)
    const hit = el("rect", { x: PAD.left - 10, y: 0, width: W - PAD.left - PAD.right + 20, height: H, fill: "transparent" }, svg);

    const show = (i) => {
      active = i;
      cross.setAttribute("x1", x(i));
      cross.setAttribute("x2", x(i));
      cross.setAttribute("opacity", 0.35);
      tip.textContent = "";
      const title = document.createElement("p");
      title.textContent = months[i].low_data ? `${months[i].label} · peu de données` : months[i].label;
      tip.append(title);
      for (const s of [...shown].sort((a, b) => (b.values[i] ?? -1) - (a.values[i] ?? -1))) {
        const row = document.createElement("div");
        const key = document.createElement("span");
        key.className = "lc-key";
        key.style.background = color.get(s.key);
        const val = document.createElement("b");
        val.textContent = pct(s.values[i]);
        row.append(key, val, document.createTextNode(s.label));
        tip.append(row);
      }
      if (months[i].base != null) {
        const base = document.createElement("div");
        base.textContent = `sur ${months[i].base} ${unit}`;
        tip.append(base);
      }
      tip.hidden = false;
      const box = svg.getBoundingClientRect();
      const rootBox = root.getBoundingClientRect();
      const px = (x(i) / W) * box.width + box.left - rootBox.left;
      const left = px + 14 + tip.offsetWidth > rootBox.width ? px - 14 - tip.offsetWidth : px + 14;
      tip.style.left = `${Math.max(0, left)}px`;
      tip.style.top = `${box.top - rootBox.top + 8}px`;
    };
    const hide = () => { cross.setAttribute("opacity", 0); tip.hidden = true; active = -1; };
    const nearest = (evt) => {
      const box = svg.getBoundingClientRect();
      const vx = ((evt.clientX - box.left) / box.width) * W;
      let best = 0;
      months.forEach((_, i) => { if (Math.abs(x(i) - vx) < Math.abs(x(best) - vx)) best = i; });
      return best;
    };
    hit.addEventListener("pointermove", (e) => show(nearest(e)));
    hit.addEventListener("pointerdown", (e) => show(nearest(e)));
    hit.addEventListener("pointerleave", hide);
    svg.onkeydown = (e) => {
      if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
      e.preventDefault();
      const i = active < 0 ? months.length - 1 : active + (e.key === "ArrowRight" ? 1 : -1);
      show(Math.max(0, Math.min(months.length - 1, i)));
    };
    svg.onblur = hide;
  }

  render();

  // Largeur changée (rotation, fenêtre) : redessin à la nouvelle taille, mêmes séries affichées
  root._lcObserver?.disconnect();
  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver(() => {
      const width = Math.round(root.getBoundingClientRect().width);
      if (width && Math.abs(width - W) > 40) lineChart(root, { months, series, selected: [...color.keys()], unit, note });
    });
    observer.observe(root);
    root._lcObserver = observer;
  }
}
