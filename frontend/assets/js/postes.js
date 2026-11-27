// Offres : la page principale. Les offres arrivent déjà classées pour l'utilisateur (ranking.py) ; ici, trois
// vues (Pour vous, J'aime, Écartées), des filtres et les actions rapides (J'aime, Pas intéressé, Préparer).
import {
  $, $$, api, applicationButton, attempt, boot, confirmDelete, fmtAgo, html, isLocked, jobFacts, loadMe, lockedBlock,
  logo, matchBadge, openForm, openJobDialog, plain, proBar, reactToJob, reactionButtons, reasonList, refreshCounts,
  renderPaged, renderValue, shown, toast,
} from "./app.js";

const VIEWS = ["pour-vous", "aimees", "ecartees"];
// Offres déjà dans les candidatures : elles sortent de « Pour vous »
const IN_PIPELINE = new Set(["a_postuler", "prete", "postule", "relance", "entretien", "offre", "refuse", "abandonne"]);
const FILTERS = ["q", "metier", "lieu", "mode", "niveau", "date", "salaire", "entreprise"];

const params = new URLSearchParams(location.search);
const state = {
  view: VIEWS.includes(params.get("vue")) ? params.get("vue") : "pour-vous",
  sort: params.get("tri") === "date" ? "date" : "match",
  ...Object.fromEntries(FILTERS.map((k) => [k, params.get(k) ?? ""])),
};
let jobs = []; // dans l'ordre du serveur (pertinence)
let order = []; // ordre affiché : il ne bouge pas sous le curseur après un retour (bouton « Reclasser »)
let companies = [];
let options = { families: [], seniority: [] };
let profile = null;
let pollTimer;

const jobDate = (j) => j.published_on ?? j.created_at.slice(0, 10);
/** Écartée par l'utilisateur lui-même (et non par une exclusion automatique). */
const ownDislike = (j) => j.feedback === "dislike" || j.status === "pas_interesse";

// --- Vues et filtres ----------------------------------------------------------------------------------

const inView = {
  "pour-vous": (j) => !j.hidden_reason && !IN_PIPELINE.has(j.status),
  aimees: (j) => j.feedback === "like",
  ecartees: (j) => !!j.hidden_reason && !j.closed_at,
};

function matches(j) {
  if (!inView[state.view](j)) return false;
  // Offre floutée (Free) : rien de réel à filtrer, elle n'apparaît que sans filtre
  if (j.locked) return FILTERS.every((k) => !state[k]);
  if (state.entreprise && String(j.company.id) !== state.entreprise) return false;
  if (state.metier && j.role_family !== state.metier) return false;
  if (state.lieu && j.city !== state.lieu) return false;
  if (state.mode && j.work_mode !== state.mode) return false;
  if (state.niveau && j.seniority_level !== state.niveau) return false;
  if (state.date && (Date.now() - new Date(jobDate(j))) / 86400000 > Number(state.date)) return false;
  if (state.salaire) {
    const offered = j.salary_max_keur ?? j.salary_min_keur;
    if (state.salaire === "affiche" ? !offered && !plain(j.salary) : !(offered >= Number(state.salaire))) return false;
  }
  const q = state.q.trim().toLowerCase();
  return !q || [j.title, j.company.name, j.stack, j.category, j.location, j.role_type]
    .some((v) => plain(v).toLowerCase().includes(q));
}

function visibleJobs() {
  const byId = new Map(jobs.map((j) => [j.id, j]));
  const list = order.map((id) => byId.get(id)).filter(Boolean).filter(matches);
  if (state.sort === "date") list.sort((a, b) => jobDate(b).localeCompare(jobDate(a)));
  // Écartées : mes propres « Pas intéressé » d'abord, pour pouvoir revenir dessus
  return state.view === "ecartees" ? list.sort((a, b) => ownDislike(b) - ownDislike(a)) : list;
}

/** Choix d'une liste déroulante : seulement les valeurs présentes dans la vue, avec leur nombre. */
function choices(select, all, entries, current) {
  const shownEntries = entries.filter(([k, , n]) => n || k === current);
  $(select).innerHTML = html`<option value="">${all}</option>${shownEntries.map(([k, label, n]) => html`
    <option value="${k}" ${k === current ? "selected" : ""}>${label}${n != null ? ` (${n})` : ""}</option>`)}`.s;
  $(select).classList.toggle("is-set", !!current);
}

