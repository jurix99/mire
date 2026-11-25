import { $, ICONS, api, attempt, boot, fmtDate, html, loadMe, openForm, toast } from "./app.js";

const KINDS = [
  ["ats", "ATS (logiciels de recrutement)"],
  ["job_board", "Job boards"],
  ["vc_board", "Job boards de fonds VC"],
  ["funding_radar", "Radars de levées de fonds"],
  ["recruiter", "Cabinets de recrutement"],
  ["google_query", "Requêtes Google"],
  ["autre", "Autres"],
];
const KIND_LABEL = Object.fromEntries(KINDS);
const RUN_KIND = {
  collecte: "Collecte des offres", decouverte: "Découverte des entreprises", planifie: "Planifié", manuel: "Manuel",
  approfondi: "Approfondi", offres: "Offres hors ATS", recherche: "Recherche par les offres",
};
const RUN_STATUS = { en_cours: "En cours", termine: "Terminé", echec: "Échec" };

let sources = [];
let kindFilter = "";

const isUrl = (q) => /^https?:\/\//i.test(q ?? "") && !q.includes("<");

const fields = () => [
  { name: "name", label: "Nom", required: true, maxlength: 200 },
  { name: "kind", label: "Type", type: "select", required: true, options: KINDS },
  { name: "query", label: "Où chercher (URL ou requête)", full: true, maxlength: 2000,
    placeholder: "https://… ou site:jobs.ashbyhq.com \"platform engineer\" Paris" },
  { name: "auto_readable", label: "Lecture automatique", type: "select", options: [["true", "Oui"], ["false", "Non (à faire à la main)"]] },
  { name: "notes", label: "Notes (mémoire de l'agent : entreprises vues sans offre pertinente, pistes…)", type: "textarea", full: true, maxlength: 5000 },
];

async function editSource(source) {
  const values = await openForm({
    title: source ? `Modifier ${source.name}` : "Nouvelle source",
    fields: fields(),
    values: source ? { ...source, auto_readable: String(source.auto_readable) } : { kind: kindFilter || "job_board", auto_readable: "true" },
    submitLabel: source ? "Enregistrer" : "Ajouter",
    onSubmit: (v) => {
      const body = { ...v, auto_readable: v.auto_readable !== "false" };
      return source ? api(`/sources/${source.id}`, { method: "PATCH", body }) : api("/sources", { method: "POST", body });
    },
  });
  if (values) {
    toast(source ? "Source enregistrée" : "Source ajoutée");
    await reload();
  }
}

const row = (s) => html`
  <div class="row-item source ${s.active ? "" : "inactive"}" data-id="${s.id}">
    <div class="grow">
      <span class="title">${s.name}
        ${s.added_by === "agent" ? html`<span class="pill plain" title="Ajoutée par l'agent de recherche">agent</span>` : ""}
        ${!s.auto_readable ? html`<span class="pill plain" title="Lecture automatique impossible">à la main</span>` : ""}</span>
      ${s.query ? (isUrl(s.query)
        ? html`<a class="small source-query" href="${s.query}" target="_blank" rel="noopener noreferrer">${s.query}</a>`
        : html`<code class="small source-query">${s.query}</code>`) : html`<span class="small muted">URL à compléter (l'agent peut le faire)</span>`}
      ${s.notes ? html`<span class="small muted clamp">${s.notes}</span>` : ""}
    </div>
    <span class="small muted source-stats">${s.jobs_found} offre${s.jobs_found > 1 ? "s" : ""}${s.last_used_at ? html`<br>lue le ${fmtDate(s.last_used_at)}` : ""}</span>
    <label class="switch" data-admin title="${s.active ? "Désactiver (l'agent ne l'utilisera plus)" : "Réactiver"}">
      <input type="checkbox" data-toggle ${s.active ? "checked" : ""} aria-label="Source active"><span></span>
    </label>
    <button class="icon-btn" type="button" data-edit data-admin title="Modifier">${ICONS.edit}</button>
  </div>`;

function draw() {
  const counts = Object.fromEntries(KINDS.map(([k]) => [k, sources.filter((s) => s.kind === k).length]));
  $("#kinds").innerHTML = html`
    <button type="button" class="chip all" data-kind="" aria-pressed="${!kindFilter}">Toutes <span class="n">${sources.length}</span></button>
    ${KINDS.filter(([k]) => counts[k]).map(([k, l]) => html`
      <button type="button" class="chip" data-kind="${k}" aria-pressed="${kindFilter === k}">${l} <span class="n">${counts[k]}</span></button>`)}`.s;
  const groups = KINDS.filter(([k]) => counts[k] && (!kindFilter || k === kindFilter));
  $("#groups").innerHTML = groups.length ? html`${groups.map(([k, l]) => html`
    <section class="card source-group">
      <p class="label">// ${l}</p>
      <div class="rows">${sources.filter((s) => s.kind === k).map(row)}</div>
    </section>`)}`.s : html`<p class="empty card">Aucune source pour l'instant.</p>`.s;
}

