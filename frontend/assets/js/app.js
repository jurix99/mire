// Mire — code partagé par toutes les pages (API, barre latérale, pastilles, logos, formulaires)

import { planFeatures } from "./plans.js";

// --- Statuts (clés identiques à l'enum process_status du backend) -----------

export const STATUSES = [
  ["nouveau", "Nouveau"],
  ["a_postuler", "À regarder"],
  ["postule", "Postulé"],
  ["relance", "Relance"],
  ["entretien", "Entretien"],
  ["offre", "Offre"],
  ["refuse", "Refusé"],
  ["pas_interesse", "Pas intéressé"],
  ["abandonne", "Abandonné"],
];
export const STATUS_LABEL = Object.fromEntries(STATUSES);

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
    ${STATUSES.map(([k, l]) => html`<option value="${k}" ${raw(k === current ? "selected" : "")}>${l}</option>`)}
  </select>`;
}

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
  thumbDown: raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 14V2"/><path d="M9 18.1 10 14H4.2a2 2 0 0 1-2-2.4l1.4-7A2 2 0 0 1 5.6 3H17v11l-4 8a2.4 2.4 0 0 1-4-.9Z"/></svg>`),
};

// --- Notifications ----------------------------------------------------------

let toastTimer;
/** action : { label, onClick } pour un bouton dans la notification (ex. « Passer Pro »). */
export function toast(message, error = false, action = null) {
  let el = $(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("role", "status");
    document.body.append(el);
  }
  el.textContent = message;
  if (action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "toast-action";
    btn.textContent = action.label;
    btn.addEventListener("click", () => { el.hidden = true; action.onClick(); });
    el.append(btn);
  }
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

/** Fenêtre « Passer Pro » : ce qui est débloqué, les deux prix, la renonciation au droit de rétractation. */
export async function openProModal() {
  if ($("dialog.pro-dialog")) return;
  let billing = null;
  try { billing = (await loadMe()).billing; } catch { /* affichée sans les chiffres */ }
  const free = billing?.limits ?? {};
  const pro = billing?.pro_limits ?? {};
  const prices = billing?.prices ?? {};
  const isPro = billing && billing.plan === "pro";
  const list = planFeatures(free, pro);
  const feats = (items) => html`<ul class="plan-feats">${items.map(([on, text]) => html`<li class="${on ? "" : "off"}">${text}</li>`)}</ul>`;
  // « 10 € TTC / mois » -> montant en grand, le reste en petit (comme sur la landing)
  const [, amount = prices.monthly ?? "Pro", unit = ""] = (prices.monthly ?? "").match(/^(.*?€)\s*(.*)$/) ?? [];
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
          <p class="plan-sub">Sans engagement. Ou <strong>${prices.pass_3m ?? "le Pass 3 mois"}</strong>, sans renouvellement.</p>
          ${feats(list.pro)}
          ${isPro ? html`<p class="plan-sub"><strong>Vous avez déjà l'offre Pro.</strong></p>` : html`
          <div class="plan-ctas">
            <button type="button" class="plan-cta" data-checkout="monthly">Passer Pro</button>
            <button type="button" class="plan-cta ghost" data-checkout="pass_3m">Pass 3 mois</button>
          </div>
          <p class="pro-waiver">En cliquant sur « Passer Pro » ou « Pass 3 mois », vous demandez l'accès immédiat à Mire Pro
            et renoncez à votre droit de rétractation de 14 jours (<a href="/cgv#retractation" target="_blank" rel="noopener">CGV, art. 6</a>).</p>
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

// --- Retours sur une offre (J'aime / Pas intéressé) -------------------------------

let reasonsPromise;

/**
 * Demande la raison (facultative) puis enregistre le retour. « J'aime » ajoute l'offre aux références ;
 * « Pas intéressé » pose ce statut. Retourne la réponse de l'API (ou null si annulé).
 */
export async function openFeedback(job, sentiment) {
  const like = sentiment === "like";
  reasonsPromise ??= api("/feedback/reasons");
  const reasons = like ? [] : await reasonsPromise;
  if (job.locked) return openProModal(); // offre récente verrouillée (Free)
  const values = await openForm({
    title: like ? `J'aime : ${plain(job.title)}` : `Pas intéressé : ${plain(job.title)}`,
    fields: [
      ...(like ? [] : [{ name: "reasons", label: "Pourquoi ? (facultatif)", type: "checks", full: true,
                         options: reasons.map((r) => [r.key, r.label]) }]),
      { name: "text", label: like ? "Qu'est-ce qui vous plaît ? (facultatif)" : "Précisez (facultatif)",
        type: "textarea", full: true, maxlength: 1000,
        hint: like ? "L'offre rejoint vos offres de référence : les offres qui lui ressemblent seront mieux notées."
                   : "Vos retours servent à proposer de nouvelles règles, que vous validez une par une." },
    ],
    submitLabel: like ? "J'aime" : "Pas intéressé",
  });
  if (!values) return null;
  const res = await attempt(() => api(`/jobs/${job.id}/feedback`, {
    method: "POST", body: { sentiment, reasons: values.reasons ?? [], text: values.text },
  }), like ? "Ajoutée à vos offres de référence" : "Noté : pas intéressé");

  // « Déjà passé en entretien » : proposer d'exclure l'entreprise (validation explicite)
  const it = res.proposal?.items?.[0];
  if (it && window.confirm(`Exclure ${it.value.name} des prochaines offres ? (vous êtes déjà passé en entretien)`)) {
    await attempt(() => api(`/profile/proposals/${res.proposal.import_id}/apply`, { method: "POST", body: { accept: [it.id] } }),
      `${it.value.name} ajoutée aux entreprises exclues`);
  }
  if (res.suggest_synthesis) {
    setTimeout(() => toast(`${res.new_feedback} nouveaux retours : mettez à jour vos règles depuis « Mon profil ».`), 2600);
  }
  refreshPage();
  return res;
}

