import { $, $$, ICONS, STATUSES, api, attempt, boot, fmtToday, html, loadMe, openChatPanel, openForm, pill, toast } from "./app.js";

const HELP = {
  nouveau: "Poste repéré, pas encore étudié",
  a_postuler: "Poste retenu, à regarder de près",
  postule: "Candidature envoyée",
  relance: "Pas de réponse : relance à faire ou faite",
  entretien: "Un ou plusieurs entretiens en cours",
  offre: "Proposition reçue",
  refuse: "Candidature refusée",
  pas_interesse: "Écarté par vous (la raison aide l'assistant à affiner vos règles)",
  abandonne: "Process arrêté de votre côté",
};

const WORK_MODES = [["remote", "Remote"], ["hybride", "Hybride"], ["sur_site", "Sur site"]];
const WORK_MODE_LABEL = Object.fromEntries(WORK_MODES);
const SKILL_KINDS = [["fort", "Point fort"], ["a_developper", "À développer"]];
const RULE_KINDS = [["eviter", "À éviter"], ["preferer", "À préférer"]];

const memberSince = new Intl.DateTimeFormat("fr-FR", { month: "long", year: "numeric" });
const monthYear = new Intl.DateTimeFormat("fr-FR", { month: "short", year: "numeric" });

let profile;
let gemini = { enabled: false };

// --- Helpers ----------------------------------------------------------------

const splitList = (v) => (v ?? "").split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
const joinList = (a) => (a ?? []).join(", ");

/** "2021-03" -> "mars 2021", "2021" -> "2021", texte libre inchangé. */
function fmtYm(v) {
  if (!v) return "";
  const m = /^(\d{4})-(\d{2})$/.exec(v);
  return m ? monthYear.format(new Date(Number(m[1]), Number(m[2]) - 1, 1)) : v;
}
const period = (item) =>
  item.start || item.end ? `${fmtYm(item.start) || "?"} – ${item.end ? fmtYm(item.end) : "aujourd'hui"}` : "";

const head = (label, title, action = "") => html`
  <div class="section-head">
    <div class="intro"><p class="label">${label}</p><h2>${title}</h2></div>${action}
  </div>`;
const editBtn = (attr, label) => html`<button type="button" class="icon-btn" data-${attr} aria-label="${label}" title="${label}">${ICONS.edit}</button>`;
const emptyRow = (text) => html`<p class="empty">${text}</p>`;

async function save(patch, message = "Profil enregistré") {
  profile = await attempt(() => api("/profile", { method: "PATCH", body: patch }), message);
  renderProfile();
}

// --- Identité professionnelle ---------------------------------------------------

function renderPro() {
  const p = profile;
  const rows = [
    ["Titre", p.headline],
    ["Localisation", p.location],
    ["Expérience", p.years_experience != null ? `${p.years_experience} an${p.years_experience >= 2 ? "s" : ""}` : null],
    ["Langues", p.languages.map((l) => (l.level ? `${l.name} (${l.level})` : l.name)).join(", ")],
  ];
  $("#pro").innerHTML = html`
    ${head("// Identité professionnelle", "En bref", editBtn("edit-pro", "Modifier l'identité professionnelle"))}
    <div class="rows">${rows.map(([k, v]) => html`
      <div class="row-item"><span class="muted small" style="min-width:110px">${k}</span><span class="grow">${v || html`<span class="muted">—</span>`}</span></div>`)}
    </div>
    ${p.summary ? html`<p class="small prewrap" style="margin-top:12px">${p.summary}</p>` : ""}`.s;

  $("#pro").onclick = (e) => {
    if (!e.target.closest("[data-edit-pro]")) return;
    openForm({
      title: "Identité professionnelle",
      values: {
        ...p,
        languages: p.languages.map((l) => (l.level ? `${l.name} : ${l.level}` : l.name)).join("\n"),
      },
      fields: [
        { name: "headline", label: "Titre", full: true, maxlength: 300, placeholder: "Ex. Développeur backend senior" },
        { name: "location", label: "Localisation", maxlength: 200 },
        { name: "years_experience", label: "Années d'expérience", type: "number", min: 0, max: 60, step: 0.5 },
        { name: "languages", label: "Langues", type: "textarea", full: true, hint: "Une par ligne, ex. « Anglais : courant »" },
        { name: "summary", label: "Résumé", type: "textarea", full: true, maxlength: 5000 },
      ],
      onSubmit: (v) => save({
        headline: v.headline,
        location: v.location,
        years_experience: v.years_experience,
        summary: v.summary,
        languages: (v.languages ?? "").split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
          const [name, ...level] = line.split(/\s*:\s*/);
          return { name, level: level.join(" : ") || null };
        }),
      }),
    });
  };
}

