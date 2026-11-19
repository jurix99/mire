import {
  $, AUTO_LABEL, ICONS, STATUSES, api, attempt, boot, confirmDelete, fmtAgo, html, isLocked, loadMe, logo, openFeedback, openForm,
  openJobDialog, plain, proBar, proTag, refreshCounts, renderValue, score, statusSelect, toast,
} from "./app.js";

let jobs = [];
let companies = [];
const params = new URLSearchParams(location.search);
let statusFilter = params.get("statut") ?? "";
let pollTimer;

function fields() {
  return [
    { name: "title", label: "Intitulé", required: true, full: true },
    { name: "company_id", label: "Entreprise", type: "select", required: true,
      options: companies.filter((c) => !c.locked).map((c) => [c.id, c.name]) },
    { name: "location", label: "Lieu", placeholder: "Paris · Hybride · peu de déplacements" },
    { name: "category", label: "Catégorie", placeholder: "Platform / MLOps" },
    { name: "seniority", label: "Expérience demandée", placeholder: "5+ ans" },
    { name: "role_type", label: "Type de rôle", placeholder: "Platform, founding, FDE…" },
    { name: "work_mode", label: "Mode de travail", type: "select",
      options: [["", "Non précisé"], ["sur_site", "Sur site"], ["hybride", "Hybride"], ["remote", "Remote"]] },
    { name: "travel", label: "Déplacements", placeholder: "Peu, 1 semaine par mois…" },
    { name: "salary", label: "Salaire affiché", placeholder: "70-90 k€" },
    { name: "published_on", label: "Date de publication", type: "date" },
    { name: "source", label: "Source", placeholder: "Welcome to the Jungle" },
    { name: "url", label: "Lien de l'offre", type: "url", full: true },
    { name: "stack", label: "Stack", full: true, placeholder: "Python, Kubernetes…" },
    { name: "summary", label: "Description de l'offre", type: "textarea", full: true, maxlength: 20000,
      hint: "Collez le texte de l'annonce : il s'affiche dans la fiche du poste et sert à l'évaluation." },
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

/** Ligne sous le titre : pourquoi c'est intéressant, ou raison d'écart. */
function subline(j) {
  if (j.locked) return html`<span class="small muted">Publiée ${fmtAgo(j.published_on ?? j.created_at)} · ${proTag}</span>`;
  const a = j.assessment;
  if (a?.exclusion && j.display_status === "ecartee") return html`<span class="small muted exclusion-line">${renderValue(a.exclusion)}</span>`;
  // « Pourquoi » verrouillé (Free) : flouté dans la fiche, la ligne garde le lieu et la source
  if (a?.why && !isLocked(a.why)) return html`<span class="small why-line">${a.why}</span>`;
  return html`<span class="small muted">${[j.location, j.source].filter(Boolean).join(" · ")}</span>`;
}

const row = (j) => html`
  <div class="job-row clickable ${j.display_status === "ecartee" ? "is-excluded" : ""} ${j.locked ? "is-locked pro-quiet" : ""}" data-id="${j.id}">
    ${logo(j.company)}
    <div style="min-width:0;display:grid;gap:2px">
      ${isLocked(j.title) ? renderValue(j.title)
        : html`<button type="button" class="link-btn title" data-action="view" title="Voir l'offre">${j.title}</button>`}
      ${subline(j)}
    </div>
    <span class="col-company">${renderValue(j.company.name)}</span>
    <span class="col-score" title="${j.assessment?.manual_score != null ? "Note corrigée par vous" : "Note calculée selon votre profil"}">${score(j.score)}</span>
    <span class="col-status">${statusSelect(j.display_status, `data-job="${j.id}"`)}</span>
    <span class="actions">
      ${j.locked ? "" : html`
      <button class="icon-btn fb-like" type="button" data-action="like" title="J'aime (offre de référence)">${ICONS.heart}</button>
      <button class="icon-btn fb-dislike" type="button" data-action="dislike" title="Pas intéressé">${ICONS.thumbDown}</button>`}
      ${j.url && !isLocked(j.url) ? html`<a class="icon-btn" href="${j.url}" target="_blank" rel="noopener" title="Voir l'offre originale">${ICONS.link}</a>` : ""}
      ${j.locked ? "" : html`<button class="icon-btn" type="button" data-action="edit" title="Modifier">${ICONS.edit}</button>`}
    </span>
  </div>`;

function drawFilters() {
  const counts = {};
  jobs.forEach((j) => (counts[j.display_status] = (counts[j.display_status] ?? 0) + 1));
  const visible = jobs.length - (counts.ecartee ?? 0);
  const chips = [...STATUSES, ...Object.entries(AUTO_LABEL)];
  $("#filters").innerHTML = html`
    <button type="button" class="chip all" data-status="" aria-pressed="${!statusFilter}"
      title="Toutes les offres, sauf celles écartées automatiquement">Tous <span class="n">${visible}</span></button>
    ${chips.filter(([k]) => counts[k] || k === statusFilter).map(([k, l]) => html`
      <button type="button" class="chip st-${k}" data-status="${k}" aria-pressed="${statusFilter === k}">
        <span class="dot"></span>${l} <span class="n">${counts[k] ?? 0}</span>
      </button>`)}`.s;
}

function draw() {
  drawFilters();
  const q = $("#search").value.trim().toLowerCase();
  const companyId = $("#company").value;
  const shown = jobs.filter(
    (j) =>
      (statusFilter ? j.display_status === statusFilter : j.display_status !== "ecartee") &&
      (!companyId || String(j.company.id) === companyId) &&
      (!q || [j.title, j.company.name, j.stack, j.category, j.location].some((v) => plain(v).toLowerCase().includes(q)))
  );
  $("#list").innerHTML = shown.length ? html`${shown.map(row)}`.s : html`<p class="empty">Aucun poste ne correspond.</p>`.s;
}

// --- Bandeau d'évaluation ---------------------------------------------------------

function statusBar() {
  let bar = $("#assess-bar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "assess-bar";
    bar.className = "assess-bar small";
    const list = $("#list");
    (list.closest(".card") ?? list).before(bar); // au-dessus du tableau
  }
  return bar;
}

async function drawAssessmentStatus() {
  clearTimeout(pollTimer);
  let st;
  try {
    st = await api("/assessments/status");
  } catch {
    return;
  }
  const bar = statusBar();
  if (!st.enabled) {
    bar.innerHTML = html`<span class="muted">Évaluation automatique désactivée : GEMINI_API_KEY n'est pas configurée.</span>`.s;
    return;
  }
  if (!st.profile_ready) {
    bar.innerHTML = html`<span>Remplissez <a href="/mon-profil">votre profil</a> pour que les offres soient notées selon vos critères.</span>`.s;
    return;
  }
  bar.innerHTML = html`
    <span>${st.pending
      ? `${st.pending} offre${st.pending > 1 ? "s" : ""} en attente d'évaluation…`
      : `Offres évaluées selon votre profil : ${st.kept} retenue${st.kept > 1 ? "s" : ""}, ${st.excluded} écartée${st.excluded > 1 ? "s" : ""}.`}</span>
    ${st.pending ? html`<button type="button" class="btn ghost sm" data-run>Évaluer maintenant</button>` : ""}
    ${st.last_error ? html`<span class="exclusion-line">Dernière erreur : ${st.last_error}</span>` : ""}`.s;
  // Tant qu'il reste des offres à évaluer (tâche de fond), on rafraîchit régulièrement
  if (st.pending) {
    pollTimer = setTimeout(async () => {
      if (!document.body.contains(bar)) return; // page quittée
      const before = st.pending;
      await drawAssessmentStatus();
      if (before) await reloadJobs();
    }, 8000);
  }
}

async function reloadJobs() {
  jobs = await api("/jobs");
  draw();
}

/** Offre Free : nombre d'offres récentes floutées (calculé par l'API). */
async function drawProBanner() {
  const b = (await loadMe()).billing;
  const n = b?.enabled && b.plan === "free" ? b.locked_jobs : 0;
  const days = b?.limits?.fresh_delay_days;
  $("#pro-banner").innerHTML = n
    ? proBar(`${n} offre${n > 1 ? "s" : ""} de moins de ${days} jours ${n > 1 ? "sont réservées" : "est réservée"} au Pro`).s
    : "";
}

async function reload() {
  [jobs, companies] = await Promise.all([api("/jobs"), api("/companies")]);
  const current = $("#company").value || params.get("entreprise") || "";
  $("#company").innerHTML = html`<option value="">Toutes les entreprises</option>
    ${companies.filter((c) => !c.locked).map((c) => html`<option value="${c.id}" ${String(c.id) === current ? "selected" : ""}>${c.name}</option>`)}`.s;
  draw();
  drawAssessmentStatus();
  drawProBanner().catch(() => {});
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
$("main").addEventListener("click", async (e) => {
  const run = e.target.closest("#assess-bar [data-run]");
  if (!run) return;
  run.disabled = true;
  run.textContent = "Évaluation en cours…";
  await attempt(() => api("/assessments/run", { method: "POST" })).catch(() => {});
  await reloadJobs();
  drawAssessmentStatus();
});
$("#list").addEventListener("click", (e) => {
  const rowEl = e.target.closest("[data-id]");
  if (!rowEl) return;
  const job = jobs.find((j) => j.id === Number(rowEl.dataset.id));
  if (e.target.closest("[data-action=edit]")) return editJob(job);
  const fb = e.target.closest("[data-action=like], [data-action=dislike]");
  if (fb) return openFeedback(job, fb.dataset.action).catch(() => {});
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
