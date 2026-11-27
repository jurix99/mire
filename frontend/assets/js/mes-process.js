// Candidatures : quatre colonnes, de la préparation à la réponse. Les offres « pas intéressé » n'y sont pas
// (elles sont dans Offres > Écartées). On ajoute une candidature depuis une offre.
import {
  $, $$, STATUS_LABEL, api, applicationButton, attempt, boot, candidatureUrl, confirmDelete, daysUntil, fmtDate, html,
  isLocked, logo, manualStatuses, openForm, openJobDialog, pill, plain, proTag, refreshCounts, renderValue, toast,
} from "./app.js";

// [clé, titre, statuts, statut posé quand on y glisse une carte]
const COLUMNS = [
  ["preparer", "À préparer", ["a_postuler", "prete"], "a_postuler"],
  ["envoyees", "Envoyées", ["postule", "relance"], "postule"],
  ["entretiens", "Entretiens", ["entretien", "offre"], "entretien"],
  ["terminees", "Terminées", ["refuse", "abandonne"], "refuse"],
];
const COLUMN_COLOR = { preparer: "a_postuler", envoyees: "postule", entretiens: "entretien", terminees: "abandonne" };
let apps = [];

function nextStep(a) {
  if (!a.next_step && !a.next_step_date) return "";
  const d = daysUntil(a.next_step_date);
  const late = d != null && d < 0;
  const soon = d != null && d <= 2;
  const when = a.next_step_date ? ` · ${fmtDate(a.next_step_date)}${late ? " (en retard)" : d === 0 ? " (aujourd'hui)" : ""}` : "";
  return html`<span class="next ${soon ? "soon" : ""}">${a.next_step ?? "Étape"}${when}</span>`;
}

const card = (a) => {
  // Offre ou entreprise réservée au Pro : un seul repère sur la carte, les valeurs floutées n'ont pas de badge
  const locked = isLocked(a.company.name) || isLocked(a.job.title);
  const prepare = !locked && (["a_postuler", "prete"].includes(a.status) || a.draft_status);
  return html`
  <div class="p-card ${locked ? "pro-quiet" : ""}" role="button" tabindex="0" draggable="true" data-id="${a.id}" id="process-${a.id}">
    <span class="co">${logo(a.company, "sm")}${renderValue(a.company.name)}</span>
    <span class="small">${renderValue(a.job.title)}${locked ? html` <span data-pro style="cursor:pointer">${proTag}</span>` : ""}</span>
    <span class="p-card-meta">${pill(a.status)}${a.applied_on ? html`<span class="small muted">${fmtDate(a.applied_on)}</span>` : ""}</span>
    ${nextStep(a)}
    ${prepare ? html`<span class="p-card-action">${applicationButton({ id: a.job.id, draft_status: a.draft_status })}</span>` : ""}
  </div>`;
};

/** Où l'on en est, en une phrase (remplace les graphiques de suivi). */
function summary() {
  const reached = (a, statuses) => statuses.includes(a.status) || a.events.some((e) => statuses.includes(e.to_status));
  const sent = apps.filter((a) => reached(a, ["postule", "relance", "entretien", "offre"]));
  const interviews = sent.filter((a) => reached(a, ["entretien", "offre"]));
  const parts = [`${sent.length} envoyée${sent.length > 1 ? "s" : ""}`];
  if (interviews.length) {
    parts.push(`${interviews.length} entretien${interviews.length > 1 ? "s" : ""} obtenu${interviews.length > 1 ? "s" : ""} `
      + `(${Math.round((interviews.length / sent.length) * 100)} %)`);
  } else if (sent.length) parts.push("pas encore d'entretien");
  return parts.join(" · ");
}

function draw() {
  const shown = apps.filter((a) => a.status !== "pas_interesse");
  $("#summary").textContent = shown.length ? summary() : "";
  $("#empty").hidden = shown.length > 0;
  $("#board").hidden = !shown.length;
  $("#board").innerHTML = html`${COLUMNS.map(([key, label, statuses]) => {
    const items = shown.filter((a) => statuses.includes(a.status));
    return html`
      <section class="lane st-${COLUMN_COLOR[key]}" data-column="${key}" aria-label="${label}">
        <div class="lane-head"><h2>${label}</h2><span class="n">${items.length}</span></div>
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
    await attempt(() => api(`/applications/${id}`, { method: "PATCH", body: { status } }), `Passée en « ${STATUS_LABEL[status]} »`);
  } finally {
    await reload();
    refreshCounts();
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
        <button type="button" class="icon-btn close" data-close aria-label="Fermer">×</button></div>
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
    title: [plain(a.company.name), plain(a.job.title)].filter(Boolean).join(" · ") || "Candidature (offre réservée au Pro)",
    fields: [
      { name: "status", label: "Statut", type: "select", required: true,
        options: manualStatuses(a.status).filter(([k]) => !["nouveau", "pas_interesse"].includes(k) || k === a.status) },
      { name: "applied_on", label: "Envoyée le", type: "date" },
      { name: "next_step", label: "Prochaine étape", placeholder: "Entretien technique" },
      { name: "next_step_date", label: "Date de l'étape", type: "date" },
      { name: "notes", label: "Notes", type: "textarea", full: true },
    ],
    values: a,
    extra: html`<p class="small links">
        ${isLocked(a.job.title) ? "" : html`<button type="button" class="link-btn" data-offer="${a.job.id}">Voir l'offre</button>`}
        ${a.draft_status ? html`<a href="${candidatureUrl(a.job.id)}">Voir ma candidature préparée</a>` : ""}</p>${timeline(a)}`,
    danger: {
      label: "Retirer",
      onClick: async () => {
        if (!confirmDelete("cette candidature (l'offre reste dans vos propositions)")) return false;
        await api(`/applications/${a.id}`, { method: "DELETE" });
        return true;
      },
    },
    onSubmit: (values) => api(`/applications/${a.id}`, { method: "PATCH", body: values }),
  });
  if (result) {
    await reload();
    refreshCounts();
    toast(result.deleted ? "Candidature retirée" : "Candidature mise à jour");
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
  const id = Number(e.dataTransfer.getData("text/plain"));
  const [, , statuses, target] = COLUMNS.find(([key]) => key === lane.dataset.column);
  const app = apps.find((a) => a.id === id);
  if (app && !statuses.includes(app.status)) setStatus(id, target).catch(() => {});
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
// Liens de la fenêtre d'une candidature : l'offre, et ce qui a été envoyé
document.addEventListener("click", (e) => {
  const snapshot = e.target.closest?.("[data-snapshot]");
  if (snapshot) return openSnapshot(Number(snapshot.dataset.app), Number(snapshot.dataset.snapshot));
  const offer = e.target.closest?.("[data-offer]");
  if (offer) openJobDialog(Number(offer.dataset.offer));
});

let firstLoad = true;
boot(async () => {
  await reload();
  // Lien direct vers une candidature (#process-12), au premier affichage seulement
  // (la page est aussi rechargée après une action confirmée dans le chat)
  if (!firstLoad) return;
  firstLoad = false;
  const target = location.hash && document.getElementById(location.hash.slice(1));
  if (target) {
    target.scrollIntoView({ block: "center", inline: "center" });
    target.click();
  }
});