// --- Préférences ------------------------------------------------------------

function renderPrefs() {
  const p = profile.preferences;
  const rows = [
    ["Rôles visés", joinList(p.roles)],
    ["Lieux", joinList(p.locations)],
    ["Mode de travail", p.work_modes.map((m) => WORK_MODE_LABEL[m]).join(", ")],
    ["Stades", joinList(p.company_stages)],
    ["Salaire min.", p.min_salary_keur ? `${p.min_salary_keur} k€ brut / an` : ""],
    ["Clouds préférés", joinList(p.clouds_preferred)],
    ["Clouds à éviter", joinList(p.clouds_avoided)],
    ["Secteurs", joinList(p.sectors)],
  ];
  $("#prefs").innerHTML = html`
    ${head("// Recherche", "Préférences", editBtn("edit-prefs", "Modifier les préférences"))}
    <div class="rows">${rows.map(([k, v]) => html`
      <div class="row-item"><span class="muted small" style="min-width:120px">${k}</span><span class="grow">${v || html`<span class="muted">—</span>`}</span></div>`)}
    </div>`.s;

  $("#prefs").onclick = (e) => {
    if (!e.target.closest("[data-edit-prefs]")) return;
    const hint = "Séparés par des virgules";
    openForm({
      title: "Préférences de recherche",
      values: {
        ...Object.fromEntries(Object.entries(p).map(([k, v]) => [k, Array.isArray(v) && k !== "work_modes" ? joinList(v) : v])),
      },
      fields: [
        { name: "roles", label: "Rôles visés", full: true, hint, placeholder: "Backend, Platform engineer" },
        { name: "locations", label: "Lieux", hint, placeholder: "Paris, Lyon" },
        { name: "min_salary_keur", label: "Salaire minimum (k€ brut / an)", type: "number", min: 0, max: 1000 },
        { name: "work_modes", label: "Mode de travail", type: "checks", options: WORK_MODES, full: true },
        { name: "company_stages", label: "Stades d'entreprise", full: true, hint, placeholder: "Seed, Série A, Série B" },
        { name: "clouds_preferred", label: "Clouds préférés", hint, placeholder: "GCP, AWS" },
        { name: "clouds_avoided", label: "Clouds à éviter", hint },
        { name: "sectors", label: "Secteurs", full: true, hint, placeholder: "IA, santé, climat" },
      ],
      onSubmit: (v) => save({
        preferences: {
          roles: splitList(v.roles), locations: splitList(v.locations), work_modes: v.work_modes,
          company_stages: splitList(v.company_stages), min_salary_keur: v.min_salary_keur,
          clouds_preferred: splitList(v.clouds_preferred), clouds_avoided: splitList(v.clouds_avoided),
          sectors: splitList(v.sectors),
        },
      }),
    });
  };
}

// --- Compétences ------------------------------------------------------------

function renderSkills() {
  const skills = profile.skills;
  const group = (kind, title) => {
    const items = skills.map((s, i) => [s, i]).filter(([s]) => s.kind === kind);
    return html`<div class="skill-group">
      <p class="small muted">${title}</p>
      <div class="tags">${items.length ? items.map(([s, i]) => html`
        <span class="tag removable ${kind === "fort" ? "strong" : ""}">
          <button type="button" class="tag-main" data-toggle-skill="${i}" title="Passer en « ${kind === "fort" ? "à développer" : "point fort"} »">${s.name}${s.level ? ` · ${s.level}` : ""}</button>
          <button type="button" class="tag-x" data-rm-skill="${i}" aria-label="Retirer ${s.name}">×</button>
        </span>`) : html`<span class="small muted">Aucune</span>`}</div>
    </div>`;
  };
  $("#skills").innerHTML = html`
    ${head("// Compétences", "Ce que je sais faire")}
    <div style="display:grid;gap:16px">
      ${group("fort", "Points forts")}
      ${group("a_developper", "À développer")}
      <form class="inline-add" data-add-skill>
        <input class="input" name="name" placeholder="Ajouter une compétence" maxlength="120" required aria-label="Compétence">
        <select class="input" name="kind" aria-label="Type">${SKILL_KINDS.map(([v, l]) => html`<option value="${v}">${l}</option>`)}</select>
        <button class="btn sm">Ajouter</button>
      </form>
      <p class="small muted">Cliquez sur une compétence pour la basculer entre « point fort » et « à développer ».</p>
    </div>`.s;

  const box = $("#skills");
  box.onclick = (e) => {
    const rm = e.target.closest("[data-rm-skill]");
    const toggle = e.target.closest("[data-toggle-skill]");
    if (rm) save({ skills: skills.filter((_, i) => i !== Number(rm.dataset.rmSkill)) }, "Compétence retirée");
    if (toggle) {
      const i = Number(toggle.dataset.toggleSkill);
      save({ skills: skills.map((s, j) => (j === i ? { ...s, kind: s.kind === "fort" ? "a_developper" : "fort" } : s)) });
    }
  };
  box.onsubmit = (e) => {
    e.preventDefault();
    const form = e.target;
    const name = form.elements.name.value.trim();
    if (!name) return;
    if (skills.some((s) => s.name.toLowerCase() === name.toLowerCase())) return toast("Compétence déjà présente", true);
    save({ skills: [...skills, { name, kind: form.elements.kind.value }] }, "Compétence ajoutée");
  };
}