function drawFilters() {
  const pool = jobs.filter((j) => !j.locked && inView[state.view](j));
  const count = (field) => pool.reduce((acc, j) => (j[field] ? acc.set(j[field], (acc.get(j[field]) ?? 0) + 1) : acc), new Map());
  const families = count("role_family");
  choices("#f-metier", "Tous les métiers",
    options.families.map((f) => [f.key, f.label, families.get(f.key) ?? 0]), state.metier);
  const cities = [...count("city")].sort((a, b) => b[1] - a[1]).slice(0, 30);
  if (state.lieu && !cities.some(([c]) => c === state.lieu)) cities.push([state.lieu, 0]);
  choices("#f-lieu", "Tous les lieux", cities.map(([c, n]) => [c, c, n]), state.lieu);
  const modes = count("work_mode");
  choices("#f-mode", "Remote ou sur site",
    [["remote", "Remote"], ["hybride", "Hybride"], ["sur_site", "Sur site"]].map(([k, l]) => [k, l, modes.get(k) ?? 0]),
    state.mode);
  const levels = count("seniority_level");
  choices("#f-niveau", "Tous les niveaux",
    options.seniority.map((s) => [s.key, s.label, levels.get(s.key) ?? 0]), state.niveau);
  choices("#f-date", "Toute date", [["7", "Moins de 7 jours"], ["30", "Moins de 30 jours"]], state.date);
  choices("#f-salaire", "Salaire indifférent", [["affiche", "Salaire affiché"], ["50", "50 k€ et plus"],
    ["60", "60 k€ et plus"], ["70", "70 k€ et plus"], ["80", "80 k€ et plus"]], state.salaire);
  const company = companies.find((c) => String(c.id) === state.entreprise);
  $("#f-entreprise").innerHTML = state.entreprise
    ? html`<button type="button" class="chip is-set" data-clear="entreprise">${company ? plain(company.name) : "Entreprise"} ×</button>`.s
    : "";
  $("#f-reset").hidden = FILTERS.every((k) => !state[k]);
  for (const view of VIEWS) {
    const btn = $(`[data-view="${view}"]`);
    btn.setAttribute("aria-selected", String(view === state.view));
    $(".n", btn).textContent = jobs.filter(inView[view]).length;
  }
}

function syncUrl() {
  const url = new URL(location.href);
  const set = (k, v) => (v ? url.searchParams.set(k, v) : url.searchParams.delete(k));
  set("vue", state.view === "pour-vous" ? "" : state.view);
  set("tri", state.sort === "date" ? "date" : "");
  FILTERS.forEach((k) => set(k, state[k]));
  history.replaceState(null, "", url);
}

// --- Cartes -------------------------------------------------------------------------------------------

const lockedCard = (j) => html`
  <div class="job-card locked-card" data-id="${j.id}">
    ${lockedBlock(html`<div class="job-card-body">
      <span class="logo is-locked" data-initials="?"></span>
      <div class="job-card-main">
        <span class="title">${shown(j.title)}</span>
        <span class="small muted">${shown(j.company.name)} · ${fmtAgo(j.published_on ?? j.created_at)}</span>
      </div>
      <span class="match mid">75</span>
    </div>`)}
  </div>`;

function card(j) {
  if (j.locked) return lockedCard(j);
  const excluded = state.view === "ecartees";
  const why = j.assessment?.why && !isLocked(j.assessment.why) ? j.assessment.why : "";
  return html`
  <article class="job-card ${excluded ? "is-excluded" : ""}" data-id="${j.id}">
    <div class="job-card-body">
      ${logo(j.company)}
      <div class="job-card-main">
        <button type="button" class="link-btn title" data-open>${j.title}</button>
        <span class="small muted"><strong>${renderValue(j.company.name)}</strong> · ${jobFacts(j)}</span>
        ${excluded ? html`<span class="small exclusion-note">${j.hidden_reason}</span>`
          : html`${reasonList(j.match)}${why ? html`<span class="small why-line">${why}</span>` : ""}`}
      </div>
      ${excluded ? "" : matchBadge(j.match)}
    </div>
    <div class="job-card-actions">
      ${!excluded ? reactionButtons(j.feedback)
        : ownDislike(j) ? html`<button type="button" class="btn ghost sm" data-restore>Remettre dans les propositions</button>`
        // Entreprise écartée ou exclue : c'est elle qu'il faut rétablir (un J'aime sur l'offre n'y suffit pas)
        : j.hidden_reason.startsWith("Entreprise") ? html`<a class="btn ghost sm" href="/entreprises?vue=ecartees">Revoir les entreprises écartées</a>`
        : html`<button type="button" class="btn ghost sm" data-react="like" title="Ajoutée à vos J'aime, elle revient dans vos propositions">Proposer quand même</button>`}
      <span class="spacer"></span>
      <button type="button" class="link-btn small muted" data-edit data-admin>Modifier</button>
      ${IN_PIPELINE.has(j.status) ? html`<a class="small" href="/mes-process">Dans vos candidatures</a>` : ""}
      ${excluded ? "" : applicationButton(j)}
    </div>
  </article>`;
}

