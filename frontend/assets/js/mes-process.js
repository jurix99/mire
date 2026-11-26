import {
  $, $$, STATUSES, STATUS_LABEL, api, applicationButton, attempt, boot, candidatureUrl, confirmDelete, daysUntil, fmtDate, html,
  isLocked, logo, manualStatuses, openForm, pill, plain, proTag, refreshCounts, renderValue, toast,
} from "./app.js";

// Colonnes du tableau (le statut « nouveau » reste sur la page Postes)
const LANES = STATUSES.filter(([k]) => k !== "nouveau");
let apps = [];

function nextStep(a) {
  if (!a.next_step && !a.next_step_date) return "";
  const d = daysUntil(a.next_step_date);
  const late = d != null && d < 0;
  const soon = d != null && d <= 2;
  const when = a.next_step_date ? ` · ${fmtDate(a.next_step_date)}${late ? " (en retard)" : d === 0 ? " (aujourd'hui)" : ""}` : "";
  return html`<span class="next ${soon ? "soon" : ""}">${a.next_step ?? "Étape"}${when}</span>`;
}

// Cartes où préparer / revoir la candidature (page Candidature)
const PREPARE_LANES = new Set(["a_postuler", "prete"]);

const card = (a) => {
  // Offre ou entreprise réservée au Pro : un seul repère sur la carte, les valeurs floutées n'ont pas de badge
  const locked = isLocked(a.company.name) || isLocked(a.job.title);
  const prepare = !locked && (PREPARE_LANES.has(a.status) || a.draft_status);
  return html`
  <div class="p-card ${locked ? "pro-quiet" : ""}" role="button" tabindex="0" draggable="true" data-id="${a.id}" id="process-${a.id}">
    <span class="co">${logo(a.company, "sm")}${renderValue(a.company.name)}</span>
    <span class="small">${renderValue(a.job.title)}${locked ? html` <span data-pro style="cursor:pointer">${proTag}</span>` : ""}</span>
    ${nextStep(a)}
    ${prepare ? html`<span class="p-card-action">${applicationButton({ id: a.job.id, draft_status: a.draft_status })}</span>` : ""}
  </div>`;
};

function draw() {
  $("#board").innerHTML = html`${LANES.map(([k, label]) => {
    const items = apps.filter((a) => a.status === k);
    return html`
      <section class="lane st-${k}" data-status="${k}" aria-label="${label}">
        <div class="lane-head">${pill(k)}<span class="n">${items.length}</span></div>
        ${items.map(card)}
      </section>`;
  })}`.s;
}

async function reload() {
  apps = await api("/applications");
  draw();
}

async function setStatus(id, status) {
  const app = apps.find((a) => a.id === id);
  if (!app || app.status === status) return;
  if (status === "prete") {
    // « Prête à envoyer » se pose depuis la page Candidature, une fois les champs obligatoires remplis
    toast("Relisez la candidature, puis « Marquer prête à envoyer » sur la page Candidature.");
    if (!isLocked(app.job.title)) setTimeout(() => (location.href = candidatureUrl(app.job.id)), 900);
    return;
  }
  app.status = status; // affichage immédiat
  draw();
  try {
    await attempt(() => api(`/applications/${id}`, { method: "PATCH", body: { status } }), `Passé en « ${STATUS_LABEL[status]} »`);
  } finally {
    await reload();
  }
}

const timeline = (a) =>
  a.events.length
    ? html`<div class="timeline"><p class="label">Historique</p>
        ${a.events.slice().reverse().map((e) => html`
          <div class="ev"><span class="small muted" style="min-width:56px">${fmtDate(e.created_at)}</span>
            ${e.from_status ? html`${pill(e.from_status)} →` : ""} ${pill(e.to_status)}
            ${e.has_snapshot ? html`<button type="button" class="link-btn small" data-snapshot="${e.id}" data-app="${a.id}">Ce que j'ai envoyé</button>` : ""}</div>`)}
      </div>`
    : "";