// --- Règles -----------------------------------------------------------------

function renderRules() {
  const rules = profile.rules;
  $("#rules").innerHTML = html`
    ${head("// Règles", "À éviter, à préférer")}
    <div class="rows">${rules.length ? rules.map((r, i) => html`
      <div class="row-item">
        <span class="pill ${r.kind === "eviter" ? "st-refuse" : "st-offre"}">${r.kind === "eviter" ? "Éviter" : "Préférer"}</span>
        <span class="grow">${r.text}</span>
        <button type="button" class="icon-btn" data-rm-rule="${i}" aria-label="Supprimer la règle">${ICONS.trash}</button>
      </div>`) : emptyRow("Aucune règle. Exemple : « Écarter les postes où TypeScript est au cœur du poste ».")}
    </div>
    <form class="inline-add" data-add-rule style="margin-top:12px">
      <select class="input" name="kind" aria-label="Type de règle">${RULE_KINDS.map(([v, l]) => html`<option value="${v}">${l}</option>`)}</select>
      <input class="input" name="text" placeholder="Ex. postes avec astreintes" maxlength="300" required aria-label="Règle">
      <button class="btn sm">Ajouter</button>
    </form>`.s;

  const box = $("#rules");
  box.onclick = (e) => {
    const rm = e.target.closest("[data-rm-rule]");
    if (rm) save({ rules: rules.filter((_, i) => i !== Number(rm.dataset.rmRule)) }, "Règle supprimée");
  };
  box.onsubmit = (e) => {
    e.preventDefault();
    const text = e.target.elements.text.value.trim();
    if (text) save({ rules: [...rules, { kind: e.target.elements.kind.value, text }] }, "Règle ajoutée");
  };
}

// --- Expériences et formation -------------------------------------------------

const LIST_SECTIONS = {
  experiences: {
    label: "// Parcours",
    title: "Expériences",
    empty: "Aucune expérience. Importez votre profil LinkedIn ou ajoutez-en une.",
    line: (x) => [x.title, x.company].filter(Boolean).join(" · "),
    meta: (x) => [period(x), x.location].filter(Boolean).join(" · "),
    desc: (x) => x.description,
    fields: [
      { name: "title", label: "Intitulé", maxlength: 200, required: true },
      { name: "company", label: "Entreprise", maxlength: 200 },
      { name: "start", label: "Début", maxlength: 30, placeholder: "2021-03" },
      { name: "end", label: "Fin", maxlength: 30, placeholder: "Vide si poste actuel" },
      { name: "location", label: "Lieu", maxlength: 200, full: true },
      { name: "description", label: "Description", type: "textarea", full: true, maxlength: 5000 },
    ],
  },
  education: {
    label: "// Formation",
    title: "Diplômes",
    empty: "Aucune formation renseignée.",
    line: (x) => [x.degree, x.school].filter(Boolean).join(" · "),
    meta: (x) => [x.field, period(x)].filter(Boolean).join(" · "),
    desc: () => "",
    fields: [
      { name: "school", label: "École", maxlength: 200, required: true },
      { name: "degree", label: "Diplôme", maxlength: 200 },
      { name: "field", label: "Domaine", maxlength: 200, full: true },
      { name: "start", label: "Début", maxlength: 30, placeholder: "2014" },
      { name: "end", label: "Fin", maxlength: 30, placeholder: "2019" },
    ],
  },
};

