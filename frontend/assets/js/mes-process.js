import { $, $$, STATUSES, STATUS_LABEL, api, attempt, boot, confirmDelete, daysUntil, fmtDate, html, logo, openForm, pill, refreshCounts, toast } from "./app.js";

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

const card = (a) => html`
  <button type="button" class="p-card" draggable="true" data-id="${a.id}" id="process-${a.id}">
    <span class="co">${logo(a.company, "sm")}${a.company.name}</span>
    <span class="small">${a.job.title}</span>
    ${nextStep(a)}
  </button>`;

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
            ${e.from_status ? html`${pill(e.from_status)} →` : ""} ${pill(e.to_status)}</div>`)}
      </div>`
    : "";

async function editApp(a) {
  const result = await openForm({
    title: `${a.company.name} · ${a.job.title}`,
    fields: [
      { name: "status", label: "Statut", type: "select", options: STATUSES, required: true },
      { name: "applied_on", label: "Candidature envoyée le", type: "date" },
      { name: "next_step", label: "Prochaine étape", placeholder: "Entretien technique", full: true },
      { name: "next_step_date", label: "Date de l'étape", type: "date" },
      { name: "notes", label: "Notes", type: "textarea", full: true },
    ],
    values: a,
    extra: timeline(a),
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
  const free = jobs.filter((j) => j.status === "nouveau");
  if (!free.length) return toast("Tous les postes ont déjà un process. Ajoutez un poste depuis la page Postes.", true);
  const result = await openForm({
    title: "Nouveau process",
    fields: [
      { name: "job_id", label: "Poste", type: "select", required: true, full: true,
        options: free.map((j) => [j.id, `${j.company.name} · ${j.title}`]) },
      { name: "status", label: "Statut", type: "select", options: LANES, required: true },
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
  const c = e.target.closest(".p-card");
  if (c) editApp(apps.find((a) => a.id === Number(c.dataset.id)));
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
