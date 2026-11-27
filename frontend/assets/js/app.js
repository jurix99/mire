// Mire — code partagé par toutes les pages (API, barre latérale, pastilles, logos, formulaires)

import { planFeatures } from "./plans.js";

// --- Statuts (clés identiques à l'enum process_status du backend) -----------

export const STATUSES = [
  ["nouveau", "Nouveau"],
  ["a_postuler", "À regarder"],
  ["prete", "Prête à envoyer"],
  ["postule", "Postulé"],
  ["relance", "Relance"],
  ["entretien", "Entretien"],
  ["offre", "Offre"],
  ["refuse", "Refusé"],
  ["pas_interesse", "Pas intéressé"],
  ["abandonne", "Abandonné"],
];
export const STATUS_LABEL = Object.fromEntries(STATUSES);
/** Statuts proposés dans les listes : « Prête à envoyer » se pose depuis la page Candidature (bouton dédié). */
export const manualStatuses = (current) => STATUSES.filter(([k]) => k !== "prete" || k === current);

// --- HTML sûr ---------------------------------------------------------------

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
export const raw = (s) => new Raw(s);

/** Gabarit HTML : les valeurs interpolées sont échappées, sauf celles produites par html`` ou raw(). */
export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((v, i) => {
    out += (Array.isArray(v) ? v.map(toHtml).join("") : toHtml(v)) + strings[i + 1];
  });
  return raw(out);
}
const toHtml = (v) => (v instanceof Raw ? v.s : v === false || v == null ? "" : esc(v));

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// --- API --------------------------------------------------------------------

export async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: { Accept: "application/json" } };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`/api${path}`, opts);
  if (res.status === 401) {
    location.href = `/auth/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    throw new Error("Session expirée");
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (res.status === 402 && data?.detail?.code) {
    // Offre Free : quota atteint ou fonction réservée au Pro (message et bouton Pro déjà affichés)
    planError(data.detail);
    const err = new Error(data.detail.message ?? "Réservé à l'offre Pro");
    err.code = data.detail.code;
    err.handled = true;
    throw err;
  }
  if (!res.ok) {
    const d = data?.detail;
    const msg = Array.isArray(d) ? d.map((e) => `${e.loc?.at(-1) ?? ""} : ${e.msg}`).join(" · ") : d?.message ?? d;
    throw new Error(msg || `Erreur ${res.status}`);
  }
  return data;
}

// --- Formats ----------------------------------------------------------------

const fmtShort = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short" });
const fmtLong = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const parseDay = (iso) => (iso && iso.length === 10 ? new Date(`${iso}T00:00:00`) : new Date(iso));

export const fmtDate = (iso) => (iso ? fmtShort.format(parseDay(iso)) : "");
export const fmtToday = () => fmtLong.format(new Date());
/** Date relative : « aujourd'hui », « hier », « il y a 3 jours ». */
export function fmtAgo(iso) {
  if (!iso) return "";
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = parseDay(iso); day.setHours(0, 0, 0, 0);
  const n = Math.round((today - day) / 86400000);
  return n <= 0 ? "aujourd'hui" : n === 1 ? "hier" : `il y a ${n} jours`;
}
export const fmtMoney = (m) => (m == null ? "" : `${Number(m).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} M€`);
export function daysUntil(iso) {
  if (!iso) return null;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((parseDay(iso) - today) / 86400000);
}

// --- Composants -------------------------------------------------------------

// Statuts affichés mais jamais posés par l'utilisateur (verdict de l'évaluation, offre fermée)
export const AUTO_LABEL = { ecartee: "Écartée (auto)", fermee: "Offre fermée" };
export const DISPLAY_LABEL = { ...STATUS_LABEL, ...AUTO_LABEL };

export const pill = (status) => html`<span class="pill st-${status}">${DISPLAY_LABEL[status] ?? status}</span>`;

/** Sélecteur de statut. `current` peut être un statut automatique (ex. « ecartee ») : il est affiché
 * mais pas sélectionnable ; choisir un statut le remplace (l'utilisateur a toujours le dernier mot). */
export function statusSelect(current, attrs = "") {
  return html`<select class="status-select st-${current}" aria-label="Statut" ${raw(attrs)}>
    ${AUTO_LABEL[current] ? html`<option value="${current}" selected disabled>${AUTO_LABEL[current]}</option>` : ""}
    ${manualStatuses(current).map(([k, l]) => html`<option value="${k}" ${raw(k === current ? "selected" : "")}>${l}</option>`)}
  </select>`;
}

// --- Candidature préparée (page Candidature) -----------------------------------------------------
// Mire prépare le formulaire ; l'utilisateur copie-colle et envoie lui-même sur le site de l'entreprise.

export const candidatureUrl = (jobId) => `/candidature?job=${encodeURIComponent(jobId)}`;
const DOC_ICON = raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/></svg>`);

/**
 * Bouton « Préparer ma candidature » (ou « Voir ma candidature » une fois préparée).
 * job : { id, score, draft_status } ; icon : version icône (lignes de la page Postes).
 */
export function applicationButton(job, { icon = false, cls = "btn ghost sm" } = {}) {
  const ready = !!job.draft_status;
  const label = ready ? "Voir ma candidature" : "Préparer ma candidature";
  const score = isLocked(job.score) ? "" : job.score ?? "";
  if (ready) {
    return icon
      ? html`<a class="icon-btn cand-ready" href="${candidatureUrl(job.id)}" title="${label}" aria-label="${label}">${DOC_ICON}</a>`
      : html`<a class="${cls}" href="${candidatureUrl(job.id)}">${DOC_ICON} ${label}</a>`;
  }
  return icon
    ? html`<button type="button" class="icon-btn" data-prepare="${job.id}" data-score="${score}" title="${label}" aria-label="${label}">${DOC_ICON}</button>`
    : html`<button type="button" class="${cls}" data-prepare="${job.id}" data-score="${score}">${DOC_ICON} ${label}</button>`;
}

/** Lance la préparation : Free -> fenêtre Pro (aperçu flouté) ; note sous 5 -> avertissement ; puis page Candidature. */
export async function prepareApplication(jobId, { score = null } = {}) {
  let billing = null;
  try { billing = (await loadMe()).billing; } catch { /* le serveur tranchera (402) */ }
  if (billing && billing.limits?.prepare_application_per_day === 0) return openProModal({ preview: "candidature" });
  if (score != null && score !== "" && Number(score) < 5
    && !window.confirm(`Cette offre est notée ${Number(score).toLocaleString("fr-FR")}/10 pour vous. Préparer quand même la candidature ?`)) return null;
  try {
    await api(`/jobs/${jobId}/application/prepare`, { method: "POST" });
  } catch (err) {
    if (!err.handled) toast(err.message, true);
    return null;
  }
  location.href = candidatureUrl(jobId);
  return true;
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest?.("[data-prepare]");
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  btn.disabled = true;
  prepareApplication(btn.dataset.prepare, { score: btn.dataset.score }).finally(() => (btn.disabled = false));
});

function hue(text) {
  let h = 0;
  for (const c of text) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}