function renderList(key) {
  const conf = LIST_SECTIONS[key];
  const items = profile[key];
  const addBtn = html`<button type="button" class="btn ghost sm" data-add>Ajouter</button>`;
  $(`#${key}`).innerHTML = html`
    ${head(conf.label, conf.title, addBtn)}
    <div class="rows">${items.length ? items.map((x, i) => html`
      <div class="row-item" style="align-items:flex-start">
        <div class="grow">
          <span class="title">${conf.line(x) || "—"}</span>
          ${conf.meta(x) ? html`<span class="small muted">${conf.meta(x)}</span>` : ""}
          ${conf.desc(x) ? html`<span class="small muted clamp">${conf.desc(x)}</span>` : ""}
        </div>
        <button type="button" class="icon-btn" data-edit="${i}" aria-label="Modifier">${ICONS.edit}</button>
      </div>`) : emptyRow(conf.empty)}
    </div>`.s;

  $(`#${key}`).onclick = (e) => {
    const edit = e.target.closest("[data-edit]");
    if (!edit && !e.target.closest("[data-add]")) return;
    const index = edit ? Number(edit.dataset.edit) : -1;
    openForm({
      title: `${index >= 0 ? "Modifier" : "Ajouter"} : ${conf.title.toLowerCase()}`,
      values: index >= 0 ? items[index] : {},
      fields: conf.fields,
      danger: index >= 0 ? {
        label: "Supprimer",
        onClick: async () => {
          if (!window.confirm("Supprimer cet élément du profil ?")) return false;
          await save({ [key]: items.filter((_, i) => i !== index) }, "Supprimé");
          return true;
        },
      } : undefined,
      onSubmit: (v) => save({ [key]: index >= 0 ? items.map((x, i) => (i === index ? v : x)) : [...items, v] }),
    });
  };
}

// --- Import depuis LinkedIn -------------------------------------------------

function renderImport() {
  const noAi = !gemini.enabled;
  $("#import").innerHTML = html`
    ${head("// Importer depuis LinkedIn", "Remplir mon profil")}
    <div style="display:grid;gap:14px">
      <p class="small muted">LinkedIn ne partage avec les applications que votre nom, votre e-mail et votre photo. Pour le reste, deux façons rapides :</p>
      <details class="guide" open>
        <summary>Export de vos données LinkedIn (recommandé)</summary>
        <ol class="small">
          <li>Sur LinkedIn : <strong>Moi</strong> → <strong>Préférences et confidentialité</strong> → <strong>Confidentialité des données</strong> → <strong>Obtenir une copie de vos données</strong>.</li>
          <li>Choisissez les données qui vous intéressent (Profil, Postes, Compétences, Formation, Langues) ou l'archive complète, puis <strong>Demander l'archive</strong>.</li>
          <li>Vous recevez un e-mail en quelques minutes : téléchargez le ZIP et déposez-le ici <strong>sans le décompresser</strong>.</li>
        </ol>
      </details>
      <details class="guide">
        <summary>PDF de votre profil ou CV</summary>
        <ol class="small">
          <li>Sur votre profil LinkedIn : bouton <strong>Plus</strong> → <strong>Enregistrer au format PDF</strong>.</li>
          <li>Déposez ce PDF (ou votre CV) ici : Gemini en extrait les informations. Vous pouvez aussi coller du texte.</li>
        </ol>
      </details>
      <div class="import-actions">
        <label class="btn">Importer l'export (ZIP)<input type="file" accept=".zip,application/zip" data-import="linkedin-export" hidden></label>
        <label class="btn ghost ${noAi ? "disabled" : ""}">Importer un PDF<input type="file" accept=".pdf,application/pdf" data-import="pdf" hidden ${noAi ? "disabled" : ""}></label>
        <button type="button" class="btn ghost" data-paste ${noAi ? "disabled" : ""}>Coller du texte</button>
      </div>
      ${noAi ? html`<p class="small muted">PDF et texte : nécessitent Gemini (GEMINI_API_KEY non configurée sur le serveur).</p>` : ""}
      <p class="small muted" data-import-status hidden></p>
      <div id="import-preview"></div>
    </div>`.s;

  const box = $("#import");
  const status = $("[data-import-status]", box);
  const busy = (text) => {
    status.hidden = !text;
    status.textContent = text ?? "";
    $$("button, input", $(".import-actions", box)).forEach((el) => (el.disabled = Boolean(text) || (noAi && el.dataset.import !== "linkedin-export")));
  };
  const run = async (fn, text) => {
    busy(text);
    try {
      renderPreview(await attempt(fn));
    } catch { /* erreur déjà affichée */ } finally {
      busy(null);
    }
  };

  box.onchange = (e) => {
    const input = e.target.closest("[data-import]");
    if (!input?.files[0]) return;
    const form = new FormData();
    form.append("file", input.files[0]);
    input.value = "";
    const label = input.dataset.import === "pdf" ? "Analyse du PDF par Gemini…" : "Lecture de l'archive…";
    run(() => api(`/profile/import/${input.dataset.import}`, { method: "POST", form }), label);
  };
  box.onclick = async (e) => {
    if (!e.target.closest("[data-paste]")) return;
    const v = await openForm({
      title: "Coller du texte",
      fields: [{ name: "text", label: "Texte de votre profil ou CV", type: "textarea", full: true, required: true, maxlength: 60000,
        hint: "Le texte est analysé par Gemini ; rien n'est enregistré avant votre validation." }],
      submitLabel: "Analyser",
    });
    if (v?.text) run(() => api("/profile/import/text", { method: "POST", body: { text: v.text } }), "Analyse du texte par Gemini…");
  };
}

