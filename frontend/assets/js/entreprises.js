import { $, ICONS, api, attempt, boot, confirmDelete, fmtDate, fmtMoney, html, logo, openForm, refreshCounts, toast } from "./app.js";

let companies = [];

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
  { name: "logo_url", label: "URL du logo (facultatif)", type: "url", full: true, hint: "Sinon le favicon du site est utilisé" },
  { name: "logo_file", label: "Ou importer un logo", type: "file", accept: "image/png,image/jpeg,image/gif,image/webp,image/svg+xml,image/x-icon", full: true, hint: "PNG, JPEG, SVG, WebP… 512 Ko max" },
];

async function uploadLogo(id, file) {
  const form = new FormData();
  form.append("file", file);
  await api(`/companies/${id}/logo`, { method: "PUT", form });
}

async function editCompany(company) {
  const result = await openForm({
    title: company ? `Modifier ${company.name}` : "Nouvelle entreprise",
    fields: FIELDS,
    values: company ?? {},
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

const card = (c) => html`
  <article class="card" style="display:grid;gap:14px;align-content:start" data-id="${c.id}">
    <div style="display:flex;align-items:center;gap:14px">
      ${logo(c, "lg")}
      <div style="flex:1;min-width:0">
        <h2>${c.name}</h2>
        <p class="small muted">${c.sector ?? "Secteur à préciser"}${c.city ? ` · ${c.city}` : ""}</p>
      </div>
      <button class="icon-btn" type="button" data-action="edit" title="Modifier">${ICONS.edit}</button>
    </div>
    <p class="small">${c.description ?? ""}</p>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      ${c.funding_stage ? html`<span class="pill plain">${c.funding_stage}</span>` : ""}
      ${c.funding_amount_meur != null ? html`<strong>${fmtMoney(c.funding_amount_meur)}</strong>` : ""}
      ${c.funding_announced_on ? html`<span class="small muted">· ${fmtDate(c.funding_announced_on)}</span>` : ""}
    </div>
    ${c.investors ? html`<p class="small muted">${c.investors}</p>` : ""}
    <div class="card-foot" style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:auto">
      <a class="link-more" href="/postes?entreprise=${c.id}">${c.jobs_count} poste${c.jobs_count > 1 ? "s" : ""}</a>
      <span style="display:flex;gap:2px">
        <button class="icon-btn" type="button" data-action="refresh-logo" title="Récupérer à nouveau le logo">${ICONS.image}</button>
        ${c.website ? html`<a class="icon-btn" href="${c.website}" target="_blank" rel="noopener" title="Site web">${ICONS.link}</a>` : ""}
      </span>
    </div>
  </article>`;

function draw() {
  const q = $("#search").value.trim().toLowerCase();
  const sector = $("#sector").value;
  const shown = companies.filter(
    (c) =>
      (!sector || c.sector === sector) &&
      (!q || [c.name, c.sector, c.description, c.investors].some((v) => v?.toLowerCase().includes(q)))
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
  if (message) {
    refreshCounts();
    toast(message);
  }
}

$("#search").addEventListener("input", draw);
$("#sector").addEventListener("change", draw);
$("#add").addEventListener("click", () => editCompany(null));
$("#list").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const company = companies.find((c) => c.id === Number(btn.closest("[data-id]").dataset.id));
  if (btn.dataset.action === "edit") editCompany(company);
  if (btn.dataset.action === "refresh-logo") {
    await attempt(() => api(`/companies/${company.id}/logo`, { method: "DELETE" }));
    await reload("Logo récupéré à nouveau");
  }
});

boot(() => reload());