const initials = (name) =>
  name.split(/[\s-]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";

// --- Offre Pro : valeurs verrouillées -----------------------------------------
// Pour un utilisateur Free, l'API remplace les données réservées au Pro par {locked: true, placeholder} :
// placeholder est un texte FACTICE (jamais la vraie valeur), affiché flouté avec un badge « Pro ».

const LOCK_ICON = raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>`);

export const isLocked = (v) => v != null && typeof v === "object" && v.locked === true;
/** Valeur utilisable en texte (recherche, tri, titres de formulaire) : "" si verrouillée. */
export const plain = (v) => (isLocked(v) ? "" : v ?? "");
export const proTag = html`<span class="pro-tag">${LOCK_ICON} Pro</span>`;

/**
 * Affiche une valeur de l'API, ou son texte factice flouté si elle est verrouillée (clic : fenêtre Pro).
 * fmt(v) : mise en forme de la vraie valeur ; block : bloc de texte (description, « pourquoi »…).
 */
export function renderValue(v, { fmt, block = false } = {}) {
  if (isLocked(v)) {
    return html`<span class="locked ${block ? "block" : ""}" data-pro role="button" tabindex="0" title="Réservé à l'offre Pro"
      aria-label="Réservé à l'offre Pro"><span class="locked-text" aria-hidden="true">${v.placeholder}</span><span class="pro-badge">${LOCK_ICON} Pro</span></span>`;
  }
  if (v == null || v === "") return "";
  return fmt ? fmt(v) : v;
}

/** Texte factice d'une valeur verrouillée, ou la valeur elle-même (dans un bloc déjà flouté en entier). */
export const shown = (v) => (isLocked(v) ? v.placeholder : v ?? "");

/**
 * Bloc entièrement flouté d'un seul tenant (une ligne d'offre, une carte, la partie « levée » d'une carte),
 * avec un seul badge « Pro » au centre ; clic : fenêtre Pro. content : HTML déjà rendu (texte factice).
 */
export const lockedBlock = (content, { cls = "" } = {}) => html`
  <div class="locked-block ${cls}" data-pro role="button" tabindex="0" title="Réservé à l'offre Pro" aria-label="Réservé à l'offre Pro">
    <div class="locked-block-content" aria-hidden="true">${content}</div>
    <span class="pro-badge">${LOCK_ICON} Pro</span>
  </div>`;

/**
 * Liste affichée par paquets : les éléments suivants s'ajoutent quand on approche du bas de la page.
 * keep : garder au moins le nombre d'éléments déjà affichés (rafraîchissement sans perdre sa position).
 */
export function renderPaged(container, items, render, { page = 40, empty = "", keep = false } = {}) {
  const previous = container._paged;
  previous?.observer?.disconnect();
  const state = { shown: 0, observer: null };
  container._paged = state;
  container.innerHTML = items.length ? "" : String(empty);
  if (!items.length) return;
  const sentinel = document.createElement("div");
  sentinel.className = "list-sentinel";
  sentinel.setAttribute("aria-hidden", "true");
  container.append(sentinel);
  const more = (count) => {
    const next = items.slice(state.shown, state.shown + count);
    sentinel.insertAdjacentHTML("beforebegin", html`${next.map(render)}`.s);
    state.shown += next.length;
    if (state.shown >= items.length) {
      state.observer?.disconnect();
      sentinel.remove();
    }
  };
  more(keep && previous ? Math.max(page, previous.shown) : page);
  if (state.shown < items.length) {
    state.observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) more(page);
    }, { rootMargin: "800px 0px" });
    state.observer.observe(sentinel);
  }
}

/** Bandeau d'appel au Pro (chiffres calculés par l'API). */
export const proBar = (text) => html`<div class="pro-bar" role="note"><span>${LOCK_ICON} ${text}</span>
  <button type="button" class="btn sm" data-pro>Passer Pro</button></div>`;

/** Logo d'entreprise : image récupérée par le backend, avec initiales colorées en repli. */
export function logo(company, size = "") {
  const name = plain(company.name);
  if (!company.logo || !name) return html`<span class="logo ${size} is-locked" data-initials="${name ? initials(name) : "?"}"></span>`;
  const bust = company._v ? `?v=${company._v}` : "";
  return html`<span class="logo ${size}" style="--hue:${hue(name)}" data-initials="${initials(name)}">
    <img src="${company.logo}${bust}" alt="" loading="lazy">
  </span>`;
}
// Image introuvable (404) : on retire l'<img> pour laisser apparaître les initiales
document.addEventListener("error", (e) => {
  if (e.target instanceof HTMLImageElement && e.target.parentElement?.classList.contains("logo")) e.target.remove();
}, true);

export const score = (s) => (s == null ? "" : isLocked(s) ? renderValue(s)
  : html`<span class="score ${s < 7.5 ? "low" : ""}">${Number(s).toFixed(1)}</span>`);

export const ICONS = {
  edit: raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`),
  trash: raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>`),
  link: raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>`),
  image: raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/></svg>`),
  arrow: raw(`<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>`),
  plus: raw(`<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>`),
  heart: raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.8 0-3 .5-4.5 2-1.5-1.5-2.7-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7Z"/></svg>`),
  cross: raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>`),
};

// --- Notifications ----------------------------------------------------------

let toastTimer;
/** action : { label, onClick } (ou une liste) pour des boutons dans la notification (ex. « Annuler »). */
export function toast(message, error = false, action = null) {
  let el = $(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("role", "status");
    document.body.append(el);
  }
  el.textContent = message;
  const actions = action ? [action].flat() : [];
  for (const a of actions) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "toast-action";
    btn.textContent = a.label;
    btn.addEventListener("click", () => { el.hidden = true; a.onClick(); });
    el.append(btn);
  }
  // Une fiche ouverte (<dialog> modal) passe au-dessus de tout : la notification s'affiche dedans
  const host = document.querySelector("dialog[open]") ?? document.body;
  if (el.parentElement !== host) host.append(el);
  // La fiche se ferme : la notification (et ses boutons) reste visible
  if (host !== document.body) host.addEventListener("close", () => document.body.append(el), { once: true });
  el.classList.toggle("error", error);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), action ? 8000 : error ? 5000 : 2500);
}

/** Exécute une action et affiche l'erreur éventuelle. */
export async function attempt(fn, okMessage) {
  try {
    const r = await fn();
    if (okMessage) toast(okMessage);
    return r;
  } catch (err) {
    if (!err.handled) toast(err.message, true);
    throw err;
  }
}

// --- Offre Pro : fenêtre d'abonnement, erreurs 402 ---------------------------------

/** Réponse 402 de l'API : quota Free atteint (message + bouton Pro), ou fonction réservée au Pro. */
function planError(detail) {
  if (detail.code === "quota") toast(detail.message, true, { label: "Passer Pro", onClick: openProModal });
  else openProModal();
}

/** Aperçu flouté d'une candidature exemple (fenêtre Pro ouverte depuis « Préparer ma candidature »). Texte factice. */
const CANDIDATURE_PREVIEW = html`
  <div class="pro-preview" aria-hidden="true">
    <div class="locked-block-content">
      <p class="label">// Candidature · Backend Engineer</p>
      <div class="cand-sample"><span>Prénom</span><span>Camille</span></div>
      <div class="cand-sample"><span>Pourquoi nous ?</span><span>Votre produit de planification répond à un problème que j'ai traité pendant trois ans…</span></div>
      <div class="cand-sample"><span>CV adapté</span><span>CV_Camille_Durand_Acme.pdf · 2 expériences mises en avant</span></div>
      <div class="cand-sample"><span>Lettre</span><span>Acme vient de lever pour industrialiser…</span></div>
    </div>
    <p class="small"><strong>Préparer ma candidature</strong> : formulaire de l'offre pré-rempli, CV adapté et lettre, à relire
      et copier-coller. Réservé au Pro.</p>
  </div>`;

/** Fenêtre « Passer Pro » : ce qui est débloqué, les deux prix, la renonciation au droit de rétractation.
 * preview : « candidature » ajoute l'aperçu flouté d'une candidature préparée. */
export async function openProModal({ preview = null } = {}) {
  if ($("dialog.pro-dialog")) return;
  let billing = null;
  try { billing = (await loadMe()).billing; } catch { /* affichée sans les chiffres */ }
  const free = billing?.limits ?? {};
  const pro = billing?.pro_limits ?? {};
  const prices = billing?.prices ?? {};
  const isPro = billing && billing.plan === "pro" && !billing.trial; // en essai : on peut déjà s'abonner
  const list = planFeatures(free, pro);
  const feats = (items) => html`<ul class="plan-feats">${items.map(([on, text]) => html`<li class="${on ? "" : "off"}">${text}</li>`)}</ul>`;
  // Le Pass 3 mois est mis en avant (le temps d'une recherche, sans renouvellement) ; « 25 € TTC pour 3 mois » ->
  // montant en grand, le reste en petit (comme sur la landing)
  const [, amount = prices.pass_3m ?? "Pass 3 mois", unit = ""] = (prices.pass_3m ?? "").match(/^(.*?€)\s*(.*)$/) ?? [];
  const dlg = document.createElement("dialog");
  dlg.className = "pro-dialog";
  dlg.setAttribute("aria-label", "Passer à l'offre Pro");
  dlg.innerHTML = html`
    <div class="dialog-body">
      <div class="pro-head">
        <div>
          <p class="label">// Mire Pro</p>
          <h2>Le radar complet.</h2>
          <p class="muted">Toutes les offres, les levées et le travail de l'IA, sans attendre.</p>
        </div>
        <button type="button" class="icon-btn" data-close aria-label="Fermer">×</button>
      </div>
      ${preview === "candidature" ? CANDIDATURE_PREVIEW : ""}
      <div class="pro-plans">
        <article class="plan">
          <p class="plan-name">Free ${isPro ? "" : html`<span class="plan-current">Votre offre</span>`}</p>
          <p class="plan-price">0 €</p>
          <p class="plan-sub">Pour découvrir le radar.</p>
          ${feats(list.free)}
        </article>
        <article class="plan pro">
          <p class="plan-name">Pro <span class="plan-tag">Radar complet</span></p>
          <p class="plan-price">${amount} <small>${unit}</small></p>
          <p class="plan-sub"><strong>Pass 3 mois</strong>${prices.pass_per_month ? `, soit ${prices.pass_per_month}` : ""} :
            le temps d'une recherche, en un paiement, sans renouvellement${prices.pass_saving ? html`
            <span class="plan-saving">${prices.pass_saving}</span>` : "."}</p>
          ${feats(list.pro)}
          ${isPro ? html`<p class="plan-sub"><strong>Vous avez déjà l'offre Pro.</strong></p>` : html`
          <p class="pro-waiver">En cliquant sur « Prendre le Pass 3 mois » ou « S'abonner au mois », vous demandez l'accès
            immédiat à Mire Pro et renoncez à votre droit de rétractation de 14 jours
            (<a href="/cgv#retractation" target="_blank" rel="noopener">CGV, art. 6</a>).</p>
          <div class="plan-ctas">
            <button type="button" class="plan-cta" data-checkout="pass_3m">Prendre le Pass 3 mois</button>
            <button type="button" class="plan-cta ghost" data-checkout="monthly">S'abonner au mois${prices.monthly
              ? ` · ${prices.monthly}` : ""}</button>
          </div>
          <p class="form-error" hidden></p>`}
        </article>
      </div>
      <p class="small muted pro-note">Prix TTC · Paiement sécurisé par Stripe · Résiliable en ligne ·
        <a href="/cgv" target="_blank" rel="noopener">CGV</a> · <a href="/confidentialite" target="_blank" rel="noopener">Confidentialité</a></p>
    </div>`.s;
  document.body.append(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  dlg.addEventListener("click", async (e) => {
    if (e.target === dlg || e.target.closest("[data-close]")) return dlg.close();
    const btn = e.target.closest("[data-checkout]");
    if (!btn) return;
    $$("[data-checkout]", dlg).forEach((b) => (b.disabled = true));
    try {
      // Le clic sur le bouton, sous la mention affichée juste au-dessus, vaut demande d'accès immédiat et
      // renonciation au droit de rétractation (CGV art. 6) ; le serveur l'horodate (withdrawal_waived_at)
      const res = await api("/billing/checkout", { method: "POST", body: { plan: btn.dataset.checkout, waive_withdrawal: true } });
      location.href = res.url;
    } catch (err) {
      const box = $(".form-error", dlg);
      box.textContent = err.message;
      box.hidden = false;
      $$("[data-checkout]", dlg).forEach((b) => (b.disabled = false));
    }
  });
  dlg.showModal();
}

