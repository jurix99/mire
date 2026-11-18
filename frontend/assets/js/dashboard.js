import { $, api, attempt, boot, daysUntil, fmtDate, fmtMoney, fmtToday, html, loadMe, logo, openJobDialog, pill, refreshCounts, score, statusSelect } from "./app.js";

const companyCard =(c) => html`
  <article class="card" style="display:grid;gap:14px;align-content:start">
    <p class="label">${c.sector ?? "Secteur à préciser"}</p>
    <div style="display:flex;align-items:center;gap:12px">${logo(c, "lg")}<h2>${c.name}</h2></div>
    <p class="muted small">${c.description ?? ""}</p>
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:auto">
      ${c.funding_stage ? html`<span class="pill plain">${c.funding_stage}</span>` : html`<span></span>`}
      <strong>${fmtMoney(c.funding_amount_meur)}</strong>
    </div>
    <p class="small muted">${[c.city, c.funding_announced_on && `levée le ${fmtDate(c.funding_announced_on)}`].filter(Boolean).join(" · ")}</p>
  </article>`;

const stat = (label, value, hint, href) => html`
  <a class="card stat" href="${href}" style="text-decoration:none">
    <p class="muted small">${label}</p>
    <p class="value">${value}</p>
    <p class="small muted">${hint}</p>
  </a>`;

function nextStep(app) {
  const d = daysUntil(app.next_step_date);
  const when = app.next_step_date ? ` · ${fmtDate(app.next_step_date)}` : "";
  const soon = d != null && d <= 2;
  return html`<span class="next ${soon ? "soon" : ""}">${app.next_step ?? "Pas d'étape prévue"}${when}</span>`;
}

async function render() {
  $("#today").textContent = `// ${fmtToday()}`;
  const [me, data] = await Promise.all([loadMe(), api("/dashboard")]);
  $("#hello").textContent = `Bonjour ${me.user.given_name ?? me.user.name ?? ""}`.trim();

  const s = data.stats;
  $("#stats").innerHTML = html`
    ${stat("Offres suivies", s.jobs, "Votre sélection personnelle", "/postes")}
    ${stat("À traiter", s.todo, "De nouvelles pistes à explorer", "/postes?statut=nouveau")}
    ${stat("Candidatures en cours", s.active, s.interviews ? `Dont ${s.interviews} entretien${s.interviews > 1 ? "s" : ""} à préparer` : "Aucun entretien prévu", "/mes-process")}
    ${stat("Startups financées", s.companies, "Dans votre radar", "/entreprises")}`.s;

  const todo = $("#todo-count");
  todo.textContent = s.todo;
  todo.hidden = !s.todo;

  $("#priority").innerHTML = data.priority_jobs.length
    ? html`${data.priority_jobs.map((j) => html`
        <div class="row-item clickable" data-job-id="${j.id}">
          ${logo(j.company)}
          <div class="grow">
            <button type="button" class="link-btn title" title="Voir l'offre">${j.title}</button>
            <span class="muted small">${j.company.name}${j.location ? ` · ${j.location}` : ""}</span>
            ${j.assessment?.why ? html`<span class="small why-line">${j.assessment.why}</span>` : ""}
          </div>
          ${score(j.score)}
          ${statusSelect(j.status, `data-job="${j.id}"`)}
        </div>`)}`.s
    : html`<p class="empty">Rien à traiter, bravo.</p>`.s;

  $("#processes").innerHTML = data.processes.length
    ? html`${data.processes.map((a) => html`
        <a class="row-item" href="/mes-process#process-${a.id}" style="text-decoration:none">
          ${logo(a.company)}
          <div class="grow">
            <div class="meta"><span class="title">${a.company.name}</span>${pill(a.status)}</div>
            <span class="small">${a.job.title}</span>
            ${nextStep(a)}
          </div>
        </a>`)}`.s
    : html`<p class="empty">Aucun process en cours.</p>`.s;

  $("#fundings").innerHTML = html`${data.recent_fundings.map(companyCard)}`.s;
}

// Clic sur un poste prioritaire (hors sélecteur de statut) : fiche du poste avec le contenu de l'offre
$("main").addEventListener("click", (e) => {
  const rowEl = e.target.closest("[data-job-id]");
  if (!rowEl || e.target.closest("select")) return;
  openJobDialog(Number(rowEl.dataset.jobId));
});

// Changement de statut directement depuis le tableau de bord
// (écouteur sur <main> : il disparaît avec la page lors de la navigation sans rechargement)
$("main").addEventListener("change", async (e) => {
  const sel = e.target.closest("select[data-job]");
  if (!sel) return;
  await attempt(() => api(`/jobs/${sel.dataset.job}/status`, { method: "PUT", body: { status: sel.value } }), "Statut mis à jour")
    .catch(() => {});
  await Promise.all([render(), refreshCounts()]);
});

boot(render);
