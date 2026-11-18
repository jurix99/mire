import { $, ICONS, STATUSES, api, attempt, boot, confirmDelete, html, logo, openForm, openJobDialog, refreshCounts, score, statusSelect, toast } from "./app.js";

let jobs = [];
let companies = [];
const params = new URLSearchParams(location.search);
let statusFilter = params.get("statut") ?? "";

function fields() {
  return [
    { name: "title", label: "Intitulé", required: true, full: true },
    { name: "company_id", label: "Entreprise", type: "select", required: true, options: companies.map((c) => [c.id, c.name]) },
    { name: "score", label: "Score (0 à 10)", type: "number", step: "0.1", min: 0, max: 10 },
    { name: "location", label: "Lieu", placeholder: "Paris · Hybride" },
    { name: "category", label: "Catégorie", placeholder: "Platform / MLOps" },
    { name: "seniority", label: "Expérience", placeholder: "5 ans" },
    { name: "source", label: "Source", placeholder: "Welcome to the Jungle" },
    { name: "url", label: "Lien de l'offre", type: "url", full: true },
    { name: "stack", label: "Stack", full: true, placeholder: "Python, Kubernetes…" },
    { name: "summary", label: "Description de l'offre", type: "textarea", full: true, maxlength: 20000,
      hint: "Collez le texte de l'annonce : il s'affiche dans la fiche du poste et sert à l'assistant." },
  ];
}

async function editJob(job) {
  if (!companies.length) return toast("Ajoutez d'abord une entreprise", true);
  const result = await openForm({
    title: job ? "Modifier le poste" : "Nouveau poste",
    fields: fields(),
    values: job ? { ...job, company_id: job.company.id } : { company_id: params.get("entreprise") ?? "" },
    submitLabel: job ? "Enregistrer" : "Ajouter",
    danger: job && {
      label: "Supprimer",
      onClick: async () => {
        if (!confirmDelete(`le poste « ${job.title} » (et le process associé)`)) return false;
        await api(`/jobs/${job.id}`, { method: "DELETE" });
        return true;
      },
    },
    onSubmit: (values) => {
      const body = { ...values, company_id: Number(values.company_id) };
      return job ? api(`/jobs/${job.id}`, { method: "PATCH", body }) : api("/jobs", { method: "POST", body });
    },
  });
  if (result) {
    await reload();
    refreshCounts();
    toast(result.deleted ? "Poste supprimé" : "Poste enregistré");
  }
}

const row = (j) => html`
  <div class="job-row clickable" data-id="${j.id}">
    ${logo(j.company)}
    <div style="min-width:0;display:grid;gap:2px">
      <button type="button" class="link-btn title" data-action="view" title="Voir l'offre">${j.title}</button>
      <span class="small muted">${[j.location, j.source].filter(Boolean).join(" · ")}</span>
    </div>
    <span class="col-company">${j.company.name}</span>
    <span class="col-score">${score(j.score)}</span>
    <span class="col-status">${statusSelect(j.status, `data-job="${j.id}"`)}</span>
    <span class="actions">
      ${j.url ? html`<a class="icon-btn" href="${j.url}" target="_blank" rel="noopener" title="Voir l'offre">${ICONS.link}</a>` : ""}
      <button class="icon-btn" type="button" data-action="edit" title="Modifier">${ICONS.edit}</button>
    </span>
  </div>`;

function drawFilters() {
  const counts = Object.fromEntries(STATUSES.map(([k]) => [k, 0]));
  jobs.forEach((j) => counts[j.status]++);
  $("#filters").innerHTML = html`
    <button type="button" class="chip all" data-status="" aria-pressed="${!statusFilter}">Tous <span class="n">${jobs.length}</span></button>
    ${STATUSES.filter(([k]) => counts[k] || k === statusFilter).map(([k, l]) => html`
      <button type="button" class="chip st-${k}" data-status="${k}" aria-pressed="${statusFilter === k}">
        <span class="dot"></span>${l} <span class="n">${counts[k]}</span>
      </button>`)}`.s;
}

function draw() {
  drawFilters();
  const q = $("#search").value.trim().toLowerCase();
  const companyId = $("#company").value;
  const shown = jobs.filter(
    (j) =>
      (!statusFilter || j.status === statusFilter) &&
      (!companyId || String(j.company.id) === companyId) &&
      (!q || [j.title, j.company.name, j.stack, j.category, j.location].some((v) => v?.toLowerCase().includes(q)))
  );
  $("#list").innerHTML = shown.length ? html`${shown.map(row)}`.s : html`<p class="empty">Aucun poste ne correspond.</p>`.s;
}

async function reload() {
  [jobs, companies] = await Promise.all([api("/jobs"), api("/companies")]);
  const current = $("#company").value || params.get("entreprise") || "";
  $("#company").innerHTML = html`<option value="">Toutes les entreprises</option>
    ${companies.map((c) => html`<option value="${c.id}" ${String(c.id) === current ? "selected" : ""}>${c.name}</option>`)}`.s;
  draw();
}

$("#search").addEventListener("input", draw);
$("#company").addEventListener("change", draw);
$("#add").addEventListener("click", () => editJob(null));
$("#filters").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-status]");
  if (!chip) return;
  statusFilter = chip.dataset.status;
  const url = new URL(location.href);
  if (statusFilter) url.searchParams.set("statut", statusFilter);
  else url.searchParams.delete("statut");
  history.replaceState(null, "", url);
  draw();
});
$("#list").addEventListener("click", (e) => {
  const rowEl = e.target.closest("[data-id]");
  if (!rowEl) return;
  const job = jobs.find((j) => j.id === Number(rowEl.dataset.id));
  if (e.target.closest("[data-action=edit]")) return editJob(job);
  // Clic n'importe où sur la ligne (hors statut, lien et boutons) : fiche du poste avec le contenu de l'offre
  if (e.target.closest("select, a, button:not([data-action=view])")) return;
  openJobDialog(job.id, { onEdit: () => editJob(job) });
});
$("#list").addEventListener("change", async (e) => {
  const sel = e.target.closest("select[data-job]");
  if (!sel) return;
  const updated = await attempt(
    () => api(`/jobs/${sel.dataset.job}/status`, { method: "PUT", body: { status: sel.value } }),
    "Statut mis à jour"
  ).catch(() => null);
  if (updated) jobs = jobs.map((j) => (j.id === updated.id ? updated : j));
  draw();
  refreshCounts();
});

boot(reload);