// Clic (ou Entrée) sur un élément verrouillé ou un bouton « Passer Pro » : fenêtre Pro, rien d'autre
// (écouteur en phase de capture : passe avant les clics de ligne qui ouvriraient la fiche)
document.addEventListener("click", (e) => {
  if (!e.target.closest?.("[data-pro]")) return;
  e.preventDefault();
  e.stopPropagation();
  openProModal();
}, true);
document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.closest?.(".locked[data-pro], .locked-block[data-pro]")) {
    e.preventDefault();
    openProModal();
  }
});

// --- Formulaire générique dans un <dialog> -----------------------------------

/**
 * Ouvre un formulaire et résout avec les valeurs saisies (null si annulé).
 * fields : [{ name, label, type: text|url|number|date|textarea|select|checks|file, options: [[v, l]], required, full, hint, step, min, max, maxlength }]
 *   (checks : cases à cocher, la valeur renvoyée est un tableau)
 * onSubmit(values) : appelé avant fermeture ; une erreur levée s'affiche dans le formulaire.
 * extra : contenu HTML ajouté sous les champs ; danger : { label, onClick } pour un bouton de suppression.
 */
export function openForm({ title, fields, values = {}, submitLabel = "Enregistrer", onSubmit, extra = "", danger }) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.innerHTML = html`
      <form method="dialog" novalidate>
        <div class="dialog-body">
          <h2>${title}</h2>
          <div class="fields">${fields.map((f) => fieldHtml(f, values[f.name]))}</div>
          ${raw(String(extra))}
          <p class="form-error" hidden></p>
        </div>
        <div class="dialog-foot">
          ${danger ? html`<button type="button" class="btn danger" data-danger>${danger.label}</button>` : ""}
          <div class="right">
            <button type="button" class="btn ghost" data-cancel>Annuler</button>
            <button type="submit" class="btn">${submitLabel}</button>
          </div>
        </div>
      </form>`.s;
    document.body.append(dlg);
    const form = $("form", dlg);
    const errorEl = $(".form-error", dlg);
    let result = null;

    const close = () => dlg.close();
    dlg.addEventListener("close", () => { dlg.remove(); resolve(result); });
    $("[data-cancel]", dlg).addEventListener("click", close);
    $("[data-danger]", dlg)?.addEventListener("click", async () => {
      try {
        if (await danger.onClick()) { result = { deleted: true }; close(); }
      } catch (err) { errorEl.textContent = err.message; errorEl.hidden = false; }
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!form.reportValidity()) return;
      const out = {};
      for (const f of fields) {
        const input = form.elements[f.name];
        if (f.type === "checks") out[f.name] = $$(`input[name="${f.name}"]:checked`, form).map((i) => i.value);
        else if (f.type === "file") out[f.name] = input.files[0] ?? null;
        else if (f.type === "number") out[f.name] = input.value === "" ? null : Number(input.value);
        else out[f.name] = input.value.trim() === "" ? null : input.value.trim();
      }
      const submit = $("button[type=submit]", dlg);
      submit.disabled = true;
      try {
        result = onSubmit ? (await onSubmit(out)) ?? out : out;
        close();
      } catch (err) {
        errorEl.textContent = err.message;
        errorEl.hidden = false;
      } finally {
        submit.disabled = false;
      }
    });

    dlg.showModal();
    $("input:not([type=hidden]), select, textarea", dlg)?.focus();
  });
}