const OP_LABEL = { add: "Ajout", set: "Ajout", update: "Modification", deactivate: "Désactiver" };
const CONFIDENCE_LABEL = { haute: "confiance haute", moyenne: "confiance moyenne", basse: "confiance basse" };

/** Une ligne de proposition : case à cocher + valeur + badges + justification. */
function proposalItem(it) {
  const replaces = it.op === "set" && it.before;
  return html`<label class="change">
    <input type="checkbox" value="${it.id}" ${it.checked ? "checked" : ""}>
    <span class="grow">
      <span class="change-line">
        <span class="pill plain">${replaces ? "Remplace votre saisie" : OP_LABEL[it.op] ?? it.op}</span>
        ${it.inferred ? html`<span class="badge-inferred" title="Déduction de l'assistant, pas un fait importé">déduit</span>` : ""}
        ${it.confidence ? html`<span class="small muted">${CONFIDENCE_LABEL[it.confidence] ?? it.confidence}</span>` : ""}
      </span>
      <span class="small">${it.display}</span>
      ${it.before ? html`<span class="small muted">Actuel : ${it.before}</span>` : ""}
      ${it.reason ? html`<span class="small muted">${it.reason}</span>` : ""}
      ${it.examples?.length ? html`<span class="small muted">D'après : ${it.examples.join(", ")}</span>` : ""}
    </span>
  </label>`;
}

/**
 * Aperçu d'une proposition (import, analyse, synthèse des règles), validée élément par élément.
 * Les éléments sont regroupés par champ ; rien n'est enregistré avant « Appliquer ».
 */
function renderPreview(preview) {
  const box = $("#import-preview");
  if (!preview) { box.innerHTML = ""; return; }
  const groups = new Map();
  for (const it of preview.items) {
    if (!groups.has(it.label)) groups.set(it.label, []);
    groups.get(it.label).push(it);
  }
  const intro = preview.kind === "import"
    ? "Cochez ce que vous voulez appliquer. Rien de ce que vous avez saisi n'est supprimé."
    : "Ce sont des suggestions (« déduit ») : acceptez-les une par une. Rien n'est modifié sans votre accord.";
  box.innerHTML = html`
    <div class="import-preview">
      <h3>Aperçu · ${preview.source_label}${preview.filename ? ` · ${preview.filename}` : ""}</h3>
      ${preview.details.map((d) => html`<p class="small muted">${d}</p>`)}
      ${preview.items.length ? html`
        <p class="small">${intro}</p>
        <div class="changes">${[...groups].map(([label, items]) => html`
          <div class="change-group"><strong class="small">${label}</strong>${items.map(proposalItem)}</div>`)}
        </div>` : html`<p class="small">Rien de nouveau : votre profil contient déjà ces informations.</p>`}
      ${preview.questions?.length ? html`
        <div class="questions">
          <p class="small"><strong>Pour compléter votre profil :</strong></p>
          <ul class="small">${preview.questions.map((q) => html`<li>${q}</li>`)}</ul>
          <button type="button" class="btn ghost sm" data-onboarding>Compléter mon profil avec l'assistant</button>
        </div>` : ""}
      <div class="import-actions">
        ${preview.items.length ? html`
          <button type="button" class="btn" data-apply>Appliquer la sélection</button>
          <button type="button" class="btn ghost" data-check-all>Tout cocher</button>` : ""}
        <button type="button" class="btn ghost" data-cancel-import>${preview.items.length ? "Annuler" : "Fermer"}</button>
      </div>
    </div>`.s;

  box.onclick = async (e) => {
    if (e.target.closest("[data-onboarding]")) return startOnboarding();
    if (e.target.closest("[data-check-all]")) return $$(".change input", box).forEach((i) => (i.checked = true));
    if (e.target.closest("[data-cancel-import]")) {
      api(`/profile/proposals/${preview.import_id}`, { method: "DELETE" }).catch(() => {});
      renderPreview(null);
    }
    if (e.target.closest("[data-apply]")) {
      const accept = $$(".change input:checked", box).map((i) => i.value);
      if (!accept.length) return toast("Rien n'est coché", true);
      e.target.disabled = true;
      try {
        profile = await attempt(() => api(`/profile/proposals/${preview.import_id}/apply`, { method: "POST", body: { accept } }),
          `${accept.length} élément${accept.length > 1 ? "s" : ""} appliqué${accept.length > 1 ? "s" : ""}`);
        const hadQuestions = preview.questions?.length;
        renderPreview(null);
        renderProfile();
        if (hadQuestions || preview.kind === "import") offerOnboarding();
      } catch {
        e.target.disabled = false;
      }
    }
  };
}

/** Après un import ou une analyse : proposer de compléter le profil avec l'assistant. */
function offerOnboarding() {
  const box = $("#import-preview");
  box.innerHTML = html`<div class="import-preview">
    <p class="small">Profil mis à jour. L'assistant peut vous poser quelques questions sur ce qui manque encore
    (lieux, stades, salaire, cloud, déplacements…).</p>
    <div class="import-actions">
      <button type="button" class="btn" data-onboarding>Compléter mon profil avec l'assistant</button>
      <button type="button" class="btn ghost" data-close-offer>Plus tard</button>
    </div>
  </div>`.s;
  box.onclick = (e) => {
    if (e.target.closest("[data-onboarding]")) startOnboarding();
    if (e.target.closest("[data-onboarding], [data-close-offer]")) box.innerHTML = "";
  };
}

function startOnboarding() {
  if (!gemini.enabled) return toast("L'assistant nécessite Gemini (GEMINI_API_KEY non configurée).", true);
  openChatPanel({ mode: "onboarding" });
}

// --- Page -------------------------------------------------------------------

function renderProfile() {
  renderPro();
  renderPrefs();
  renderSkills();
  renderRules();
  renderList("experiences");
  renderList("education");
}

async function render() {
  const [{ user, counts }, data, chat] = await Promise.all([
    loadMe(),
    api("/profile"),
    api("/chat/status").catch(() => ({ enabled: false })),
  ]);
  profile = data;
  gemini = chat;
  const name = user.name ?? [user.given_name, user.family_name].filter(Boolean).join(" ");
  $("#identity").innerHTML = html`
    ${user.picture
      ? html`<img class="avatar" src="${user.picture}" alt="" referrerpolicy="no-referrer">`
      : html`<span class="avatar">${(name || "?")[0].toUpperCase()}</span>`}
    <div><h2>${name || "Utilisateur"}</h2><p class="muted small">${profile.headline || "Connecté avec LinkedIn"}</p></div>`.s;

  const last = profile.raw_imports.at(-1);
  const rows = [
    ["E-mail", user.email ?? "Non communiqué"],
    ["Membre depuis", memberSince.format(new Date(user.created_at))],
    ["Process suivis", counts.processes],
    ["Dernier import", last ? `${new Date(last.imported_at).toLocaleDateString("fr-FR")} · ${last.filename ?? last.source}` : "Aucun"],
    ["Aujourd'hui", fmtToday()],
  ];
  $("#details").innerHTML = html`${rows.map(([k, v]) => html`
    <div class="row-item"><span class="grow muted small">${k}</span><span>${v}</span></div>`)}`.s;

  $("#legend").innerHTML = html`${STATUSES.map(([k]) => html`
    <div class="item"><span style="min-width:110px">${pill(k)}</span><span class="small muted">${HELP[k]}</span></div>`)}`.s;

  // L'aperçu d'import en cours est conservé si la page est rafraîchie (ex. après une action du chat)
  if (!$("#import .import-preview")) renderImport();
  renderProfile();
}

boot(render);
