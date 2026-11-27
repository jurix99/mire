// Entreprises : trouver celles qui valent la peine. Classées d'après leurs meilleures offres pour l'utilisateur et
// ses retours (ranking.py). J'aime : leurs offres remontent. Pas intéressé : elles sortent, offres comprises.
import {
  $, ICONS, api, attempt, boot, confirmDelete, fmtDate, fmtMoney, html, isLocked, loadMe, lockedBlock, logo,
  matchBadge, openForm, openJobDialog, plain, proBar, reactToCompany, reactionButtons, reasonList, refreshCounts,
  renderPaged, renderValue, shown, toast,
} from "./app.js";

const VIEWS = ["pour-vous", "aimees", "ecartees"];
const params = new URLSearchParams(location.search);
const state = {
  view: VIEWS.includes(params.get("vue")) ? params.get("vue") : "pour-vous",
  q: "", secteur: "", stade: "", sort: "match", all: false,
};
let companies = [];
let order = [];

const ATS_OPTIONS = [["ashby", "Ashby"], ["lever", "Lever"], ["greenhouse", "Greenhouse"], ["recruitee", "Recruitee"],
  ["teamtailor", "Teamtailor"], ["yc", "YC (Work at a Startup)"], ["autre", "Autre"]];
const AUTO_ATS = new Set(["ashby", "lever", "greenhouse", "recruitee"]);

const FIELDS = [
  { name: "name", label: "Nom", required: true },
  { name: "website", label: "Site web", type: "url", placeholder: "https://…", hint: "Sert aussi à trouver le logo" },
  { name: "sector", label: "Secteur", placeholder: "SaaS & IA métier" },
  { name: "city", label: "Ville" },
  { name: "funding_stage", label: "Tour de financement", placeholder: "Série A" },
  { name: "funding_amount_meur", label: "Montant levé (M€)", type: "number", step: "0.1", min: 0 },
  { name: "funding_announced_on", label: "Date de la levée", type: "date" },
  { name: "investors", label: "Investisseurs" },
  { name: "description", label: "Description", type: "textarea", full: true },
  { name: "ats", label: "ATS (logiciel de recrutement)", type: "select", options: [["", "Inconnu"], ...ATS_OPTIONS],
    hint: "Ashby, Lever, Greenhouse et Recruitee : vérification automatique des offres" },
  { name: "ats_slug", label: "Identifiant ATS (slug)", placeholder: "ex. remanence",
    hint: "La fin de l'URL du tableau d'offres : jobs.ashbyhq.com/<slug>" },
  { name: "careers_url", label: "Page carrières", type: "url", full: true },
  { name: "headcount", label: "Effectif approximatif", placeholder: "11-50" },
  { name: "logo_url", label: "URL du logo (facultatif)", type: "url", full: true, hint: "Sinon le favicon du site est utilisé" },
  { name: "logo_file", label: "Ou importer un logo", type: "file", accept: "image/png,image/jpeg,image/gif,image/webp,image/svg+xml,image/x-icon", full: true, hint: "PNG, JPEG, SVG, WebP… 512 Ko max" },
];

// --- Vues, filtres, tri ---------------------------------------------------------------------------------

// Secteurs : le champ est libre (« SaaS, IA, finance ») : découpé en tags pour un filtre court
const tagKey = (t) => t.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
const sectorTags = (sector) => (sector ?? "").split(/\s*(?:[,/&·;+]|\set\s)\s*/).map((t) => t.trim()).filter((t) => t.length > 1);

const inView = {
  "pour-vous": (c) => !c.hidden_reason,
  aimees: (c) => c.reaction === "like",
  ecartees: (c) => !!c.hidden_reason,
};

function matches(c) {
  if (!inView[state.view](c)) return false;
  // Par défaut, seulement les entreprises qui ont une offre ouverte pour l'utilisateur : c'est là qu'on postule
  if (state.view === "pour-vous" && !state.all && !c.locked && !c.top_jobs.length) return false;
  if (state.secteur && !sectorTags(c.sector).some((t) => tagKey(t) === state.secteur)) return false;
  if (state.stade && plain(c.funding_stage) !== state.stade) return false;
  const q = state.q.trim().toLowerCase();
  return !q || [c.name, c.sector, c.description, c.investors, c.city].some((v) => plain(v).toLowerCase().includes(q));
}

const SORTS = {
  recent: (a, b) => (plain(b.funding_announced_on) || "").localeCompare(plain(a.funding_announced_on) || ""),
  name: (a, b) => plain(a.name).localeCompare(plain(b.name), "fr", { sensitivity: "base" }),
};