function fieldHtml(f, value) {
  const id = `f-${f.name}`;
  if (f.type === "checks") {
    const selected = new Set(value ?? []);
    return html`<fieldset class="field checks ${f.full ? "full" : ""}"><legend>${f.label}</legend>
      <div class="check-row">${f.options.map(([v, l]) => html`<label class="check">
        <input type="checkbox" name="${f.name}" value="${v}" ${raw(selected.has(v) ? "checked" : "")}> ${l}</label>`)}</div>
      ${f.hint ? html`<span class="hint">${f.hint}</span>` : ""}
    </fieldset>`;
  }
  const extra = f.maxlength ? ` maxlength="${Number(f.maxlength)}"` : "";
  const common = raw(`id="${id}" name="${esc(f.name)}" ${f.required ? "required" : ""}${extra}`);
  let input;
  if (f.type === "textarea") input = html`<textarea class="input" ${common}>${value ?? ""}</textarea>`;
  else if (f.type === "select")
    input = html`<select ${common}>${f.options.map(
      ([v, l]) => html`<option value="${v}" ${raw(String(v) === String(value ?? "") ? "selected" : "")}>${l}</option>`
    )}</select>`;
  else if (f.type === "file") input = html`<input class="input" type="file" accept="${f.accept ?? ""}" ${common}>`;
  else
    input = html`<input class="input" type="${f.type ?? "text"}" value="${value ?? ""}" ${common}
      ${raw(f.step ? `step="${f.step}"` : "")} ${raw(f.min != null ? `min="${f.min}"` : "")} ${raw(f.max != null ? `max="${f.max}"` : "")}
      ${raw(f.placeholder ? `placeholder="${esc(f.placeholder)}"` : "")}>`;
  return html`<label class="field ${f.full ? "full" : ""}" for="${id}">
    <span>${f.label}${f.required ? " *" : ""}</span>${input}
    ${f.hint ? html`<span class="hint">${f.hint}</span>` : ""}
  </label>`;
}

export const confirmDelete = (what) => window.confirm(`Supprimer ${what} ? Cette action est définitive.`);

// --- Retours (J'aime / Pas intéressé) : un clic, annulable ------------------------