const EMPTY = {
  "pour-vous": "Aucune offre ne correspond. Élargissez les filtres ou votre recherche dans « Profil ».",
  aimees: "Rien pour l'instant : cliquez sur le cœur d'une offre, elle se range ici et remonte les offres qui lui ressemblent.",
  ecartees: "Aucune offre écartée.",
};

function drawCount(list = visibleJobs()) {
  $("#count").textContent = `${list.length} offre${list.length > 1 ? "s" : ""}`;
}

/** keep : rafraîchissement sans revenir en haut de la liste. */
function draw({ keep = false } = {}) {
  drawFilters();
  const list = visibleJobs();
  drawCount(list);
  renderPaged($("#list"), list, card, { keep, page: 30, empty: html`<p class="empty card">${EMPTY[state.view]}</p>` });
}

/** Nouvel ordre du serveur appliqué : au chargement, ou à la demande. */
function applyOrder() {
  order = jobs.map((j) => j.id);
  $("#rerank").hidden = true;
}

// --- Données ------------------------------------------------------------------------------------------

async function loadJobs({ keepOrder = false } = {}) {
  jobs = await api("/jobs?brief=true");
  if (!keepOrder || !order.length) return applyOrder();
  const known = new Set(order);
  order = [...order, ...jobs.filter((j) => !known.has(j.id)).map((j) => j.id)];
  // Le classement des premières offres proposées a-t-il changé ?
  const top = (ids) => {
    const byId = new Map(jobs.map((j) => [j.id, j]));
    return ids.filter((id) => byId.has(id) && matches(byId.get(id))).slice(0, 15).join();
  };
  $("#rerank").hidden = state.sort !== "match" || top(order) === top(jobs.map((j) => j.id));
}

async function reload() {
  const first = !order.length;
  const tasks = [loadJobs({ keepOrder: !first })];
  if (first) {
    tasks.push(api("/options").then((o) => (options = o)), api("/companies").then((c) => (companies = c)));
  }
  tasks.push(api("/profile").then((p) => (profile = p)));
  await Promise.all(tasks);
  $("#q").value = state.q;
  $("#sort").value = state.sort;
  draw({ keep: !first });
  drawSetup();
  drawAssessmentStatus();
  drawProBanner().catch(() => {});
}

/** Profil trop vide pour un bon classement : ce qui manque, en une ligne. */
function drawSetup() {
  const p = profile.preferences;
  const missing = [!p.roles.length && "vos métiers visés", profile.skills.length < 3 && "vos compétences",
    !p.locations.length && "vos lieux"].filter(Boolean);
  $("#setup").innerHTML = missing.length
    ? html`<div class="notice setup"><span><strong>Le classement manque d'informations.</strong>
        Ajoutez ${missing.join(", ")} : c'est ce qui compte le plus.</span>
        <a class="btn sm" href="/mon-profil">Compléter ma recherche</a></div>`.s
    : "";
}

/** Analyse par Gemini en cours : une ligne discrète, la liste se met à jour toute seule. */
async function drawAssessmentStatus() {
  clearTimeout(pollTimer);
  let st;
  try {
    st = await api("/assessments/status");
  } catch {
    return;
  }
  const bar = $("#assess");
  if (!bar) return; // page quittée
  bar.innerHTML = st.enabled && st.profile_ready && st.pending
    ? html`<span>Analyse par l'IA en cours : ${st.pending} offre${st.pending > 1 ? "s" : ""} restante${st.pending > 1 ? "s" : ""}.</span>
        <button type="button" class="link-btn" data-run>Accélérer</button>
        ${st.last_error ? html`<span class="exclusion-note">Dernière erreur : ${st.last_error}</span>` : ""}`.s
    : "";
  if (st.enabled && st.profile_ready && st.pending) {
    pollTimer = setTimeout(async () => {
      if (!document.body.contains(bar)) return;
      await loadJobs({ keepOrder: true });
      draw({ keep: true });
      drawAssessmentStatus();
    }, 15000);
  }
}

/** Offre Free : nombre d'offres floutées (calculé par l'API). */
async function drawProBanner() {
  const b = (await loadMe()).billing;
  const n = b?.enabled && b.plan === "free" ? b.locked_jobs : 0;
  const visible = b?.limits?.visible_jobs;
  $("#pro-banner").innerHTML = n
    ? proBar(`En Free, vous voyez en entier ${visible} offres bien notées pour vous. ${n > 1 ? `Les ${n} autres` : "L'autre"}, `
      + `dont votre meilleure offre, ${n > 1 ? "sont réservées" : "est réservée"} au Pro`).s
    : "";
}

// --- Ajout et modification (administrateurs) ----------------------------------------------------------