/** Ordre par pertinence : classées d'abord, les entreprises floutées (Free) toujours à la fin. */
const byMatch = (list) => [...list].sort((a, b) => (a.locked - b.locked)
  || ((b.match?.score ?? -1) - (a.match?.score ?? -1)) || SORTS.recent(a, b) || SORTS.name(a, b));

function visible() {
  const byId = new Map(companies.map((c) => [c.id, c]));
  const list = order.map((id) => byId.get(id)).filter(Boolean).filter(matches);
  return state.sort === "match" ? list : list.sort((a, b) => (a.locked - b.locked) || SORTS[state.sort](a, b));
}

function counted(values) {
  return [...values.reduce((acc, [key, label]) => {
    const entry = acc.get(key) ?? { label, n: 0 };
    entry.n += 1;
    return acc.set(key, entry);
  }, new Map())].sort((a, b) => b[1].n - a[1].n);
}

function drawFilters() {
  const pool = companies.filter((c) => inView[state.view](c) && (state.view !== "pour-vous" || state.all || c.top_jobs.length));
  const sectors = counted(pool.flatMap((c) => [...new Map(sectorTags(c.sector).map((t) => [tagKey(t), t]))]))
    .filter(([k, e]) => e.n >= 2 || k === state.secteur);
  const stages = counted(pool.map((c) => plain(c.funding_stage)).filter(Boolean).map((s) => [s, s]));
  const option = (current) => ([k, e]) => html`<option value="${k}" ${k === current ? "selected" : ""}>${e.label} (${e.n})</option>`;
  $("#f-secteur").innerHTML = html`<option value="">Tous les secteurs</option>${sectors.map(option(state.secteur))}`.s;
  $("#f-stade").innerHTML = html`<option value="">Tous les stades</option>${stages.map(option(state.stade))}`.s;
  $("#f-stade").hidden = !stages.length;
  $("#f-all-wrap").hidden = state.view !== "pour-vous";
  for (const view of VIEWS) {
    const btn = $(`[data-view="${view}"]`);
    btn.setAttribute("aria-selected", String(view === state.view));
    $(".n", btn).textContent = companies.filter((c) => inView[view](c)
      && (view !== "pour-vous" || state.all || c.locked || c.top_jobs.length)).length;
  }
}

// --- Cartes ---------------------------------------------------------------------------------------------

const lockedCard = (c) => html`
  <article class="card card-locked" data-id="${c.id}">
    ${lockedBlock(html`
      <div class="co-head"><span class="logo lg is-locked" data-initials="?"></span>
        <div><h2>${shown(c.name)}</h2><p class="small muted">${c.sector ?? "Secteur à préciser"}</p></div></div>
      <p class="small">${shown(c.description) || "Lorem ipsum dolor sit amet consectetur adipiscing elit."}</p>
      <p class="small muted">Série A · 12,5 M€</p>
      <p class="small">${shown(c.jobs_count)} offres</p>`)}
  </article>`;

/** Levée en une ligne (floutée d'un seul tenant en Free). */
function funding(c) {
  const fields = [c.funding_stage, c.funding_amount_meur, c.funding_announced_on];
  if (fields.some(isLocked)) {
    return lockedBlock(html`<p class="small">Série A · 12,5 M€ · 12/03/2026</p>`, { cls: "funding-lock" });
  }
  const parts = [c.funding_stage, c.funding_amount_meur != null ? fmtMoney(c.funding_amount_meur) : "",
    c.funding_announced_on ? `levée le ${fmtDate(c.funding_announced_on)}` : ""].filter(Boolean);
  return parts.length ? html`<p class="small muted">${parts.join(" · ")}</p>` : "";
}