const WORK_MODE_LABEL = { sur_site: "Sur site", hybride: "Hybride", remote: "Remote" };
const httpUrl = (u) => (u && /^https?:\/\//i.test(u) ? u : null);

/** Pertinence pour l'utilisateur (0-100), calculée par le serveur (ranking.py). */
export const matchBadge = (m) => (m
  ? html`<span class="match ${m.score >= 70 ? "high" : m.score >= 50 ? "mid" : "low"}"
      title="Pertinence pour vous, sur 100" aria-label="Pertinence ${m.score} sur 100">${m.score}</span>`
  : "");

/** Ce qui joue pour et contre. */
export const reasonList = (m) => (m && (m.reasons.length || m.warnings.length)
  ? html`<ul class="reasons">${m.reasons.map((r) => html`<li class="plus">${r}</li>`)}${m.warnings.map(
      (r) => html`<li class="minus">${r}</li>`)}</ul>`
  : "");

export function salaryText(job) {
  if (job.salary) return renderValue(job.salary);
  const { salary_min_keur: min, salary_max_keur: max } = job;
  if (!min && !max) return "";
  return min && max && min !== max ? `${min}–${max} k€` : `${max ?? min} k€`;
}

/**
 * Offre vue ouverte sur le site de l'entreprise (liste de son ATS) dans les 7 derniers jours : la preuve qu'elle
 * n'est pas une offre fantôme. Plus ancien, ou offre fermée : rien.
 */
export function verifiedTag(job) {
  if (!job.verified_open_at || job.closed_at) return "";
  const hours = Math.max(0, Math.floor((Date.now() - new Date(job.verified_open_at)) / 3600000));
  if (hours > 24 * 7) return "";
  const when = hours < 1 ? "il y a moins d'une heure" : hours < 48 ? `il y a ${hours} h` : `il y a ${Math.floor(hours / 24)} jours`;
  return html`<span class="verified" title="Offre vue ouverte sur le site de l'entreprise ${when}">✓ vérifiée ${when}</span>`;
}

/** Lieu, mode, salaire, ancienneté et vérification d'une offre, sur une ligne. */
export function jobFacts(job) {
  const parts = [job.city ?? job.location, WORK_MODE_LABEL[job.work_mode], salaryText(job),
    fmtAgo(job.published_on ?? job.created_at), verifiedTag(job)].filter(Boolean);
  return parts.map((p, i) => html`${i ? " · " : ""}${p}`);
}

/** Boutons J'aime / Pas intéressé (offre ou entreprise) ; state : "like" | "dislike" | null. */
export const reactionButtons = (state, { labels = false } = {}) => html`
  <button type="button" class="${labels ? "btn ghost" : "icon-btn"} fb-btn fb-like ${state === "like" ? "is-on" : ""}"
    data-react="like" aria-pressed="${state === "like"}"
    title="${state === "like" ? "Retirer de mes J'aime" : "J'aime"}" aria-label="J'aime">${ICONS.heart}${labels ? " J'aime" : ""}</button>
  <button type="button" class="${labels ? "btn ghost" : "icon-btn"} fb-btn fb-dislike ${state === "dislike" ? "is-on" : ""}"
    data-react="dislike" aria-pressed="${state === "dislike"}"
    title="${state === "dislike" ? "Remettre dans les propositions" : "Pas intéressé"}"
    aria-label="Pas intéressé">${ICONS.cross}${labels ? " Pas intéressé" : ""}</button>`;

/**
 * Enregistre ou retire un retour sur une offre. value : "like" | "dislike" | null.
 * Retourne l'offre mise à jour (null si l'action n'a pas abouti).
 */
export async function reactToJob(job, value) {
  if (job.locked) { openProModal(); return null; }
  let updated;
  try {
    updated = value
      ? (await api(`/jobs/${job.id}/feedback`, { method: "POST", body: { sentiment: value, reasons: [], text: null } })).job
      : await api(`/jobs/${job.id}/feedback`, { method: "DELETE" });
  } catch (err) {
    if (!err.handled) toast(err.message, true);
    return null;
  }
  const undo = { label: "Annuler", onClick: () => reactToJob(updated, null).then(refreshPage) };
  if (value === "like") toast("Ajoutée à vos J'aime", false, [undo]);
  else if (value === "dislike") {
    toast("Offre écartée", false, [undo, { label: "Dire pourquoi", onClick: () => askReasons(updated) }]);
  } else toast("Retour retiré");
  return updated;
}

/** « Dire pourquoi » après un « Pas intéressé » : affine le classement et la synthèse des règles. */
export async function askReasons(job) {
  const reasons = await api("/feedback/reasons");
  const values = await openForm({
    title: `Pas intéressé : ${plain(job.title)}`,
    fields: [
      { name: "reasons", label: "Pourquoi ?", type: "checks", full: true, options: reasons.map((r) => [r.key, r.label]) },
      { name: "text", label: "Précisez (facultatif)", type: "textarea", full: true, maxlength: 1000 },
    ],
    submitLabel: "Enregistrer",
  });
  if (!values) return;
  const res = await attempt(() => api(`/jobs/${job.id}/feedback`, {
    method: "POST", body: { sentiment: "dislike", reasons: values.reasons ?? [], text: values.text },
  }), "Noté, le classement en tient compte").catch(() => null);
  if (!res) return;
  // « Déjà passé en entretien » : proposer d'exclure l'entreprise (validation explicite)
  const it = res.proposal?.items?.[0];
  if (it && window.confirm(`Exclure ${it.value.name} des prochaines offres ? (vous êtes déjà passé en entretien)`)) {
    await attempt(() => api(`/profile/proposals/${res.proposal.import_id}/apply`, { method: "POST", body: { accept: [it.id] } }),
      `${it.value.name} ajoutée aux entreprises exclues`);
  }
  refreshPage();
}

/** J'aime / Pas intéressé sur une entreprise (value null : retiré). Retourne l'entreprise mise à jour. */
export async function reactToCompany(company, value) {
  let updated;
  try {
    updated = await api(`/companies/${company.id}/reaction`, { method: "PUT", body: { value } });
  } catch (err) {
    if (!err.handled) toast(err.message, true);
    return null;
  }
  const undo = { label: "Annuler", onClick: () => reactToCompany(updated, null).then(refreshPage) };
  if (value === "like") toast(`${plain(updated.name)} : ses offres remontent`, false, [undo]);
  else if (value === "dislike") toast(`${plain(updated.name)} écartée avec ses offres`, false, [undo]);
  else toast("Retour retiré");
  return updated;
}

// --- Fiche d'une offre ---------------------------------------------------------

/**
 * Fiche d'une offre dans un <dialog> : pourquoi elle est proposée, analyse, texte de l'annonce, et les actions
 * (J'aime, Pas intéressé, préparer la candidature, marquer « postulé »).
 * onEdit(job) : facultatif, ajoute un bouton « Modifier » (administrateurs).
 */
export async function openJobDialog(jobId, { onEdit } = {}) {
  const dlg = document.createElement("dialog");
  dlg.className = "job-dialog";
  dlg.setAttribute("aria-label", "Détail de l'offre");
  dlg.innerHTML = `<div class="dialog-body"><p class="muted small">Chargement…</p></div>`;
  document.body.append(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); }); // clic sur le fond
  dlg.showModal();

  let job;
  try {
    job = await api(`/jobs/${jobId}`);
  } catch (err) {
    dlg.close();
    toast(err.message, true);
    return;
  }
  const draw = () => {
    const url = httpUrl(job.url);
    const app = job.application;
    const followed = app && !["nouveau", "pas_interesse"].includes(app.status);
    const sent = followed && !["a_postuler", "prete"].includes(app.status);
    dlg.innerHTML = html`
      <div class="dialog-body">
        <div class="job-head">
          ${logo(job.company, "lg")}
          <div class="grow">
            <h2>${renderValue(job.title)}</h2>
            <p class="muted small">${renderValue(job.company.name)} · ${jobFacts(job)}</p>
          </div>
          ${matchBadge(job.match)}
          <button type="button" class="icon-btn close" data-close aria-label="Fermer">×</button>
        </div>
        ${job.locked ? proBar("Cette offre est réservée au Pro.") : ""}
        ${job.closed_at ? html`<p class="notice warn small">Offre fermée le ${fmtDate(job.closed_at)}.</p>`
          : job.hidden_reason ? html`<p class="notice warn small">Pas dans vos propositions : ${job.hidden_reason}</p>` : ""}
        ${followed ? html`<p class="notice small">Dans vos candidatures : ${pill(app.status)}
            ${app.applied_on ? ` envoyée le ${fmtDate(app.applied_on)}` : ""}
            · <a href="/mes-process#process-${app.id}">Suivre</a></p>` : ""}
        ${reasonList(job.match)}
        ${job.locked ? "" : analysisSection(job.assessment)}
        <section class="job-section">
          <p class="label">// L'annonce</p>
          ${isLocked(job.summary) ? renderValue(job.summary, { block: true })
            : job.summary ? html`<div class="job-text prewrap">${job.summary}</div>`
            : html`<p class="muted small">Pas de description enregistrée.${url ? " Consultez l'annonce originale." : ""}</p>`}
        </section>
      </div>
      <div class="dialog-foot">
        <div class="feedback-buttons">${job.locked ? "" : reactionButtons(job.feedback, { labels: true })}</div>
        <div class="right">
          ${onEdit && !job.locked ? html`<button type="button" class="btn ghost" data-edit data-admin>Modifier</button>` : ""}
          ${url ? html`<a class="btn ghost" href="${url}" target="_blank" rel="noopener noreferrer">Annonce ${ICONS.link}</a>`
            : isLocked(job.url) ? html`<button type="button" class="btn ghost" data-pro>Annonce ${proTag}</button>` : ""}
          ${job.locked || sent ? "" : html`<button type="button" class="btn ghost" data-applied>J'ai postulé</button>`}
          ${job.locked ? "" : applicationButton(job, { cls: "btn" })}
        </div>
      </div>`.s;
  };

  const reload = async () => {
    job = await api(`/jobs/${job.id}`);
    draw();
  };

  draw();
  dlg.addEventListener("click", async (e) => {
    if (e.target.closest("[data-close]")) return dlg.close();
    const reaction = e.target.closest("[data-react]");
    if (reaction) {
      const value = job.feedback === reaction.dataset.react ? null : reaction.dataset.react;
      if (!(await reactToJob(job, value))) return;
      if (value === "dislike") dlg.close();
      else await reload().catch(() => {});
      return refreshPage();
    }
    if (e.target.closest("[data-applied]")) {
      const ok = await attempt(() => api(`/jobs/${job.id}/status`, { method: "PUT", body: { status: "postule" } }),
        "Ajoutée à vos candidatures envoyées").catch(() => null);
      if (!ok) return;
      await reload().catch(() => {});
      return refreshPage();
    }
    if (e.target.closest("[data-edit]")) {
      dlg.close();
      onEdit(job);
    }
  });
  $("[data-close]", dlg).focus();
}

/** Analyse de Gemini quand l'offre a été évaluée : note, pourquoi, compétences. */
function analysisSection(a) {
  if (!a || (a.score == null && !a.why)) return "";
  const tags = (label, list, cls) => (isLocked(list)
    ? html`<div class="tags"><span class="small muted">${label}</span>${renderValue(list)}</div>`
    : list.length ? html`<div class="tags"><span class="small muted">${label}</span>${list.map((s) => html`<span class="tag ${cls}">${s}</span>`)}</div>` : "");
  const note = a.score == null ? "" : isLocked(a.score) ? renderValue(a.score)
    : html`${Number(a.score).toLocaleString("fr-FR")}/10`;
  return html`<section class="job-section">
    <p class="label">// Analyse de l'IA ${note}</p>
    ${a.why ? html`<p class="small">${renderValue(a.why, { block: true })}</p>` : ""}
    ${tags("Vous avez :", a.skills_have, "have")}
    ${tags("À acquérir :", a.skills_missing, "missing")}
    ${a.stale ? html`<p class="small muted">Analyse faite avant la dernière modification de votre profil : elle sera refaite.</p>` : ""}
  </section>`;
}

// --- Barre latérale ---------------------------------------------------------

const NAV = [
  ["/postes", "Offres", null],
  ["/entreprises", "Entreprises", null],
  ["/mes-process", "Candidatures", "processes"],
  ["/mon-profil", "Profil", null],
  ["/sources", "Sources", null, true], // réservée aux admins (gestion du radar partagé, santé du moteur)
];

let mePromise;
/** Profil + compteurs de navigation (une seule requête par page). */
export const loadMe = () => (mePromise ??= api("/me"));

export async function refreshCounts() {
  mePromise = undefined;
  const me = await loadMe();
  for (const [key, n] of Object.entries(me.counts)) {
    const el = $(`[data-count="${key}"]`);
    if (el) el.textContent = n;
  }
  const plan = $("#plan-box");
  if (plan) plan.hidden = !(me.billing?.enabled && me.billing.plan === "free");
  // Radar partagé : seuls les admins voient les boutons qui le modifient (l'API refuse aussi : 403)
  const notAdmin = me.user.is_admin === false;
  document.body.classList.toggle("not-admin", notAdmin);
}

/** Logo Mire (anneau ouvert + point central + satellite), coloré via currentColor. */
const LOGO_SVG = `<svg class="brand-logo" viewBox="0 0 1024 1024" aria-hidden="true"><defs><mask id="mire-gap"><rect width="1024" height="1024" fill="#fff"/><circle cx="734" cy="290" r="108" fill="#000"/></mask></defs><circle cx="512" cy="512" r="314" fill="none" stroke="currentColor" stroke-width="92" mask="url(#mire-gap)"/><circle cx="512" cy="512" r="84" fill="currentColor"/><circle cx="734" cy="290" r="75" fill="currentColor"/></svg>`;

/** Chemin normalisé d'une page : "/entreprises.html" -> "/entreprises", "/index" -> "/". */
const pagePath = (p) => p.replace(/\.html$/, "").replace(/\/index$/, "/") || "/";

export async function renderShell() {
  const path = pagePath(location.pathname);
  const aside = $("#sidebar");
  // Construite une seule fois : elle reste en place pendant la navigation sans rechargement
  if (aside.dataset.ready) {
    for (const a of aside.querySelectorAll(".nav a")) {
      if (pagePath(new URL(a.href).pathname) === path) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
  } else {
    aside.dataset.ready = "1";
    aside.innerHTML = html`
    <a class="brand" href="/postes" aria-label="Mire, offres pour vous">${raw(LOGO_SVG)}<span>Mire</span></a>
    <nav class="nav" aria-label="Navigation principale">
      ${NAV.map(([href, label, count, adminOnly]) => html`
        <a href="${href}" ${raw(path === href ? 'aria-current="page"' : "")} ${raw(adminOnly ? "data-admin" : "")}>
          <span>${label}</span>${count ? html`<span class="count" data-count="${count}"></span>` : ""}
        </a>`)}
    </nav>
    <div class="watch" id="plan-box" hidden>
      <p class="label">// Offre Free</p>
      <p class="small muted">L'offre la plus pertinente, toutes les autres, les levées de fonds et les notes de l'IA : réservées au Pro.</p>
      <button type="button" class="btn sm" data-pro>Passer Pro</button>
    </div>
    <p class="sidebar-legal small muted">
      <a href="/mentions-legales">Mentions légales</a> · <a href="/cgv">CGV</a> · <a href="/confidentialite">Confidentialité</a>
    </p>`.s;
    mountChat();
  }
  try {
    await refreshCounts();
  } catch (err) {
    toast(err.message, true);
  }
}

let pageRender = null;

/** Recharge les données de la page ouverte (ex. après une action confirmée dans le chat). */
export async function refreshPage() {
  const render = pageRender;
  await Promise.allSettled([refreshCounts(), render?.()]);
}

/** Point d'entrée de chaque page. */
export async function boot(render) {
  const main = $("main");
  pageRender = render;
  renderShell();
  const done = (async () => {
    try {
      await render();
    } catch (err) {
      console.error(err);
      toast(err.message, true);
    }
  })();
  // La page apparaît (fondu léger) une fois ses données affichées, ou au plus tard après 400 ms
  await Promise.race([done, new Promise((r) => setTimeout(r, 400))]);
  main.classList.add("ready");
  // Arrivée depuis « Passer Pro » de la landing (/?pro=1) : fenêtre Pro, puis on nettoie l'URL
  const url = new URL(location.href);
  if (url.searchParams.get("pro") === "1") {
    url.searchParams.delete("pro");
    history.replaceState(null, "", url);
    openProModal();
  }
  // Lien du récapitulatif par e-mail : /postes?offre=<id>[&retour=like|dislike] (rien n'est enregistré sans session)
  const offer = Number(url.searchParams.get("offre"));
  if (offer > 0) {
    const reaction = url.searchParams.get("retour");
    url.searchParams.delete("offre");
    url.searchParams.delete("retour");
    history.replaceState(null, "", url);
    await done;
    openFromEmail(offer, ["like", "dislike"].includes(reaction) ? reaction : null);
  }
  await done;
}

async function openFromEmail(jobId, reaction) {
  if (reaction) {
    const job = await api(`/jobs/${jobId}`).catch(() => null);
    if (!job) { toast("Cette offre n'existe plus", true); return; }
    if (job.feedback !== reaction && await reactToJob(job, reaction)) await refreshPage();
    if (reaction === "dislike") return; // le toast propose « Dire pourquoi » et « Annuler »
  }
  openJobDialog(jobId);
}

// --- Assistant (chat flottant, Gemini) ----------------------------------------
// Bouton en bas à droite sur toutes les pages, panneau de conversation. Les écritures proposées par
// l'assistant s'affichent en cartes « Confirmer / Annuler » ; rien n'est modifié sans confirmation.

const CHAT_ICON = raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z"/><path d="M8.5 11h.01M12 11h.01M15.5 11h.01"/></svg>`);
const CLOSE_ICON = raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>`);
const RESET_ICON = raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>`);
const SEND_ICON = raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>`);

const ACTION_STATUS = {
  pending: "En attente de confirmation",
  confirmed: "Confirmé",
  cancelled: "Annulé",
  failed: "Échec",
  expired: "Expiré",
};
const SUGGESTIONS = [
  "Quels postes devrais-je regarder en priorité ?",
  "Ajoute Kubernetes à mes compétences à développer",
  "Où en sont mes process ?",
];

/**
 * Markdown léger et sûr : le texte est d'abord entièrement échappé, puis seules quelques balises
 * connues sont ajoutées (gras, italique, code, liens http(s) ou internes, listes, paragraphes).
 */
export function renderMarkdown(text) {
  const inline = (s) => s
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(/\[([^\]\n]+)\]\(((?:https?:\/\/|\/(?!\/))[^\s)]+)\)/g,
      (_, label, url) => `<a href="${url}"${url.startsWith("/") ? "" : ' target="_blank" rel="noopener noreferrer"'}>${label}</a>`);
  const blocks = [];
  let list = null;
  for (const line of esc(text).split("\n")) {
    const item = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
    if (item) {
      const tag = /^\s*\d/.test(line) ? "ol" : "ul";
      if (!list || list.tag !== tag) blocks.push((list = { tag, items: [] }));
      list.items.push(inline(item[1]));
      continue;
    }
    list = null;
    if (!line.trim()) blocks.push(null);
    else if (typeof blocks.at(-1) === "string") blocks[blocks.length - 1] += `<br>${inline(line)}`;
    else blocks.push(inline(line));
  }
  return blocks.filter(Boolean).map((b) =>
    typeof b === "string" ? `<p>${b}</p>` : `<${b.tag}>${b.items.map((i) => `<li>${i}</li>`).join("")}</${b.tag}>`
  ).join("");
}

const chat = { status: null, loaded: false, busy: false };

function actionCard(a) {
  const change = (c) => html`<li><span class="muted">${c.label} :</span>
    ${c.before != null && c.before !== "" ? html`<s>${String(c.before)}</s> → ` : ""}${c.after != null ? String(c.after) : html`<em>retiré</em>`}</li>`;
  return html`<div class="chat-action is-${a.status}" data-action="${a.id}">
    <div class="chat-action-head"><strong>${a.summary}</strong><span class="chat-action-status">${ACTION_STATUS[a.status] ?? a.status}</span></div>
    <ul>${a.changes.map(change)}</ul>
    ${a.error ? html`<p class="small chat-error">${a.error}</p>` : ""}
    ${a.status === "pending" ? html`<div class="chat-action-buttons">
      <button type="button" class="btn sm" data-confirm="${a.id}">Confirmer</button>
      <button type="button" class="btn ghost sm" data-cancel="${a.id}">Annuler</button>
    </div>` : ""}
  </div>`;
}

function messageHtml(m) {
  if (m.role === "user") return html`<div class="chat-msg user"><div class="bubble">${m.content}</div></div>`;
  return html`<div class="chat-msg assistant">
    <div class="bubble md">${raw(renderMarkdown(m.content))}</div>
    ${m.actions?.length ? html`<div class="chat-actions">${m.actions.map(actionCard)}</div>` : ""}
    ${m.quick_replies?.length ? html`<div class="chat-suggestions quick" aria-label="Réponses rapides">
      ${m.quick_replies.map((r) => html`<button type="button" class="chip" data-suggest="${r}">${r}</button>`)}
    </div>` : ""}
  </div>`;
}

function chatLog() { return $("#chat-panel .chat-log"); }

function scrollChat() {
  const log = chatLog();
  log.scrollTop = log.scrollHeight;
}

function appendMessage(m) {
  $(".chat-empty", chatLog())?.remove();
  $$(".chat-suggestions.quick", chatLog()).forEach((el) => el.remove()); // seules les dernières restent utiles
  chatLog().insertAdjacentHTML("beforeend", messageHtml(m).s);
  scrollChat();
}

function renderEmptyChat() {
  chatLog().innerHTML = html`<div class="chat-empty">
    <p>Bonjour ! Je peux consulter et mettre à jour votre profil, vos postes et vos process.
    Je vous demande toujours confirmation avant de modifier quoi que ce soit.</p>
    <div class="chat-suggestions">${SUGGESTIONS.map((s) => html`<button type="button" class="chip" data-suggest="${s}">${s}</button>`)}</div>
  </div>`.s;
}

async function loadHistory() {
  chat.loaded = true;
  try {
    const { messages } = await api("/chat/history");
    if (!messages.length) return renderEmptyChat();
    chatLog().innerHTML = messages.map((m) => messageHtml(m).s).join("");
    scrollChat();
  } catch (err) {
    chat.loaded = false;
    chatLog().innerHTML = html`<p class="chat-error small">${err.message}</p>`.s;
  }
}

function setBusy(busy) {
  chat.busy = busy;
  const panel = $("#chat-panel");
  $("textarea", panel).disabled = busy;
  $(".chat-send", panel).disabled = busy;
  $(".chat-typing", panel)?.remove();
  if (busy) {
    chatLog().insertAdjacentHTML("beforeend",
      `<div class="chat-msg assistant chat-typing" aria-label="L'assistant réfléchit"><div class="bubble"><span></span><span></span><span></span></div></div>`);
    scrollChat();
  }
}

async function sendMessage(text) {
  const message = text.trim();
  if (!message || chat.busy) return;
  const input = $("#chat-panel textarea");
  input.value = "";
  updateCounter();
  appendMessage({ role: "user", content: message });
  setBusy(true);
  try {
    const res = await api("/chat", { method: "POST", body: { message, mode: chat.mode ?? "normal" } });
    setBusy(false);
    appendMessage(res.message);
  } catch (err) {
    setBusy(false);
    chatLog().insertAdjacentHTML("beforeend", html`<p class="chat-error small">${err.message}</p>`.s);
    scrollChat();
    if (!input.value) input.value = message; // le message n'est pas perdu
  } finally {
    input.focus();
  }
}

async function handleAction(id, verb) {
  const card = $(`.chat-action[data-action="${id}"]`, chatLog());
  $$("button", card).forEach((b) => (b.disabled = true));
  try {
    const res = await api(`/chat/actions/${id}/${verb}`, { method: "POST" });
    card.outerHTML = actionCard(res.action).s;
    if (verb === "confirm") {
      toast("Modification enregistrée");
      await refreshPage();
    }
  } catch (err) {
    toast(err.message, true);
    $$("button", card).forEach((b) => (b.disabled = false));
    if (/déjà été traitée|expirée/.test(err.message)) loadHistory();
  }
}

function updateCounter() {
  const panel = $("#chat-panel");
  const input = $("textarea", panel);
  const max = chat.status?.max_message_chars ?? 4000;
  const left = max - input.value.length;
  const counter = $(".chat-counter", panel);
  counter.textContent = left < 500 ? `${left} caractères restants` : "";
  // Hauteur automatique (jusqu'à ~6 lignes)
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
}

function openChat(open = true) {
  const panel = $("#chat-panel");
  const fab = $("#chat-fab");
  panel.hidden = !open;
  fab.setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("chat-open", open);
  if (open) {
    if (!chat.loaded) loadHistory();
    $("textarea", panel).focus();
  } else {
    fab.focus();
  }
}

/**
 * Ouvre le panneau de l'assistant depuis une page. mode = "onboarding" : entretien pour compléter le profil
 * (l'assistant pose une question à la fois sur ce qui manque).
 */
export async function openChatPanel({ mode = "normal" } = {}) {
  if (!$("#chat-panel")) await mountChat();
  if (chat.status && !chat.status.enabled) {
    toast("Assistant indisponible : la clé Gemini (GEMINI_API_KEY) n'est pas configurée sur le serveur.", true);
    return;
  }
  chat.mode = mode;
  $("#chat-panel .chat-mode").hidden = mode !== "onboarding";
  openChat(true);
  if (mode === "onboarding") {
    if (!chat.loaded) await loadHistory();
    sendMessage("Je veux compléter mon profil : pose-moi tes questions, une à la fois.");
  }
}

async function mountChat() {
  if ($("#chat-fab")) return;
  const fab = document.createElement("button");
  fab.id = "chat-fab";
  fab.type = "button";
  fab.className = "chat-fab";
  fab.setAttribute("aria-controls", "chat-panel");
  fab.setAttribute("aria-expanded", "false");
  fab.setAttribute("aria-label", "Assistant");
  fab.innerHTML = CHAT_ICON.s;

  const panel = document.createElement("section");
  panel.id = "chat-panel";
  panel.className = "chat-panel";
  panel.hidden = true;
  panel.setAttribute("aria-label", "Assistant Mire");
  panel.innerHTML = html`
    <header class="chat-head">
      <div class="chat-title"><strong>Assistant</strong><span class="small muted chat-model"></span>
        <span class="pill plain chat-mode" hidden>Entretien <button type="button" class="link-btn" data-end-onboarding
          title="Revenir au mode normal" aria-label="Terminer l'entretien">×</button></span></div>
      <div class="chat-tools">
        <button type="button" class="icon-btn" data-reset title="Nouvelle conversation" aria-label="Nouvelle conversation">${RESET_ICON}</button>
        <button type="button" class="icon-btn" data-close title="Fermer" aria-label="Fermer l'assistant">${CLOSE_ICON}</button>
      </div>
    </header>
    <div class="chat-log" aria-live="polite"></div>
    <form class="chat-form">
      <textarea rows="1" maxlength="4000" placeholder="Votre message… (Entrée pour envoyer)" aria-label="Message pour l'assistant"></textarea>
      <button type="submit" class="chat-send" aria-label="Envoyer">${SEND_ICON}</button>
      <span class="chat-counter small muted"></span>
    </form>`.s;
  document.body.append(fab, panel);

  fab.addEventListener("click", () => {
    if (chat.status && !chat.status.enabled) {
      toast("Assistant indisponible : la clé Gemini (GEMINI_API_KEY) n'est pas configurée sur le serveur.", true);
      return;
    }
    openChat(panel.hidden);
  });
  // Échap ferme le panneau (sauf si un formulaire <dialog> est ouvert par-dessus : Échap ferme d'abord celui-ci)
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden && !document.querySelector("dialog[open]")) openChat(false);
  });
  const input = $("textarea", panel);
  input.addEventListener("input", updateCounter);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendMessage(input.value);
    }
  });
  $("form", panel).addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(input.value);
  });
  panel.addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.matches("[data-close]")) openChat(false);
    else if (t.matches("[data-suggest]")) sendMessage(t.dataset.suggest);
    else if (t.matches("[data-end-onboarding]")) {
      chat.mode = "normal";
      $(".chat-mode", panel).hidden = true;
    }
    else if (t.matches("[data-confirm]")) handleAction(t.dataset.confirm, "confirm");
    else if (t.matches("[data-cancel]")) handleAction(t.dataset.cancel, "cancel");
    else if (t.matches("[data-reset]") && !chat.busy) {
      if (!window.confirm("Commencer une nouvelle conversation ? L'historique actuel ne sera plus affiché.")) return;
      await attempt(() => api("/chat/reset", { method: "POST" }));
      renderEmptyChat();
    }
  });

  try {
    chat.status = await api("/chat/status");
  } catch {
    chat.status = { enabled: false };
  }
  if (chat.status.enabled) {
    fab.title = "Assistant";
    $(".chat-model", panel).textContent = chat.status.model ? ` · ${chat.status.model}` : "";
    input.maxLength = chat.status.max_message_chars;
  } else {
    fab.classList.add("disabled");
    fab.setAttribute("aria-disabled", "true");
    fab.title = "Assistant indisponible : GEMINI_API_KEY n'est pas configurée sur le serveur";
    fab.setAttribute("aria-label", fab.title);
  }
}

// --- Navigation sans rechargement ------------------------------------------
// Au clic sur un lien interne vers une page de l'app : on récupère la page HTML,
// on remplace seulement <main> (la barre latérale reste), puis on relance le script de la page.

const PAGES = new Set(NAV.map(([href]) => href));
let navToken = 0;
let navCount = 0;

async function navigate(href, { push = true } = {}) {
  const url = new URL(href, location.href);
  const token = ++navToken;
  let doc;
  try {
    const res = await fetch(url.pathname + url.search, { headers: { Accept: "text/html" } });
    if (!res.ok || res.redirected || !res.headers.get("content-type")?.includes("text/html")) throw new Error();
    doc = new DOMParser().parseFromString(await res.text(), "text/html");
  } catch {
    location.href = url.href; // session expirée, erreur réseau… : navigation classique
    return;
  }
  if (token !== navToken) return; // un autre clic a eu lieu entre-temps
  const main = doc.querySelector("main");
  const script = doc.querySelector('script[type="module"][src]');
  if (!main || !script) {
    location.href = url.href;
    return;
  }
  if (push) history.pushState(null, "", url.href);
  document.title = doc.title;
  $("main").replaceWith(main);
  window.scrollTo(0, 0);
  // Paramètre unique : le navigateur ré-exécute le module de la page (app.js, lui, reste partagé)
  const src = new URL(script.getAttribute("src"), location.origin).pathname;
  try {
    await import(`${src}?nav=${++navCount}`);
  } catch (err) {
    // Le script de la page n'a pas pu se charger (ex. app.js en mémoire plus ancien que la page, après une mise à
    // jour de Mire) : rechargement complet, qui charge les versions à jour.
    console.warn("Navigation : rechargement complet", err);
    location.reload();
  }
}

document.addEventListener("click", (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const a = e.target.closest("a[href]");
  if (!a || a.target || a.hasAttribute("download")) return;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin || !PAGES.has(pagePath(url.pathname))) return;
  e.preventDefault();
  if (pagePath(url.pathname) === pagePath(location.pathname) && url.search === location.search) return;
  navigate(url.href);
});

window.addEventListener("popstate", () => navigate(location.href, { push: false }));
