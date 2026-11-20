import {
  $, ICONS, api, attempt, boot, confirmDelete, fmtDate, fmtMoney, html, isLocked, loadMe, logo, openForm, plain, proBar, proTag,
  refreshCounts, renderValue, toast,
} from "./app.js";

let companies = [];

const ATS_OPTIONS = [["ashby", "Ashby"], ["lever", "Lever"], ["greenhouse", "Greenhouse"], ["recruitee", "Recruitee"],
  ["teamtailor", "Teamtailor"], ["yc", "YC (Work at a Startup)"], ["autre", "Autre"]];
const AUTO_ATS = new Set(["ashby", "lever", "greenhouse", "recruitee"]);

const FIELDS = [
  { name: "name", label: "Nom", required: true },
  { name: "website", label: "Site web", type: "url", placeholder: "https://…", hint: "Sert aussi à trouver le logo automatiquement" },
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
        if (!confirmDelete(`${company.name} et ses ${company.jobs_count} poste(s)`)) return false;
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

const atsLabel = (c) => `${Object.fromEntries(ATS_OPTIONS)[c.ats] ?? c.ats}${c.ats_slug && !isLocked(c.ats_slug) ? ` (${c.ats_slug})` : ""}`;

const card = (c) => {
  // Repère « Pro » unique par carte : valeurs floutées à l'intérieur sans badge propre
  const hasPro = c.locked || [c.funding_stage, c.funding_amount_meur, c.investors, c.description, c.ats].some(isLocked);
  return html`
  <article class="card ${hasPro ? "pro-quiet" : ""}" style="display:grid;gap:14px;align-content:start" data-id="${c.id}">
    <div style="display:flex;align-items:center;gap:14px">
      ${logo(c, "lg")}
      <div style="flex:1;min-width:0">
        <h2>${renderValue(c.name)}</h2>
        <p class="small muted">${c.sector ?? "Secteur à préciser"}${c.city ? html` · ${renderValue(c.city)}` : ""}</p>
      </div>
      ${c.locked ? "" : html`<button class="icon-btn" type="button" data-action="edit" data-admin title="Modifier">${ICONS.edit}</button>`}
    </div>
    <p class="small">${renderValue(c.description, { block: true })}</p>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      ${hasPro ? html`<span data-pro title="Levée réservée à l'offre Pro" style="cursor:pointer">${proTag}</span>` : ""}
      ${c.funding_stage ? html`<span class="pill plain">${renderValue(c.funding_stage)}</span>` : ""}
      ${c.funding_amount_meur != null ? html`<strong>${renderValue(c.funding_amount_meur, { fmt: fmtMoney })}</strong>` : ""}
      ${c.funding_announced_on ? html`<span class="small muted">${renderValue(c.funding_announced_on, { fmt: fmtDate })}</span>` : ""}
    </div>
    ${c.investors ? html`<p class="small muted">${renderValue(c.investors)}</p>` : ""}
    ${c.ats || c.last_checked_at ? html`<p class="small muted">
      ${c.ats ? html`ATS : ${isLocked(c.ats) ? renderValue(c.ats) : atsLabel(c)}` : ""}
      ${c.last_checked_at ? html` · vérifiée le ${renderValue(c.last_checked_at, { fmt: fmtDate })}` : ""}
      ${c.no_relevant_jobs_at && !isLocked(c.no_relevant_jobs_at) ? " · sans offre pertinente" : ""}</p>` : ""}
    <div class="card-foot" style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:auto">
      ${isLocked(c.jobs_count) ? html`<span class="small muted">${renderValue(c.jobs_count)} postes récents</span>`
        : html`<a class="link-more" href="/postes?entreprise=${c.id}">${c.jobs_count} poste${c.jobs_count > 1 ? "s" : ""}</a>`}
      <span style="display:flex;gap:2px">
        ${AUTO_ATS.has(c.ats) && c.ats_slug ? html`<button class="btn ghost sm" type="button" data-action="check" data-admin
          title="Vérifier les offres ouvertes dans la liste de l'ATS">Vérifier les offres</button>` : ""}
        ${c.locked ? "" : html`<button class="icon-btn" type="button" data-action="refresh-logo" data-admin title="Récupérer à nouveau le logo">${ICONS.image}</button>`}
        ${c.website && !isLocked(c.website) ? html`<a class="icon-btn" href="${c.website}" target="_blank" rel="noopener" title="Site web">${ICONS.link}</a>` : ""}
      </span>
    </div>
  </article>`;
};

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
  const old = (p) => p.published_on && (Date.now() - new Date(p.published_on)) / 86400000 > 61;
  const fmt = (p) => [p.title, p.location, p.published_on ? `publiée le ${fmtDate(p.published_on)}` : "",
    old(p) ? "ancienne (plus de 2 mois) : ne sera pas ajoutée" : ""].filter(Boolean).join(" · ");
  const values = await openForm({
    title: `${company.name} : nouvelles offres`,
    fields: [{ name: "ids", label: `${res.new_postings.length} offre${res.new_postings.length > 1 ? "s" : ""} pas encore dans Mire`,
               type: "checks", full: true, options: res.new_postings.map((p) => [p.ats_job_id, fmt(p)]),
               hint: "Les offres ajoutées sont vérifiées ouvertes, puis notées pour chaque utilisateur." }],
    extra: html`<p class="small muted">${summary}</p>`,
    submitLabel: "Ajouter au radar",
  });
  if (values?.ids?.length) {
    const out = await attempt(() => api(`/companies/${company.id}/postings/import`, { method: "POST", body: { ats_job_ids: values.ids } }));
    toast(`${out.created.length} offre${out.created.length > 1 ? "s" : ""} ajoutée${out.created.length > 1 ? "s" : ""}`
      + (out.duplicates.length ? ` · ${out.duplicates.length} déjà connue${out.duplicates.length > 1 ? "s" : ""}` : "")
      + (out.too_old.length ? ` · ${out.too_old.length} trop ancienne${out.too_old.length > 1 ? "s" : ""}` : ""));
    refreshCounts();
  }
  reload();
}

function draw() {
  const q = $("#search").value.trim().toLowerCase();
  const sector = $("#sector").value;
  const shown = companies.filter(
    (c) =>
      (!sector || c.sector === sector) &&
      (!q || [c.name, c.sector, c.description, c.investors].some((v) => plain(v).toLowerCase().includes(q)))
  );
  $("#list").innerHTML = shown.length
    ? html`${shown.map(card)}`.s
    : html`<p class="empty card">Aucune entreprise ne correspond.</p>`.s;
}

async function reload(message) {
  companies = await api("/companies");
  const v = Date.now(); // force le rechargement des logos modifiés
  companies.forEach((c) => (c._v = v));
  const sectors = [...new Set(companies.map((c) => c.sector).filter(Boolean))].sort();
  const current = $("#sector").value;
  $("#sector").innerHTML = html`<option value="">Tous les secteurs</option>${sectors.map((s) => html`<option ${s === current ? "selected" : ""}>${s}</option>`)}`.s;
  draw();
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

$("#search").addEventListener("input", draw);
$("#sector").addEventListener("change", draw);
$("#add").addEventListener("click", () => editCompany(null));
$("#list").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const company = companies.find((c) => c.id === Number(btn.closest("[data-id]").dataset.id));
  if (btn.dataset.action === "edit") editCompany(company);
  if (btn.dataset.action === "check") checkCompany(company, btn);
  if (btn.dataset.action === "refresh-logo") {
    await attempt(() => api(`/companies/${company.id}/logo`, { method: "DELETE" }));
    await reload("Logo récupéré à nouveau");
  }
});

boot(() => reload());