function card(c) {
  if (c.locked) return lockedCard(c);
  const rest = c.open_jobs - c.top_jobs.length;
  return html`
  <article class="card company-card ${state.view === "ecartees" ? "is-excluded" : ""}" data-id="${c.id}">
    <div class="co-head">
      ${logo(c, "lg")}
      <div class="grow">
        <h2>${c.name}</h2>
        <p class="small muted">${c.sector ?? ""}${c.sector && c.city ? " · " : ""}${c.city ? renderValue(c.city) : ""}</p>
      </div>
      ${state.view === "ecartees" ? "" : matchBadge(c.match)}
    </div>
    ${funding(c)}
    ${c.description ? html`<p class="small clamp">${renderValue(c.description, { block: true })}</p>` : ""}
    ${state.view === "ecartees" ? html`<p class="small exclusion-note">${c.hidden_reason}</p>` : reasonList(c.match)}
    ${c.top_jobs.length && state.view !== "ecartees" ? html`<ul class="top-jobs">${c.top_jobs.map((j) => html`
      <li><button type="button" class="link-btn" data-job="${j.id}">
        <span class="grow">${j.title}${j.city ? html`<span class="muted"> · ${j.city}</span>` : ""}</span>
        <span class="small muted">${j.score}</span></button></li>`)}</ul>` : ""}
    <div class="job-card-actions">
      ${reactionButtons(c.reaction)}
      <span class="spacer"></span>
      ${c.open_jobs ? html`<a class="small" href="/postes?entreprise=${c.id}">${rest > 0 ? `Les ${c.open_jobs} offres` : "Voir les offres"}</a>`
        : html`<span class="small muted">Pas d'offre ouverte</span>`}
      ${c.careers_url && !isLocked(c.careers_url) ? html`<a class="small" href="${c.careers_url}" target="_blank" rel="noopener">Carrières</a>` : ""}
      ${c.website && !isLocked(c.website) ? html`<a class="icon-btn" href="${c.website}" target="_blank" rel="noopener" title="Site web">${ICONS.link}</a>` : ""}
    </div>
    <div class="admin-actions" data-admin>
      <button class="link-btn small" type="button" data-action="edit">Modifier</button>
      ${AUTO_ATS.has(c.ats) && c.ats_slug ? html`<button class="link-btn small" type="button" data-action="check">Vérifier les offres</button>` : ""}
      <button class="link-btn small" type="button" data-action="refresh-logo">Recharger le logo</button>
    </div>
  </article>`;
}

const EMPTY = {
  "pour-vous": "Aucune entreprise ne correspond. Cochez « Aussi sans offre pour moi » ou élargissez les filtres.",
  aimees: "Rien pour l'instant : cliquez sur le cœur d'une entreprise, ses offres remontent dans vos propositions.",
  ecartees: "Aucune entreprise écartée.",
};

function draw({ keep = false } = {}) {
  drawFilters();
  const list = visible();
  $("#count").textContent = `${list.length} entreprise${list.length > 1 ? "s" : ""}`;
  renderPaged($("#list"), list, card, { page: 24, keep, empty: html`<p class="empty card">${EMPTY[state.view]}</p>` });
}

// --- Données ----------------------------------------------------------------------------------------------

async function load() {
  companies = await api("/companies");
  const v = Date.now(); // force le rechargement des logos modifiés
  companies.forEach((c) => (c._v = v));
}

async function reload(message) {
  const first = !order.length;
  await load();
  if (first || message) order = byMatch(companies).map((c) => c.id);
  draw({ keep: !first });
  drawProBanner().catch(() => {});
  if (message) {
    refreshCounts();
    toast(message);
  }
}

/** Offre Free : nombre de levées récentes floutées (calculé par l'API). */
async function drawProBanner() {
  const b = (await loadMe()).billing;
  const n = b?.enabled && b.plan === "free" ? b.locked_fundings : 0;
  $("#pro-banner").innerHTML = n ? proBar(`${n} levée${n > 1 ? "s" : ""} récente${n > 1 ? "s" : ""} floutée${n > 1 ? "s" : ""}`).s : "";
}

// --- Administration ---------------------------------------------------------------------------------------

async function uploadLogo(id, file) {
  const form = new FormData();
  form.append("file", file);
  await api(`/companies/${id}/logo`, { method: "PUT", form });
}

async function editCompany(company) {
  // Offre Free : les champs réservés au Pro (valeur verrouillée) ne sont ni affichés ni renvoyés
  const fields = company ? FIELDS.filter((f) => !isLocked(company[f.name])) : FIELDS;
  const result = await openForm({
    title: company ? `Modifier ${company.name}` : "Nouvelle entreprise",
    fields,
    values: company ? Object.fromEntries(Object.entries(company).filter(([, v]) => !isLocked(v))) : {},
    submitLabel: company ? "Enregistrer" : "Ajouter",
    danger: company && {
      label: "Supprimer",
      onClick: async () => {
        if (!confirmDelete(`${company.name} et ses ${company.jobs_count} offre(s)`)) return false;
        await api(`/companies/${company.id}`, { method: "DELETE" });
        return true;
      },
    },
    onSubmit: async ({ logo_file, ...values }) => {
      // En modification, on n'envoie que les champs changés (changer le site ou l'URL du logo réinitialise le logo)
      const changed = company
        ? Object.fromEntries(Object.entries(values).filter(([k, v]) => String(v ?? "") !== String(company[k] ?? "")))
        : values;
      const saved = company
        ? await api(`/companies/${company.id}`, { method: "PATCH", body: changed })
        : await api("/companies", { method: "POST", body: values });
      if (logo_file) await uploadLogo(saved.id, logo_file);
      return saved;
    },
  });
  if (result) await reload(result.deleted ? "Entreprise supprimée" : "Entreprise enregistrée");
}

/** Vérifie les offres dans la liste actuelle de l'ATS, puis propose d'ajouter les nouvelles. */
async function checkCompany(company, btn) {
  btn.disabled = true;
  btn.textContent = "Vérification…";
  let res;
  try {
    res = await attempt(() => api(`/companies/${company.id}/check`, { method: "POST" }));
  } catch {
    btn.disabled = false;
    btn.textContent = "Vérifier les offres";
    return;
  }
  if (res.error) {
    toast(res.error, true);
    return reload();
  }
  const summary = `${res.listed} offre${res.listed > 1 ? "s" : ""} listée${res.listed > 1 ? "s" : ""} · `
    + `${res.verified.length} confirmée${res.verified.length > 1 ? "s" : ""} ouverte${res.verified.length > 1 ? "s" : ""} · `
    + `${res.closed.length} fermée${res.closed.length > 1 ? "s" : ""}`;
  if (!res.new_postings.length) {
    toast(`${company.name} : ${summary}. Aucune nouvelle offre.`);
    return reload();
  }
  const fmt = (p) => [p.title, p.location, p.published_on ? `publiée le ${fmtDate(p.published_on)}` : ""]
    .filter(Boolean).join(" · ");
  const values = await openForm({
    title: `${company.name} : nouvelles offres`,
    fields: [{ name: "ids", label: `${res.new_postings.length} offre${res.new_postings.length > 1 ? "s" : ""} pas encore dans Mire`,
               type: "checks", full: true, options: res.new_postings.map((p) => [p.ats_job_id, fmt(p)]),
               hint: "Les offres ajoutées sont vérifiées ouvertes, puis classées pour chaque utilisateur." }],
    extra: html`<p class="small muted">${summary}</p>`,
    submitLabel: "Ajouter au radar",
  });
  if (values?.ids?.length) {
    const out = await attempt(() => api(`/companies/${company.id}/postings/import`, { method: "POST", body: { ats_job_ids: values.ids } }));
    toast(`${out.created.length} offre${out.created.length > 1 ? "s" : ""} ajoutée${out.created.length > 1 ? "s" : ""}`
      + (out.duplicates.length ? ` · ${out.duplicates.length} déjà connue${out.duplicates.length > 1 ? "s" : ""}` : ""));
    refreshCounts();
  }
  reload();
}

// --- Événements ---------------------------------------------------------------------------------------------

function changed() {
  order = byMatch(companies).map((c) => c.id);
  draw();
}

$("#views").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-view]");
  if (!btn) return;
  state.view = btn.dataset.view;
  const url = new URL(location.href);
  if (state.view === "pour-vous") url.searchParams.delete("vue");
  else url.searchParams.set("vue", state.view);
  history.replaceState(null, "", url);
  changed();
});
$("#q").addEventListener("input", (e) => { state.q = e.target.value; draw(); });
$("#f-secteur").addEventListener("change", (e) => { state.secteur = e.target.value; changed(); });
$("#f-stade").addEventListener("change", (e) => { state.stade = e.target.value; changed(); });
$("#f-all").addEventListener("change", (e) => { state.all = e.target.checked; changed(); });
$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; changed(); });
$("#add").addEventListener("click", () => editCompany(null));
$("#list").addEventListener("click", async (e) => {
  const cardEl = e.target.closest("[data-id]");
  if (!cardEl) return;
  const company = companies.find((c) => c.id === Number(cardEl.dataset.id));
  const job = e.target.closest("[data-job]");
  if (job) return openJobDialog(Number(job.dataset.job));
  const react = e.target.closest("[data-react]");
  if (react) {
    const value = company.reaction === react.dataset.react ? null : react.dataset.react;
    const updated = await reactToCompany(company, value);
    if (!updated) return;
    updated._v = company._v;
    companies = companies.map((c) => (c.id === updated.id ? updated : c));
    if (matches(updated)) cardEl.outerHTML = card(updated).s;
    else {
      cardEl.classList.add("leaving");
      setTimeout(() => cardEl.remove(), 180);
    }
    drawFilters();
    return;
  }
  const action = e.target.closest("[data-action]")?.dataset.action;
  if (action === "edit") editCompany(company);
  if (action === "check") checkCompany(company, e.target.closest("[data-action]"));
  if (action === "refresh-logo") {
    await attempt(() => api(`/companies/${company.id}/logo`, { method: "DELETE" }));
    await reload("Logo récupéré à nouveau");
  }
});

boot(() => reload());