/** Instantané de « J'ai envoyé ma candidature » : réponses, CV et lettre tels qu'envoyés (pour préparer l'entretien). */
async function openSnapshot(appId, eventId) {
  let snap;
  try {
    snap = await api(`/applications/${appId}/events/${eventId}/snapshot`);
  } catch (err) {
    return toast(err.message, true);
  }
  const value = (v) => (Array.isArray(v) ? v.join(", ") : v === true ? "Oui (case cochée sur le site)" : v ?? "");
  const dlg = document.createElement("dialog");
  dlg.className = "job-dialog";
  dlg.setAttribute("aria-label", "Ce que j'ai envoyé");
  dlg.innerHTML = html`
    <div class="dialog-body">
      <div class="job-head"><div class="grow"><h2>Ce que j'ai envoyé</h2>
        <p class="muted small">${snap.job.title} · ${snap.job.company} · le ${fmtDate(snap.sent_at)}</p></div>
        <button type="button" class="icon-btn" data-close aria-label="Fermer">×</button></div>
      <section class="job-section"><p class="label">// Formulaire</p>
        <div class="rows">${snap.answers.filter((x) => value(x.value) !== "").map((x) => html`
          <div class="row-item"><span class="muted small" style="min-width:160px">${x.label}</span><span class="grow prewrap small">${value(x.value)}</span></div>`)}
        </div></section>
      ${snap.cv_text ? html`<section class="job-section"><p class="label">// CV envoyé</p><div class="job-text prewrap small">${snap.cv_text}</div></section>` : ""}
      ${snap.cover_letter ? html`<section class="job-section"><p class="label">// Lettre</p><div class="job-text prewrap small">${snap.cover_letter}</div></section>` : ""}
    </div>
    <div class="dialog-foot"><div class="right"><button type="button" class="btn" data-close>Fermer</button></div></div>`.s;
  document.body.append(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  dlg.addEventListener("click", (e) => { if (e.target === dlg || e.target.closest("[data-close]")) dlg.close(); });
  dlg.showModal();
}

async function editApp(a) {
  const result = await openForm({
    title: [plain(a.company.name), plain(a.job.title)].filter(Boolean).join(" · ") || "Process (offre réservée au Pro)",
    fields: [
      { name: "status", label: "Statut", type: "select", options: manualStatuses(a.status), required: true },
      { name: "applied_on", label: "Candidature envoyée le", type: "date" },
      { name: "next_step", label: "Prochaine étape", placeholder: "Entretien technique", full: true },
      { name: "next_step_date", label: "Date de l'étape", type: "date" },
      { name: "notes", label: "Notes", type: "textarea", full: true },
    ],
    values: a,
    extra: html`${a.draft_status ? html`<p class="small"><a href="${candidatureUrl(a.job.id)}">Voir ma candidature préparée</a></p>` : ""}${timeline(a)}`,
    danger: {
      label: "Supprimer",
      onClick: async () => {
        if (!confirmDelete("ce process (le poste repassera en « Nouveau »)")) return false;
        await api(`/applications/${a.id}`, { method: "DELETE" });
        return true;
      },
    },
    onSubmit: (values) => api(`/applications/${a.id}`, { method: "PATCH", body: values }),
  });
  if (result) {
    await reload();
    refreshCounts();
    toast(result.deleted ? "Process supprimé" : "Process mis à jour");
  }
}

async function addApp() {
  const jobs = await api("/jobs");
  const free = jobs.filter((j) => j.status === "nouveau" && !j.locked);
  if (!free.length) return toast("Tous les postes ont déjà un process. Ajoutez un poste depuis la page Postes.", true);
  const result = await openForm({
    title: "Nouveau process",
    fields: [
      { name: "job_id", label: "Poste", type: "select", required: true, full: true,
        options: free.map((j) => [j.id, `${j.company.name} · ${j.title}`]) },
      { name: "status", label: "Statut", type: "select", options: LANES.filter(([k]) => k !== "prete"), required: true },
      { name: "applied_on", label: "Candidature envoyée le", type: "date" },
      { name: "next_step", label: "Prochaine étape", full: true },
      { name: "next_step_date", label: "Date de l'étape", type: "date" },
    ],
    values: { status: "a_postuler" },
    submitLabel: "Créer",
    onSubmit: async ({ job_id, ...values }) => {
      // Le poste peut déjà avoir un process « nouveau » (créé depuis la page Postes) : on le met à jour
      const job = free.find((j) => j.id === Number(job_id));
      if (job.application_id) return api(`/applications/${job.application_id}`, { method: "PATCH", body: values });
      return api("/applications", { method: "POST", body: { job_id: Number(job_id), ...values } });
    },
  });
  if (result) {
    await reload();
    refreshCounts();
    toast("Process créé");
  }
}

// --- Glisser-déposer entre colonnes ---------------------------------------

const board = $("#board");
board.addEventListener("dragstart", (e) => {
  const c = e.target.closest(".p-card");
  if (!c) return;
  e.dataTransfer.setData("text/plain", c.dataset.id);
  e.dataTransfer.effectAllowed = "move";
  c.classList.add("dragging");
});
board.addEventListener("dragend", () => {
  $$(".dragging, .lane.drop", board).forEach((el) => el.classList.remove("dragging", "drop"));
});
board.addEventListener("dragover", (e) => {
  const lane = e.target.closest(".lane");
  if (!lane) return;
  e.preventDefault();
  $$(".lane.drop", board).forEach((l) => l !== lane && l.classList.remove("drop"));
  lane.classList.add("drop");
});
board.addEventListener("drop", (e) => {
  const lane = e.target.closest(".lane");
  if (!lane) return;
  e.preventDefault();
  lane.classList.remove("drop");
  setStatus(Number(e.dataTransfer.getData("text/plain")), lane.dataset.status).catch(() => {});
});
board.addEventListener("click", (e) => {
  if (e.target.closest("[data-prepare], a")) return; // bouton de la carte : page Candidature
  const c = e.target.closest(".p-card");
  if (c) editApp(apps.find((a) => a.id === Number(c.dataset.id)));
});
board.addEventListener("keydown", (e) => {
  const c = e.target.closest?.(".p-card");
  if (!c || e.target !== c || (e.key !== "Enter" && e.key !== " ")) return;
  e.preventDefault();
  editApp(apps.find((a) => a.id === Number(c.dataset.id)));
});
// Historique d'un process (fenêtre ouverte par editApp) : instantané de ce qui a été envoyé
document.addEventListener("click", (e) => {
  const btn = e.target.closest?.("[data-snapshot]");
  if (btn) openSnapshot(Number(btn.dataset.app), Number(btn.dataset.snapshot));
});

$("#add").addEventListener("click", addApp);

let firstLoad = true;
boot(async () => {
  await reload();
  // Lien direct vers un process depuis le tableau de bord (#process-12), au premier affichage seulement
  // (la page est aussi rechargée après une action confirmée dans le chat)
  if (!firstLoad) return;
  firstLoad = false;
  const target = location.hash && document.getElementById(location.hash.slice(1));
  if (target) {
    target.scrollIntoView({ block: "center", inline: "center" });
    target.click();
  }
});
