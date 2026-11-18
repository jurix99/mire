// Mire — code partagé par toutes les pages (API, barre latérale, pastilles, logos, formulaires)

// --- Statuts (clés identiques à l'enum process_status du backend) -----------

export const STATUSES = [
  ["nouveau", "Nouveau"],
  ["a_postuler", "À postuler"],
  ["postule", "Postulé"],
  ["relance", "Relance"],
  ["entretien", "Entretien"],
  ["offre", "Offre"],
  ["refuse", "Refusé"],
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
  if (!res.ok) {
    const d = data?.detail;
    const msg = Array.isArray(d) ? d.map((e) => `${e.loc?.at(-1) ?? ""} : ${e.msg}`).join(" · ") : d;
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
export const fmtMoney = (m) => (m == null ? "" : `${Number(m).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} M€`);
export function daysUntil(iso) {
  if (!iso) return null;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((parseDay(iso) - today) / 86400000);
}

// --- Composants -------------------------------------------------------------

export const pill = (status) => html`<span class="pill st-${status}">${STATUS_LABEL[status] ?? status}</span>`;

export function statusSelect(current, attrs = "") {
  return html`<select class="status-select st-${current}" aria-label="Statut" ${raw(attrs)}>
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

/** Logo d'entreprise : image récupérée par le backend, avec initiales colorées en repli. */
export function logo(company, size = "") {
  const bust = company._v ? `?v=${company._v}` : "";
  return html`<span class="logo ${size}" style="--hue:${hue(company.name)}" data-initials="${initials(company.name)}">
    <img src="${company.logo}${bust}" alt="" loading="lazy">
  </span>`;
}
// Image introuvable (404) : on retire l'<img> pour laisser apparaître les initiales
document.addEventListener("error", (e) => {
  if (e.target instanceof HTMLImageElement && e.target.parentElement?.classList.contains("logo")) e.target.remove();
}, true);

export const score = (s) => (s == null ? "" : html`<span class="score ${s < 7.5 ? "low" : ""}">${Number(s).toFixed(1)}</span>`);

export const ICONS = {
  edit: raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`),
  trash: raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>`),
  link: raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>`),
  image: raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/></svg>`),
  arrow: raw(`<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>`),
  plus: raw(`<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>`),
};

// --- Notifications ----------------------------------------------------------

let toastTimer;
export function toast(message, error = false) {
  let el = $(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("role", "status");
    document.body.append(el);
  }
  el.textContent = message;
  el.classList.toggle("error", error);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), error ? 5000 : 2500);
}

/** Exécute une action et affiche l'erreur éventuelle. */
export async function attempt(fn, okMessage) {
  try {
    const r = await fn();
    if (okMessage) toast(okMessage);
    return r;
  } catch (err) {
    toast(err.message, true);
    throw err;
  }
}

// --- Formulaire générique dans un <dialog> -----------------------------------

/**
 * Ouvre un formulaire et résout avec les valeurs saisies (null si annulé).
 * fields : [{ name, label, type: text|url|number|date|textarea|select|file, options: [[v, l]], required, full, hint, step, min, max }]
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
        if (f.type === "file") out[f.name] = input.files[0] ?? null;
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
  const common = raw(`id="${id}" name="${esc(f.name)}" ${f.required ? "required" : ""}`);
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

// --- Barre latérale ---------------------------------------------------------

const NAV = [
  ["/", "Tableau de bord", null],
  ["/entreprises", "Entreprises", "companies"],
  ["/postes", "Postes", "jobs"],
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
    <a class="brand" href="/" aria-label="Mire, tableau de bord">${raw(LOGO_SVG)}<span>Mire</span></a>
    <nav class="nav" aria-label="Navigation principale">
      ${NAV.map(([href, label, count]) => html`
        <a href="${href}" ${raw(path === href ? 'aria-current="page"' : "")}>
          <span>${label}</span>${count ? html`<span class="count" data-count="${count}"></span>` : ""}
        </a>`)}
    </nav>
    <div class="watch">
      <p class="label">// Veille personnelle</p>
      <div class="row"><span class="muted">Prochain passage auto</span><span>Non configuré</span></div>
      <p class="small muted">Données de démonstration</p>
    </div>`.s;
  }
  try {
    await refreshCounts();
  } catch (err) {
    toast(err.message, true);
  }
}

/** Point d'entrée de chaque page. */
export async function boot(render) {
  const main = $("main");
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
  await done;
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
  await import(`${src}?nav=${++navCount}`);
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