function drawRuns({ schedule, runs }) {
  $("#schedule").innerHTML = html`<span>${schedule ?? "Aucun passage planifié"}</span>
    <button type="button" class="btn ghost sm" data-collect data-admin title="Lire maintenant les offres de toutes les entreprises (ATS)">Collecter maintenant</button>`.s;
  $("#runs").innerHTML = runs.length ? html`<div class="rows">${runs.map((r) => html`
    <div class="row-item run">
      <div class="grow">
        <span class="title">${fmtDate(r.started_at)} · ${RUN_KIND[r.kind] ?? r.kind}
          <span class="pill plain">${RUN_STATUS[r.status] ?? r.status}</span></span>
        <span class="small muted">${r.jobs_seen} vues · ${r.jobs_added} ajoutées · ${r.jobs_duplicates} déjà connues ·
          ${r.jobs_closed} fermées · ${r.companies_checked} entreprises vérifiées · ${r.sources_added} sources ajoutées</span>
        ${r.mine ? html`<span class="small">Pour vous : <strong>${r.mine.nouvelles}</strong> nouvelle${r.mine.nouvelles > 1 ? "s" : ""} offre${r.mine.nouvelles > 1 ? "s" : ""} retenue${r.mine.nouvelles > 1 ? "s" : ""}${r.mine.top?.length ? ` : ${r.mine.top.map((t) => `${t.entreprise} – ${t.titre}${t.note != null ? ` (${t.note})` : ""}`).join(", ")}` : ""}</span>` : ""}
        ${r.summary ? html`<span class="small muted prewrap clamp">${r.summary}</span>` : ""}
      </div>
    </div>`)}</div>`.s : html`<p class="empty">Aucun passage pour l'instant.</p>`.s;
}

async function reload() {
  const [list, runs] = await Promise.all([api("/sources"), api("/runs")]);
  sources = list;
  draw();
  drawRuns(runs);
}

$("#add").addEventListener("click", () => editSource(null));
$("main").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-collect]");
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = "Collecte en cours…";
  try {
    await attempt(() => api("/collect/run", { method: "POST" }), "Collecte lancée : les nouvelles offres seront ensuite évaluées pour vous");
    // Suivi jusqu'à la fin de la collecte
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 5000));
      if (!document.body.contains(btn)) return;
      const st = await api("/collect/status");
      if (!st.running) break;
    }
    await reload();
  } catch {
    btn.disabled = false;
    btn.textContent = "Collecter maintenant";
  }
});
$("#kinds").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-kind]");
  if (!chip) return;
  kindFilter = chip.dataset.kind;
  draw();
});
$("#groups").addEventListener("click", (e) => {
  if (!e.target.closest("[data-edit]")) return;
  editSource(sources.find((s) => s.id === Number(e.target.closest("[data-id]").dataset.id)));
});
$("#groups").addEventListener("change", async (e) => {
  const t = e.target.closest("[data-toggle]");
  if (!t) return;
  const id = Number(t.closest("[data-id]").dataset.id);
  await attempt(() => api(`/sources/${id}`, { method: "PATCH", body: { active: t.checked } }),
    t.checked ? "Source réactivée" : "Source désactivée : l'agent ne l'utilisera plus").catch(() => {});
  await reload();
});

// Page réservée aux admins (l'API refuse aussi : 403) : retour au tableau de bord
boot(async () => {
  const me = await loadMe();
  if (me.user.is_admin === false) return location.replace("/tableau-de-bord");
  await reload();
});
