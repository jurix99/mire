// Mire — landing publique : liens de connexion, état connecté, prix et logos (API publique), animations.
// Page indépendante : n'importe pas app.js (qui redirige vers la connexion en cas de 401).

import { planFeatures } from "./plans.js";

document.documentElement.classList.add("js");

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const fmtInt = (n) => Number(n).toLocaleString("fr-FR");
/** Élément déjà visible à l'écran : affiché tout de suite, sans attendre l'IntersectionObserver. */
const inView = (el) => { const r = el.getBoundingClientRect(); return r.top < innerHeight && r.bottom > 0; };

// --- Connexion : retour vers la page demandée (?next=/postes), chemins internes seulement ---------------

const next = new URLSearchParams(location.search).get("next");
// Par défaut (ou si la page demandée est la landing elle-même) : le tableau de bord
const APP_HOME = "/tableau-de-bord";
const safeNext = next && next.startsWith("/") && !next.startsWith("//") && !["/", "/bienvenue"].includes(next)
  ? next : APP_HOME;
for (const a of $$("[data-login]")) a.href = `/auth/login?next=${encodeURIComponent(safeNext)}`;

// Déjà connecté : « Ouvrir Mire » à la place des boutons de connexion ; « Passer Pro » ouvre directement l'offre
fetch("/auth/me", { headers: { Accept: "application/json" } }).then((res) => {
  if (!res.ok) return;
  $$("[data-guest]").forEach((el) => (el.hidden = true));
  $$("[data-member]").forEach((el) => (el.hidden = false));
  $$("[data-login-pro]").forEach((el) => (el.href = `${APP_HOME}?pro=1`));
}).catch(() => {});

// --- En-tête : bordure au défilement, menu mobile --------------------------------------------------------

const head = $(".site-head");
const onScroll = () => head.classList.toggle("scrolled", scrollY > 8);
addEventListener("scroll", onScroll, { passive: true });
onScroll();

const menuBtn = $(".menu-btn");
const nav = $("#head-nav");
menuBtn.addEventListener("click", () => {
  const open = !nav.classList.contains("open");
  nav.classList.toggle("open", open);
  menuBtn.setAttribute("aria-expanded", String(open));
});
nav.addEventListener("click", (e) => {
  if (!e.target.closest("a")) return;
  nav.classList.remove("open");
  menuBtn.setAttribute("aria-expanded", "false");
});

// --- Données publiques : prix, limites, chiffres, logos ---------------------------------------------------

function hue(text) {
  let h = 0;
  for (const c of text) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}
const initials = (name) => name.split(/[\s-]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";

function countUp(el, target) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches || !target) {
    el.textContent = fmtInt(target);
    return;
  }
  const start = performance.now();
  const step = (t) => {
    const p = Math.min(1, (t - start) / 1200);
    el.textContent = fmtInt(Math.round(target * (1 - (1 - p) ** 3)));
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

const featureList = (items) => items.map(([on, text]) => `<li class="${on ? "" : "off"}">${esc(text)}</li>`).join("");

async function loadPublic() {
  let data;
  try {
    const res = await fetch("/api/public/landing", { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error();
    data = await res.json();
  } catch {
    $("#stats").hidden = true;
    return;
  }
  // Prix et limites : lus côté serveur (PLAN_LIMITS), jamais écrits en dur ici
  $$("[data-price]").forEach((el) => (el.textContent = data.prices[el.dataset.price] ?? "—"));
  const list = planFeatures(data.limits.free, data.limits.pro);
  $("#free-features").innerHTML = featureList(list.free);
  $("#pro-features").innerHTML = featureList(list.pro);

  // Chiffres du radar, animés à l'apparition
  const stats = $$("[data-stat]");
  const count = () => stats.forEach((el) => countUp(el, data.stats[el.dataset.stat] ?? 0));
  if (inView($("#stats"))) count();
  else {
    const io = new IntersectionObserver((entries, obs) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      count();
      obs.disconnect();
    });
    io.observe($("#stats"));
  }

  // Bande de logos (entreprises déjà visibles en Free), dupliquée pour un défilement continu
  if (data.companies.length >= 4) {
    const chip = (c) => `<span class="co" style="--h:${hue(c.name)}">
        <img src="${esc(c.logo)}" alt="" loading="lazy" data-ini="${esc(initials(c.name))}">${esc(c.name)}
        ${c.sector ? `<small>${esc(c.sector)}</small>` : ""}</span>`;
    const html = data.companies.map(chip).join("");
    $("#logo-track").innerHTML = html + html.replace(/<span class="co"/g, '<span class="co" aria-hidden="true"');
    $("#logo-strip").hidden = false;
  }
}

// Logo introuvable : initiales colorées
document.addEventListener("error", (e) => {
  const img = e.target;
  if (!(img instanceof HTMLImageElement) || !img.dataset.ini) return;
  const ini = document.createElement("span");
  ini.className = "ini";
  ini.textContent = img.dataset.ini;
  img.replaceWith(ini);
}, true);

loadPublic();

// --- Apparition au défilement ------------------------------------------------------------------------------

const reveal = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    e.target.classList.add("in");
    reveal.unobserve(e.target);
  }
}, { rootMargin: "0px 0px -8% 0px" });
$$(".reveal").forEach((el, i) => {
  el.style.transitionDelay = `${(i % 3) * 70}ms`;
  if (inView(el)) requestAnimationFrame(() => el.classList.add("in"));
  else reveal.observe(el);
});

// --- Pied de page : heure de Paris, année ----------------------------------------------------------------------

const clock = $("#clock");
const fmtTime = new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", hour: "2-digit", minute: "2-digit" });
const tick = () => (clock.textContent = `Paris · ${fmtTime.format(new Date())}`);
tick();
setInterval(tick, 30_000);
$("#year").textContent = new Date().getFullYear();

// --- Baromètre : agrégats publics (aucun nom d'entreprise), masqué s'il n'y a pas assez de données ----------

const pct = (x) => `${Math.round(x * 100)} %`;
const bars = (items) => items.map((i) => `<div class="baro-bar"><span>${esc(i.label)}</span>
  <span class="track"><span class="fill" style="width:${Math.max(4, i.share * 100)}%"></span></span>
  <span class="pct">${pct(i.share)}</span></div>`).join("");

async function loadBarometer() {
  let b;
  try {
    const res = await fetch("/api/public/barometer", { headers: { Accept: "application/json" } });
    if (!res.ok) return;
    b = await res.json();
  } catch {
    return;
  }
  if (!b.top_skills.items.length) return;
  $("#baro-scope").textContent = `${fmtInt(b.jobs)} offres de ${fmtInt(b.companies)} startups, ${b.period_days} derniers jours.`;
  $("#baro-skills-head").textContent = b.top_skills.headline;
  $("#baro-skills").innerHTML = bars(b.top_skills.items);
  $("#baro-rising-head").textContent = b.rising.headline;
  $("#baro-rising").innerHTML = b.rising.items.length
    ? b.rising.items.map((i) => `<li><span>${esc(i.label)}</span><span class="up">▲ +${i.pp} pts</span></li>`).join("")
    : `<li class="muted">Pas de hausse nette ce mois-ci</li>`;
  $("#baro-domains-head").textContent = b.domains.headline;
  $("#baro-domains").innerHTML = bars(b.domains.items);
  $("#barometre").hidden = false;
}

loadBarometer();
