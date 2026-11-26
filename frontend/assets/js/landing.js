// Mire — landing publique : liens de connexion, état connecté, prix et logos (API publique), animations.
// Page indépendante : n'importe pas app.js (qui redirige vers la connexion en cas de 401).

import { initDemo } from "./landing-demo.js";
import { planFeatures } from "./plans.js";

document.documentElement.classList.add("js");

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const fmtInt = (n) => Number(n).toLocaleString("fr-FR");
/** Lien externe affichable : http(s) seulement. */
const safeUrl = (u) => typeof u === "string" && /^https?:\/\//i.test(u);
/** Élément déjà visible à l'écran : affiché tout de suite, sans attendre l'IntersectionObserver. */
const inView = (el) => { const r = el.getBoundingClientRect(); return r.top < innerHeight && r.bottom > 0; };

// --- Connexion : retour vers la page demandée (?next=/postes), chemins internes seulement ---------------

const next = new URLSearchParams(location.search).get("next");
// Par défaut (ou si la page demandée est la landing elle-même) : le tableau de bord
const APP_HOME = "/tableau-de-bord";
const safeNext = next && next.startsWith("/") && !next.startsWith("//") && !["/", "/bienvenue"].includes(next)
  ? next : APP_HOME;
// Bêta privée : lien d'invitation /?invitation=<code>, transmis à la connexion (vérifié par le serveur)
const params = new URLSearchParams(location.search);
const invitation = params.get("invitation");
const inviteParam = invitation ? `&invitation=${encodeURIComponent(invitation)}` : "";
for (const a of $$("[data-login]")) a.href = `/auth/login?next=${encodeURIComponent(safeNext)}${inviteParam}`;

// Connexion refusée par la bêta privée (nouveau compte sans invitation)
if (params.get("acces") === "beta") {
  const note = document.createElement("div");
  note.className = "beta-note";
  note.setAttribute("role", "status");
  note.innerHTML = `<strong>Mire est en bêta privée.</strong> L'inscription se fait pour l'instant sur invitation :
    utilisez le lien qui vous a été envoyé, ou revenez bientôt.`;
  $("main")?.prepend(note);
}

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
    // Chaque logo mène au site de l'entreprise (nouvel onglet) ; sans site connu, simple étiquette
    const chip = (c, copy) => {
      const inner = `<img src="${esc(c.logo)}" alt="" loading="lazy" data-ini="${esc(initials(c.name))}">${esc(c.name)}
        ${c.sector ? `<small>${esc(c.sector)}</small>` : ""}`;
      const hidden = copy ? ' aria-hidden="true" tabindex="-1"' : "";
      return safeUrl(c.website)
        ? `<a class="co" style="--h:${hue(c.name)}" href="${esc(c.website)}" target="_blank" rel="noopener"
             title="Site de ${esc(c.name)}"${hidden}>${inner}</a>`
        : `<span class="co" style="--h:${hue(c.name)}"${copy ? ' aria-hidden="true"' : ""}>${inner}</span>`;
    };
    $("#logo-track").innerHTML = data.companies.map((c) => chip(c, false)).join("")
      + data.companies.map((c) => chip(c, true)).join("");
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

// --- Démo cliquable de l'app (données fictives) ------------------------------------------------------------

initDemo($("#demo-app"));

// --- Essai gratuit : lien LinkedIn -> 3 startups, sans compte (POST /api/public/trial) -------------------------