function fields() {
  return [
    { name: "title", label: "Intitulé", required: true, full: true },
    { name: "company_id", label: "Entreprise", type: "select", required: true,
      options: companies.filter((c) => !c.locked).map((c) => [c.id, c.name]).sort((a, b) => a[1].localeCompare(b[1], "fr")) },
    { name: "url", label: "Lien de l'offre", type: "url" },
    { name: "location", label: "Lieu", placeholder: "Paris" },
    { name: "work_mode", label: "Mode de travail", type: "select",
      options: [["", "Non précisé"], ["sur_site", "Sur site"], ["hybride", "Hybride"], ["remote", "Remote"]] },
    { name: "salary", label: "Salaire affiché", placeholder: "70-90 k€" },
    { name: "published_on", label: "Date de publication", type: "date" },
    { name: "summary", label: "Description de l'offre", type: "textarea", full: true, maxlength: 20000,
      hint: "Collez le texte de l'annonce : il sert au classement et à la candidature." },
  ];
}

async function editJob(job) {
  if (!companies.length) return toast("Ajoutez d'abord une entreprise", true);
  const current = job ? await api(`/jobs/${job.id}`) : null; // avec la description
  const result = await openForm({
    title: job ? "Modifier l'offre" : "Nouvelle offre",
    fields: fields(),
    values: current ? { ...current, company_id: current.company.id } : { company_id: state.entreprise },
    submitLabel: job ? "Enregistrer" : "Ajouter",
    danger: job && {
      label: "Supprimer",
      onClick: async () => {
        if (!confirmDelete(`l'offre « ${current.title} » (et la candidature associée)`)) return false;
        await api(`/jobs/${job.id}`, { method: "DELETE" });
        return true;
      },
    },
    onSubmit: (values) => {
      const body = { ...values, company_id: Number(values.company_id) };
      return job ? api(`/jobs/${job.id}`, { method: "PATCH", body }) : api("/jobs", { method: "POST", body });
    },
  });
  if (!result) return;
  order = [];
  await reload();
  refreshCounts();
  toast(result.deleted ? "Offre supprimée" : "Offre enregistrée");
}

// --- Événements ---------------------------------------------------------------------------------------

function changed() {
  syncUrl();
  applyOrder(); // un changement de vue ou de filtre applique le classement à jour
  draw();
}

$("#views").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-view]");
  if (!btn) return;
  state.view = btn.dataset.view;
  changed();
});
$("#q").addEventListener("input", (e) => { state.q = e.target.value; syncUrl(); draw(); });
$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; changed(); });
$$("#filters select[data-filter]").forEach((sel) => sel.addEventListener("change", () => {
  state[sel.dataset.filter] = sel.value;
  changed();
}));
$("#filters").addEventListener("click", (e) => {
  const clear = e.target.closest("[data-clear]");
  if (clear) state[clear.dataset.clear] = "";
  else if (e.target.closest("#f-reset")) FILTERS.forEach((k) => (state[k] = ""));
  else return;
  $("#q").value = state.q;
  changed();
});
$("#rerank").addEventListener("click", () => { applyOrder(); draw(); window.scrollTo({ top: 0, behavior: "smooth" }); });
$("#add").addEventListener("click", () => editJob(null));
$("#assess").addEventListener("click", async (e) => {
  const run = e.target.closest("[data-run]");
  if (!run) return;
  run.disabled = true;
  run.textContent = "Analyse en cours…";
  await attempt(() => api("/assessments/run", { method: "POST" })).catch(() => {});
  await loadJobs({ keepOrder: true });
  draw({ keep: true });
  drawAssessmentStatus();
});

$("#list").addEventListener("click", async (e) => {
  const cardEl = e.target.closest("[data-id]");
  if (!cardEl) return;
  const job = jobs.find((j) => j.id === Number(cardEl.dataset.id));
  if (e.target.closest("[data-edit]")) return editJob(job);
  const react = e.target.closest("[data-react]");
  if (react || e.target.closest("[data-restore]")) {
    const value = !react || job.feedback === react.dataset.react ? null : react.dataset.react;
    const updated = await reactToJob(job, value);
    if (!updated) return;
    jobs = jobs.map((j) => (j.id === updated.id ? { ...updated, summary: null } : j));
    // La carte change tout de suite (ou quitte la vue) ; le reste de la liste ne bouge pas
    if (matches(jobs.find((j) => j.id === updated.id))) cardEl.outerHTML = card(updated).s;
    else {
      cardEl.classList.add("leaving");
      setTimeout(() => cardEl.remove(), 180);
    }
    await loadJobs({ keepOrder: true });
    drawFilters();
    drawCount();
    refreshCounts();
    return;
  }
  if (e.target.closest("a, button:not([data-open])")) return;
  openJobDialog(job.id, { onEdit: () => editJob(job) });
});

boot(reload);