// --- Fiche d'un poste (contenu de l'offre) ------------------------------------

const WORK_MODE_LABEL = { sur_site: "Sur site", hybride: "Hybride", remote: "Remote" };
const httpUrl = (u) => (u && /^https?:\/\//i.test(u) ? u : null);

/**
 * Ouvre la fiche d'un poste dans un <dialog> : contenu de l'offre, stack, et process de l'utilisateur.
 * onEdit(job) : facultatif, ajoute un bouton « Modifier » (la fiche se ferme puis onEdit est appelé).
 */
export async function openJobDialog(jobId, { onEdit } = {}) {
  const dlg = document.createElement("dialog");
  dlg.className = "job-dialog";
  dlg.setAttribute("aria-label", "Détail du poste");
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
    const stack = (job.stack ?? "").split(/[,;·\n]/).map((s) => s.trim()).filter(Boolean);
    const meta = [job.location, job.seniority, job.category].filter(Boolean).join(" · ");
    const url = httpUrl(job.url);
    const app = job.application;
    const appRows = app ? [
      ["Statut", pill(app.status)],
      ["Candidature", app.applied_on ? fmtDate(app.applied_on) : ""],
      ["Prochaine étape", [app.next_step, app.next_step_date ? `le ${fmtDate(app.next_step_date)}` : ""].filter(Boolean).join(" ")],
    ].filter(([, v]) => v) : [];
    const a = job.assessment;

    dlg.innerHTML = html`
      <div class="dialog-body">
        <div class="job-head">
          ${logo(job.company, "lg")}
          <div class="grow">
            <h2>${renderValue(job.title)}</h2>
            <p class="muted small">${renderValue(job.company.name)}${meta ? ` · ${meta}` : ""}</p>
          </div>
          <button type="button" class="icon-btn" data-close aria-label="Fermer">×</button>
        </div>
        ${job.locked ? proBar(`Offre publiée ${fmtAgo(job.published_on ?? job.created_at)} : réservée au Pro.`) : ""}
        <div class="meta">
          ${pill(job.display_status)}${score(job.score)}
          ${a?.manual_score != null ? html`<span class="small muted">note corrigée par vous</span>` : ""}
          ${job.source ? html`<span class="small muted">Source : ${job.source}</span>` : ""}
        </div>
        <div class="job-facts small">
          ${job.locked ? "" : job.closed_at ? html`<span class="fact closed">Offre fermée le ${fmtDate(job.closed_at)}</span>`
            : job.verified_open_at ? html`<span class="fact open">Ouverture vérifiée le ${fmtDate(job.verified_open_at)}</span>`
            : html`<span class="fact muted">Ouverture non vérifiée</span>`}
          ${job.salary ? html`<span class="fact">Salaire : ${renderValue(job.salary)}</span>` : ""}
          ${job.work_mode ? html`<span class="fact">${WORK_MODE_LABEL[job.work_mode] ?? job.work_mode}</span>` : ""}
          ${job.travel ? html`<span class="fact">Déplacements : ${job.travel}</span>` : ""}
          ${job.role_type ? html`<span class="fact">${job.role_type}</span>` : ""}
          ${job.published_on ? html`<span class="fact muted">Publiée le ${fmtDate(job.published_on)}</span>` : ""}
        </div>
        ${stack.length ? html`<div class="tags">${stack.map((s) => html`<span class="tag">${s}</span>`)}</div>` : ""}
        ${job.locked ? "" : assessmentSection(a)}
        <section class="job-section">
          <p class="label">// L'offre</p>
          ${isLocked(job.summary) ? renderValue(job.summary, { block: true })
            : job.summary ? html`<div class="job-text prewrap">${job.summary}</div>`
            : html`<p class="muted small">Pas de description enregistrée pour ce poste.${url ? " Consultez l'offre originale." : ""}</p>`}
        </section>
        ${app ? html`<section class="job-section">
          <p class="label">// Mon process</p>
          <div class="rows">${appRows.map(([k, v]) => html`
            <div class="row-item"><span class="muted small" style="min-width:120px">${k}</span><span class="grow">${v}</span></div>`)}
          </div>
          ${app.notes ? html`<div class="job-text prewrap small">${app.notes}</div>` : ""}
        </section>` : ""}
        <p class="small muted">Ajouté le ${fmtDate(job.created_at)}</p>
      </div>
      <div class="dialog-foot">
        <div class="feedback-buttons">
          ${url ? html`<a class="btn ghost" href="${url}" target="_blank" rel="noopener noreferrer">Voir l'offre originale ${ICONS.link}</a>`
            : isLocked(job.url) ? html`<button type="button" class="btn ghost" data-pro>Voir l'offre originale ${proTag}</button>` : ""}
          ${job.locked ? "" : html`
          <button type="button" class="btn ghost fb-btn fb-like ${job.feedback === "like" ? "is-on" : ""}" data-feedback="like"
            aria-pressed="${job.feedback === "like"}" title="Offre de référence">${ICONS.heart} ${job.feedback === "like" ? "Vous aimez" : "J'aime"}</button>
          <button type="button" class="btn ghost fb-btn fb-dislike ${job.feedback === "dislike" ? "is-on" : ""}" data-feedback="dislike"
            aria-pressed="${job.feedback === "dislike"}">${ICONS.thumbDown} ${job.feedback === "dislike" ? "Pas intéressé (noté)" : "Pas intéressé"}</button>`}
        </div>
        <div class="right">
          ${onEdit && !job.locked ? html`<button type="button" class="btn ghost" data-edit data-admin>Modifier</button>` : ""}
          <button type="button" class="btn" data-close>Fermer</button>
        </div>
      </div>`.s;
  };

  const saveManual = async (value) => {
    job = { ...job, ...(await attempt(() => api(`/jobs/${job.id}/assessment/manual`, { method: "PUT", body: { score: value } }),
      value == null ? "Note calculée rétablie" : "Note corrigée")) };
    draw();
    refreshPage();
  };

  draw();
  dlg.addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) dlg.close();
    if (e.target.closest("[data-reset-score]")) saveManual(null).catch(() => {});
    const fb = e.target.closest("[data-feedback]");
    if (fb) {
      openFeedback(job, fb.dataset.feedback).then((res) => {
        if (res) { job = { ...job, ...res.job }; draw(); }
      }).catch(() => {});
    }
    if (e.target.closest("[data-edit]")) {
      dlg.close();
      onEdit(job);
    }
  });
  dlg.addEventListener("submit", (e) => {
    if (!e.target.matches(".manual-score")) return;
    e.preventDefault();
    const raw = e.target.elements.score.value;
    if (raw === "") return toast("Indiquez une note entre 0 et 10", true);
    saveManual(Number(raw)).catch(() => {});
  });
  $("[data-close]", dlg).focus();
}

/** Section « Mon évaluation » de la fiche : verdict, raisons, détail du barème, compétences, correction. */
function assessmentSection(a) {
  if (!a) {
    return html`<section class="job-section"><p class="label">// Mon évaluation</p>
      <p class="small muted">Pas encore évaluée pour vous : l'évaluation se fait automatiquement dès que votre profil est rempli.</p>
    </section>`;
  }
  const tags = (label, list, cls) => (isLocked(list)
    ? html`<div class="tags"><span class="small muted">${label}</span>${renderValue(list)}</div>`
    : list.length ? html`<div class="tags"><span class="small muted">${label}</span>${list.map((s) => html`<span class="tag ${cls}">${s}</span>`)}</div>` : "");
  const auto = plain(a.auto_score);
  return html`<section class="job-section">
    <p class="label">// Mon évaluation</p>
    ${a.exclusion ? html`<p class="small exclusion">Écartée : ${renderValue(a.exclusion)}</p>` : ""}
    ${a.why ? html`<p class="small">${renderValue(a.why, { block: true })}</p>` : ""}
    ${isLocked(a.criteria) ? html`<div class="criteria">${renderValue(a.criteria, { block: true })}</div>`
      : a.criteria.length ? html`<div class="criteria">${a.criteria.map((c) => html`
      <div class="criterion">
        <span class="small">${c.label}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${Math.round((c.points / (c.max || 1)) * 100)}%"></span></span>
        <span class="small n">${c.points.toLocaleString("fr-FR")} / ${c.max.toLocaleString("fr-FR")}</span>
      </div>`)}</div>` : ""}
    ${tags("Vous avez :", a.skills_have, "have")}
    ${tags("Manque :", a.skills_missing, "missing")}
    <form class="manual-score">
      <label class="small">Ma note
        <input class="input" name="score" type="number" min="0" max="10" step="0.5" value="${a.manual_score ?? ""}"
          placeholder="${auto}" aria-label="Corriger la note (sur 10)">
      </label>
      <button class="btn ghost sm">Corriger</button>
      ${a.manual_score != null ? html`<button type="button" class="btn ghost sm" data-reset-score>Revenir à la note calculée${auto !== "" ? ` (${auto})` : ""}</button>` : ""}
      <span class="small muted">${a.stale ? "Réévaluation en attente (profil modifié)" : `Évaluée le ${fmtDate(a.assessed_at)}`}</span>
    </form>
  </section>`;
}

// --- Barre latérale ---------------------------------------------------------

const NAV = [
  ["/tableau-de-bord", "Tableau de bord", null],
  ["/entreprises", "Entreprises", "companies"],
  ["/postes", "Postes", "jobs"],
  ["/sources", "Sources", null, true], // réservée aux admins (gestion du radar partagé)
  ["/mes-process", "Mes process", "processes"],
  ["/analytics", "Analytics", null],
  ["/mon-profil", "Mon profil", null],
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
  // Le bloc « Veille » mène à la page Sources, réservée aux admins
  const watch = $("#watch");
  if (watch) notAdmin ? watch.removeAttribute("href") : watch.setAttribute("href", "/sources");
  refreshWatch();
}

/** Bloc « Veille » de la barre latérale : planning de l'agent et dernier passage. */
async function refreshWatch() {
  if (!$("#watch")) return;
  let data;
  try {
    data = await api("/runs?limit=1");
  } catch {
    return;
  }
  const set = (key, text) => { const el = $(`[data-watch="${key}"]`); if (el) el.textContent = text; };
  set("schedule", data.schedule ?? "Non configurés");
  const last = data.runs[0];
  set("last", last ? `${fmtDate(last.started_at)} · +${last.jobs_added}` : "Aucun");
  const n = last?.mine?.nouvelles;
  set("mine", n ? `${n} nouvelle${n > 1 ? "s" : ""} offre${n > 1 ? "s" : ""} retenue${n > 1 ? "s" : ""} pour vous` : "");
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
    <a class="brand" href="/tableau-de-bord" aria-label="Mire, tableau de bord">${raw(LOGO_SVG)}<span>Mire</span></a>
    <nav class="nav" aria-label="Navigation principale">
      ${NAV.map(([href, label, count, adminOnly]) => html`
        <a href="${href}" ${raw(path === href ? 'aria-current="page"' : "")} ${raw(adminOnly ? "data-admin" : "")}>
          <span>${label}</span>${count ? html`<span class="count" data-count="${count}"></span>` : ""}
        </a>`)}
    </nav>
    <a class="watch" href="/sources" id="watch" style="text-decoration:none">
      <p class="label">// Veille personnelle</p>
      <p class="small" data-watch="schedule">…</p>
      <div class="row"><span class="muted">Dernier passage</span><span data-watch="last">…</span></div>
      <p class="small muted" data-watch="mine"></p>
    </a>
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
  await done;
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