const LINKEDIN_RE = /^(?:https?:\/\/)?(?:[a-z]{2,3}\.)?linkedin\.com\/in\/[A-Za-z0-9\-_%]{3,100}\/?(?:[?#].*)?$/i;
const MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];
const fmtMonth = (iso) => { const [y, m] = iso.split("-"); return `${MONTHS[Number(m) - 1]} ${y}`; };
const fmtMeur = (n) => `${Number(n).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} M€`;
const smooth = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");

const form = $("#trial-form");
const urlInput = $("#trial-url");
const field = urlInput.closest(".field");
const errorEl = $("#trial-error");
const submitBtn = $("#trial-submit");
const results = $("#resultats");
const loading = $("#results-loading");
const list = $("#results-list");

function showError(text) {
  errorEl.textContent = text;
  errorEl.hidden = !text;
}

function funding(f) {
  const parts = [f.stage, f.amount_meur ? fmtMeur(f.amount_meur) : null].filter(Boolean).join(" · ");
  return [parts && `<span class="fund">${esc(parts)}</span>`,
          f.announced_on && `<span class="tagc">levée en ${esc(fmtMonth(f.announced_on))}</span>`].filter(Boolean).join("");
}

function pickCard(c, i) {
  const site = safeUrl(c.website);
  const name = site ? `<a href="${esc(c.website)}" target="_blank" rel="noopener">${esc(c.name)} ↗</a>` : esc(c.name);
  const tags = [c.sector, c.city, c.headcount && `${c.headcount} pers.`].filter(Boolean)
    .map((t) => `<span class="tagc">${esc(t)}</span>`).join("");
  const others = c.open_jobs > 1 ? ` et ${c.open_jobs - 1} autre${c.open_jobs > 2 ? "s" : ""}` : "";
  return `<article class="pick" style="animation-delay:${i * 90}ms">
      <div class="logo" style="--h:${hue(c.name)}"><img src="${esc(c.logo)}" alt="" data-ini="${esc(initials(c.name))}"></div>
      <div>
        <p class="rank">0${i + 1}</p>
        <h3>${name}</h3>
        <div class="meta">${funding(c.funding)}${tags}</div>
        <p class="reason">${esc(c.reason)}</p>
        ${c.job_title ? `<p class="job">Offre ouverte : <b>${esc(c.job_title)}</b>${others}</p>` : ""}
      </div>
      ${site ? `<a class="pill ghost visit" href="${esc(c.website)}" target="_blank" rel="noopener">Voir le site</a>` : ""}
    </article>`;
}

let stepTimer;
function animateLoading() {
  const steps = $$("[data-step]", loading);
  let k = 0;
  steps.forEach((s) => (s.className = ""));
  steps[0].className = "on";
  clearInterval(stepTimer);
  stepTimer = setInterval(() => {
    if (k >= steps.length - 1) return clearInterval(stepTimer);
    steps[k].className = "done";
    steps[++k].className = "on";
  }, 4000);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!LINKEDIN_RE.test(url)) {
    field.classList.add("invalid");
    showError("Collez l'adresse de votre profil LinkedIn, de la forme linkedin.com/in/votre-nom.");
    urlInput.focus();
    return;
  }
  field.classList.toggle("invalid", false);
  showError("");
  submitBtn.disabled = true;
  submitBtn.textContent = "Recherche en cours…";
  results.hidden = false;
  loading.hidden = false;
  list.innerHTML = "";
  $("#results-cta").hidden = true;
  $("#results-title").textContent = "On cherche pour vous…";
  $("#results-sub").textContent = "";
  animateLoading();
  results.scrollIntoView({ behavior: smooth() });

  try {
    const res = await fetch("/api/public/trial", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ linkedin_url: url, wish: $("#trial-wish").value.trim() || null }),
    });
    const data = await res.json().catch(() => ({}));
    if (data.detail?.code === "need_wish") {
      // Profil public introuvable : pas de sélection au hasard, on demande une ligne
      const wish = $(".wish");
      wish.open = true;
      throw Object.assign(new Error(data.detail.message), { focus: $("#trial-wish") });
    }
    if (!res.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "L'essai n'a pas pu aboutir, réessayez dans un instant.");
    }
    if (!data.companies?.length) {
      $(".wish").open = true;
      throw Object.assign(new Error("Aucune offre du radar n'est assez proche de votre profil pour l'instant. "
        + "Précisez ce que vous cherchez (métier, niveau, ville) et relancez."), { focus: $("#trial-wish") });
    }
    const n = data.companies.length;
    $("#results-title").innerHTML = `${n} startup${n > 1 ? "s" : ""} financée${n > 1 ? "s" : ""}<br>`
      + `<span class="soft">qui recrute${n > 1 ? "nt" : ""} dans votre métier.</span>`;
    $("#results-sub").textContent = data.profile_found
      ? `Votre profil, tel que Mire l'a compris : ${data.profile_summary ?? ""}`
      : "Votre profil public n'a pas été trouvé : la sélection repose sur ce que vous avez écrit. "
        + "Précisez votre recherche pour l'affiner.";
    list.innerHTML = data.companies.map(pickCard).join("");
    $("#results-cta").hidden = false;
  } catch (err) {
    results.hidden = true;
    showError(err.message || "L'essai n'a pas pu aboutir, réessayez dans un instant.");
    form.scrollIntoView({ behavior: smooth(), block: "center" });
    err.focus?.focus({ preventScroll: true });
  } finally {
    clearInterval(stepTimer);
    loading.hidden = true;
    submitBtn.disabled = false;
    submitBtn.textContent = "Voir mes 3 startups";
  }
});
urlInput.addEventListener("input", () => field.classList.toggle("invalid", false));
