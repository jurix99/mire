// Mire — graphiques en HTML (sans bibliothèque) : barres classées, avant → après, barres empilées à 100 %,
// fourchettes. Les courbes sont dans linechart.js. Partagés par la page Analytics et la démo de la landing.
//
// Règles (consignes dataviz) : le graphique se suffit à lui-même — une seule valeur, au bout de la marque ;
// le détail (effectifs, base) est dans l'infobulle (survol, doigt, Tab) ; une teinte pour un classement,
// des teintes catégorielles dans un ordre fixe pour des parts, deux nuances d'une teinte pour avant / après.
// Toutes les données passent par textContent (jamais d'HTML venant des données).

export const COLORS = {
  blue: "#2a78d6", orange: "#eb6834", aqua: "#1baf7a", yellow: "#eda100",
  // Rampe ordinale du bleu (étapes 250 -> 650) : niveaux ordonnés (séniorité)
  ramp: ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"],
  before: "#86b6ef",
};
export const SERIES = [COLORS.blue, COLORS.orange, COLORS.aqua, COLORS.yellow];

function injectStyles() {
  if (document.getElementById("ch-styles")) return;
  const style = document.createElement("style");
  style.id = "ch-styles";
  style.textContent = `
.ch { --ch-ink: var(--text, #0b1b33); --ch-ink-2: var(--muted, #3f5a80); --ch-grid: #e3ebf7; --ch-track: #eef3fb;
  position: relative; display: grid; gap: 6px; font-size: 13px; color: var(--ch-ink); }
.ch-row { display: grid; grid-template-columns: minmax(90px, 34%) 1fr; align-items: center; gap: 12px; min-height: 26px;
  border-radius: 6px; outline: none; }
.ch-row:hover .ch-mark, .ch-row:focus-visible .ch-mark { filter: brightness(1.08); }
.ch-row:focus-visible { box-shadow: 0 0 0 2px #2a78d6; }
.ch-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ch-ink); }
.ch-plot { position: relative; display: flex; align-items: center; gap: 8px; height: 22px; }
.ch-bar { height: 14px; border-radius: 0 4px 4px 0; background: ${COLORS.blue}; min-width: 2px; }
.ch-val { font-variant-numeric: tabular-nums; font-weight: 600; white-space: nowrap; }
.ch-group { margin: 10px 0 2px; font-size: 11px; letter-spacing: .6px; text-transform: uppercase; color: var(--ch-ink-2); }
.ch-group:first-child { margin-top: 0; }
.ch-stack { display: flex; gap: 2px; height: 22px; width: 100%; }
.ch-seg { height: 100%; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 11px;
  font-weight: 600; font-variant-numeric: tabular-nums; overflow: hidden; white-space: nowrap; }
.ch-seg:first-child { border-radius: 4px 0 0 4px; } .ch-seg:last-child { border-radius: 0 4px 4px 0; }
.ch-seg.dark-text { color: var(--ch-ink); }
.ch-legend { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12px; color: var(--ch-ink-2); margin-bottom: 6px; }
.ch-legend span { display: inline-flex; align-items: center; gap: 6px; }
.ch-legend i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.ch-legend i.dot { border-radius: 50%; }
.ch-dumb { position: absolute; inset: 0 48px 0 0; }
.ch-dumb .line { position: absolute; top: 50%; height: 2px; transform: translateY(-50%); background: var(--ch-grid); }
.ch-dumb .dot { position: absolute; top: 50%; width: 10px; height: 10px; border-radius: 50%; transform: translate(-50%, -50%);
  box-shadow: 0 0 0 2px #fff; }
.ch-dumb-val { position: absolute; right: 0; top: 50%; transform: translateY(-50%); }
.ch-range { position: absolute; inset: 0 56px 0 0; }
.ch-range .iqr { position: absolute; top: 50%; height: 12px; transform: translateY(-50%); border-radius: 4px; background: ${COLORS.before}; }
.ch-range .med { position: absolute; top: 50%; width: 3px; height: 20px; transform: translate(-50%, -50%); border-radius: 2px;
  background: #1c5cab; box-shadow: 0 0 0 2px #fff; }
.ch-range-val { position: absolute; right: 0; top: 50%; transform: translateY(-50%); }
.ch-axis { display: grid; grid-template-columns: minmax(90px, 34%) 1fr; gap: 12px; font-size: 11px; color: var(--ch-ink-2); }
.ch-axis div { position: relative; height: 14px; margin-right: 56px; }
.ch-axis span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
.ch-tip { position: absolute; z-index: 5; pointer-events: none; max-width: 260px; padding: 8px 10px; border-radius: 10px;
  background: #fff; border: 1px solid var(--ch-grid); box-shadow: 0 12px 30px -12px rgba(11,27,51,.35);
  font-size: 12px; color: var(--ch-ink-2); line-height: 1.4; }
.ch-tip b { display: block; color: var(--ch-ink); margin-bottom: 2px; }
.ch-empty { color: var(--ch-ink-2); font-size: 13px; margin: 0; }
@media (max-width: 640px) { .ch-row, .ch-axis { grid-template-columns: minmax(80px, 40%) 1fr; } }
`;
  document.head.append(style);
}

function h(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

/** Prépare le conteneur : styles, classe, infobulle partagée par toutes les lignes (survol, doigt, Tab). */
function setup(root) {
  injectStyles();
  root.textContent = "";
  root.classList.add("ch");
  const tip = h("div", "ch-tip");
  tip.hidden = true;
  const show = (row) => {
    if (!row?.dataset.tipTitle) return;
    tip.textContent = "";
    tip.append(h("b", null, row.dataset.tipTitle), document.createTextNode(row.dataset.tip || ""));
    tip.hidden = false;
    const box = root.getBoundingClientRect();
    const r = row.getBoundingClientRect();
    const top = r.bottom - box.top + 6;
    const left = Math.min(Math.max(0, r.left - box.left + r.width * 0.35), box.width - tip.offsetWidth);
    tip.style.top = `${top}px`;
    tip.style.left = `${Math.max(0, left)}px`;
  };
  root.onpointerover = (e) => show(e.target.closest?.(".ch-row"));
  root.onpointerleave = () => (tip.hidden = true);
  root.onfocusin = (e) => show(e.target.closest?.(".ch-row"));
  root.onfocusout = () => (tip.hidden = true);
  root.append(tip);
  return (node) => root.insertBefore(node, tip); // le contenu passe avant l'infobulle
}

function row(label, title, tip) {
  const r = h("div", "ch-row");
  r.tabIndex = 0;
  r.dataset.tipTitle = title ?? label;
  r.dataset.tip = tip ?? "";
  r.setAttribute("aria-label", `${title ?? label}. ${tip ?? ""}`);
  r.append(h("span", "ch-label", label));
  return r;
}

const empty = (root, text = "Pas encore assez de données.") => { setup(root); root.append(h("p", "ch-empty", text)); };

/**
 * Barres classées (une teinte) : items [{ label, value, display, tip, group? }], value >= 0.
 * group : intertitre quand il change (ex. métiers regroupés par domaine). max : borne de l'échelle (sinon le max).
 */
export function barChart(root, { items, max, color = COLORS.blue, emptyText }) {
  if (!items?.length) return empty(root, emptyText);
  const add = setup(root);
  const top = max ?? Math.max(...items.map((i) => i.value), 1e-9);
  let group;
  for (const it of items) {
    if (it.group && it.group !== group) {
      group = it.group;
      add(h("p", "ch-group", group));
    }
    const r = row(it.label, it.tipTitle, it.tip);
    const plot = h("div", "ch-plot");
    const bar = h("span", "ch-bar ch-mark");
    bar.style.width = `calc((100% - 56px) * ${Math.max(0, it.value / top)})`; // 56 px gardés pour la valeur
    bar.style.background = color;
    plot.append(bar, h("span", "ch-val", it.display));
    r.append(plot);
    add(r);
  }
}

/**
 * Avant -> après (dumbbell) : items [{ label, before, after, display, tip }] en parts (0..1).
 * Point clair = période précédente, point foncé = période récente, reliés ; valeur récente au bout.
 */
export function dumbbell(root, { items, legend = ["Avant", "Maintenant"], emptyText }) {
  if (!items?.length) return empty(root, emptyText);
  const add = setup(root);
  const lg = h("div", "ch-legend");
  for (const [text, c] of [[legend[0], COLORS.before], [legend[1], COLORS.blue]]) {
    const s = h("span");
    const dot = h("i", "dot");
    dot.style.background = c;
    s.append(dot, document.createTextNode(text));
    lg.append(s);
  }
  add(lg);
  const top = Math.max(...items.flatMap((i) => [i.before, i.after]), 1e-9) * 1.05;
  for (const it of items) {
    const r = row(it.label, it.tipTitle, it.tip);
    const plot = h("div", "ch-plot");
    const zone = h("div", "ch-dumb");
    const a = (it.before / top) * 100;
    const b = (it.after / top) * 100;
    const line = h("span", "line ch-mark");
    line.style.left = `${Math.min(a, b)}%`;
    line.style.width = `${Math.abs(b - a)}%`;
    line.style.background = b >= a ? COLORS.blue : COLORS.before;
    const d1 = h("span", "dot");
    d1.style.left = `${a}%`;
    d1.style.background = COLORS.before;
    const d2 = h("span", "dot ch-mark");
    d2.style.left = `${b}%`;
    d2.style.background = COLORS.blue;
    zone.append(line, d1, d2);
    plot.append(zone, h("span", "ch-val ch-dumb-val", it.display));
    r.append(plot);
    add(r);
  }
}

/**
 * Barres empilées à 100 % : rows [{ label, parts: [{ key, value (part 0..1), tip }] }], legend [{ key, label, color }].
 * Une seule ligne sans libellé = barre segmentée (part d'un tout). Pourcentage écrit dans le segment s'il a la place.
 */
export function stackedBars(root, { rows, legend, emptyText }) {
  if (!rows?.length) return empty(root, emptyText);
  const add = setup(root);
  const colorOf = Object.fromEntries(legend.map((l) => [l.key, l.color]));
  const labelOf = Object.fromEntries(legend.map((l) => [l.key, l.label]));
  const lg = h("div", "ch-legend");
  for (const l of legend) {
    const s = h("span");
    const sw = h("i");
    sw.style.background = l.color;
    s.append(sw, document.createTextNode(l.label));
    lg.append(s);
  }
  add(lg);
  for (const rw of rows) {
    const tip = rw.parts.map((p) => `${labelOf[p.key]} ${Math.round(p.value * 100)} %${p.tip ? ` (${p.tip})` : ""}`).join(" · ");
    const r = row(rw.label ?? "", rw.label ?? "Répartition", tip);
    if (!rw.label) r.style.gridTemplateColumns = "1fr";
    if (!rw.label) r.firstChild.remove();
    const stack = h("div", "ch-stack ch-mark");
    for (const p of rw.parts) {
      if (p.value <= 0) continue;
      const seg = h("span", "ch-seg", p.value >= 0.12 ? `${Math.round(p.value * 100)} %` : "");
      seg.style.flex = `${p.value} 1 0`;
      seg.style.background = colorOf[p.key];
      if ([COLORS.yellow, COLORS.ramp[0]].includes(colorOf[p.key])) seg.classList.add("dark-text");
      stack.append(seg);
    }
    r.append(stack);
    add(r);
  }
}

/**
 * Fourchettes : rows [{ label, q1, med, q3, display, tip }] (même unité). Barre = écart interquartile (q1-q3),
 * trait foncé = médiane, valeur de la médiane au bout. Axe commun en bas.
 */
export function rangeChart(root, { rows, unit = "", emptyText }) {
  if (!rows?.length) return empty(root, emptyText);
  const add = setup(root);
  const lo = Math.min(...rows.map((r) => r.q1));
  const hi = Math.max(...rows.map((r) => r.q3));
  // 5 graduations au plus (sinon elles se chevauchent sur un écran étroit)
  const step = [10, 20, 25, 50, 100].find((s) => (Math.ceil(hi / s) - Math.floor(lo / s)) <= 4) ?? 100;
  const min = Math.floor(lo / step) * step;
  const max = Math.max(Math.ceil(hi / step) * step, min + step);
  const pos = (v) => ((v - min) / (max - min)) * 100;
  for (const it of rows) {
    const r = row(it.label, it.tipTitle, it.tip);
    const plot = h("div", "ch-plot");
    const zone = h("div", "ch-range");
    const iqr = h("span", "iqr ch-mark");
    iqr.style.left = `${pos(it.q1)}%`;
    iqr.style.width = `${Math.max(0.8, pos(it.q3) - pos(it.q1))}%`;
    const med = h("span", "med");
    med.style.left = `${pos(it.med)}%`;
    zone.append(iqr, med);
    plot.append(zone, h("span", "ch-val ch-range-val", it.display));
    r.append(plot);
    add(r);
  }
  const axis = h("div", "ch-axis");
  axis.append(h("span"));
  const ticks = h("div");
  // Trois repères (début, milieu, fin) : lisibles même dans une demi-carte
  for (const v of [min, Math.round((min + max) / 2), max]) {
    const t = h("span", null, `${v}${unit}`);
    t.style.left = `${pos(v)}%`;
    ticks.append(t);
  }
  axis.append(ticks);
  add(axis);
}
