// Profil, en quatre sections : « Ma recherche » (ce qui classe les offres), « Mon parcours » (ce qui sert aux
// candidatures), « Réponses types » et « Compte ».
import {
  $, $$, ICONS, api, attempt, boot, html, loadMe, openChatPanel, openForm, openJobDialog, plain, proTag, raw,
  refreshCounts, toast,
} from "./app.js";

const SECTIONS = ["recherche", "parcours", "candidatures", "compte"];
const WORK_MODES = [["remote", "Remote"], ["hybride", "Hybride"], ["sur_site", "Sur site"]];
const WORK_MODE_LABEL = Object.fromEntries(WORK_MODES);
const STAGES = ["Pré-seed", "Seed", "Série A", "Série B", "Série C", "Growth"];
const RULE_KINDS = [["eviter", "À éviter"], ["preferer", "À préférer"]];

const monthYear = new Intl.DateTimeFormat("fr-FR", { month: "short", year: "numeric" });
const memberSince = new Intl.DateTimeFormat("fr-FR", { month: "long", year: "numeric" });

let profile;
let options = { families: [], sectors: [], skills: [], locations: [] };
let gemini = { enabled: false };
let feedback = { new: 0, total: 0, threshold: 3, recent: [] };
let jobNames = {}; // id de poste -> « Entreprise – Titre » (offres citées par les règles)
let billing = null; // offre Free / Pro (GET /api/me)
let search = null; // formulaire « Ma recherche » en cours de saisie

// --- Helpers ----------------------------------------------------------------

const splitList = (v) => (v ?? "").split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
const joinList = (a) => (a ?? []).join(", ");
const same = (a, b) => a.trim().toLowerCase() === b.trim().toLowerCase();
const has = (list, value) => list.some((x) => same(x, value));

/** "2021-03" -> "mars 2021", "2021" -> "2021", texte libre inchangé. */
function fmtYm(v) {
  if (!v) return "";
  const m = /^(\d{4})-(\d{2})$/.exec(v);
  return m ? monthYear.format(new Date(Number(m[1]), Number(m[2]) - 1, 1)) : v;
}
const period = (item) =>
  item.start || item.end ? `${fmtYm(item.start) || "?"} – ${item.end ? fmtYm(item.end) : "aujourd'hui"}` : "";

const head = (title, hint = "", action = "") => html`
  <div class="section-head">
    <div class="intro"><h2>${title}</h2>${hint ? html`<p class="small muted">${hint}</p>` : ""}</div>${action}
  </div>`;
const editBtn = (attr, label) => html`<button type="button" class="icon-btn" data-${attr} aria-label="${label}" title="${label}">${ICONS.edit}</button>`;
const emptyRow = (text) => html`<p class="empty">${text}</p>`;

async function save(patch, message = "Profil enregistré") {
  profile = await attempt(() => api("/profile", { method: "PATCH", body: patch }), message);
  renderProfile();
}

// --- Ma recherche : un seul formulaire, des choix à cocher --------------------------

const draftFromProfile = () => {
  const p = profile.preferences;
  return {
    roles: [...p.roles], locations: [...p.locations], modes: [...p.work_modes], sectors: [...p.sectors],
    stages: [...p.company_stages], salary: p.min_salary_keur ?? "", years: profile.years_experience ?? "",
    skills: profile.skills.map((s) => ({ ...s })), dirty: false,
  };
};

const toggleChip = (field, value, selected) => html`
  <button type="button" class="chip pick" data-pick="${field}" data-value="${value}" aria-pressed="${selected}">${value}</button>`;

/** Valeurs proposées à cocher, plus celles déjà saisies qui n'en font pas partie. */
const picks = (field, values) => [
  ...values.map((v) => toggleChip(field, v, has(search[field], v))),
  ...search[field].filter((v) => !has(values, v)).map((v) => toggleChip(field, v, true)),
];

function renderSearch() {
  const groups = new Map();
  for (const f of options.families) groups.set(f.group, [...(groups.get(f.group) ?? []), f.label]);
  const known = options.families.map((f) => f.label);
  const others = search.roles.filter((r) => !has(known, r));

  $("#search").innerHTML = html`
    ${head("Ce que je cherche", "Les offres sont classées d'après ces critères et vos J'aime. Un critère vide ne pénalise aucune offre.")}
    <form class="search-form" data-search>
      <fieldset class="field"><legend>Métiers visés</legend>
        ${[...groups].map(([group, labels]) => html`<div class="pick-row"><span class="small muted pick-group">${group}</span>
          <div class="chip-row">${labels.map((l) => toggleChip("roles", l, has(search.roles, l)))}</div></div>`)}
        ${others.length ? html`<div class="pick-row"><span class="small muted pick-group">Autres</span>
          <div class="chip-row">${others.map((r) => toggleChip("roles", r, true))}</div></div>` : ""}
      </fieldset>

      <fieldset class="field"><legend>Compétences</legend>
        <div class="tags">${search.skills.length ? search.skills.map((s, i) => html`
          <span class="tag removable ${s.kind === "fort" ? "strong" : ""}">
            <button type="button" class="tag-main" data-toggle-skill="${i}" title="Point fort ou à développer : cliquer pour changer">${s.name}${s.kind === "fort" ? "" : " · à développer"}</button>
            <button type="button" class="tag-x" data-rm-skill="${i}" aria-label="Retirer ${s.name}">×</button>
          </span>`) : html`<span class="small muted">Aucune : ajoutez vos points forts, c'est le critère le plus parlant.</span>`}</div>
        <div class="inline-add"><input class="input" name="skill" list="skill-list" maxlength="120" placeholder="Ajouter une compétence puis Entrée" aria-label="Compétence">
          <button type="button" class="btn ghost sm" data-add="skill">Ajouter</button></div>
        <datalist id="skill-list">${options.skills.map((s) => html`<option value="${s}"></option>`)}</datalist>
      </fieldset>

      <div class="fields">
        <label class="field">Années d'expérience
          <input class="input" type="number" name="years" min="0" max="60" step="0.5" value="${search.years}"></label>
        <label class="field">Salaire minimum (k€ brut / an)
          <input class="input" type="number" name="salary" min="0" max="1000" value="${search.salary}"></label>
      </div>

      <fieldset class="field"><legend>Lieux</legend>
        <div class="tags">${search.locations.map((l, i) => html`<span class="tag removable strong">
          <span class="tag-main">${l}</span><button type="button" class="tag-x" data-rm-location="${i}" aria-label="Retirer ${l}">×</button></span>`)}</div>
        <div class="inline-add"><input class="input" name="location" list="location-list" maxlength="120" placeholder="Ajouter une ville puis Entrée" aria-label="Ville">
          <button type="button" class="btn ghost sm" data-add="location">Ajouter</button></div>
        <datalist id="location-list">${options.locations.map((l) => html`<option value="${l}"></option>`)}</datalist>
      </fieldset>

      <fieldset class="field"><legend>Mode de travail accepté</legend>
        <div class="chip-row">${WORK_MODES.map(([key, label]) => html`
          <button type="button" class="chip pick" data-mode="${key}" aria-pressed="${search.modes.includes(key)}">${label}</button>`)}</div>
      </fieldset>

      <fieldset class="field"><legend>Secteurs qui m'attirent</legend>
        <div class="chip-row">${picks("sectors", options.sectors.map((s) => s.label))}</div>
      </fieldset>

      <fieldset class="field"><legend>Stades d'entreprise</legend>
        <div class="chip-row">${picks("stages", STAGES)}</div>
      </fieldset>

      <div class="search-save">
        <button class="btn" data-save ${search.dirty ? "" : "disabled"}>Enregistrer et reclasser</button>
        <a class="small" href="/postes">Voir les offres</a>
      </div>
    </form>`.s;

  const box = $("#search");
  const redraw = () => { search.dirty = true; renderSearch(); };
  const addFrom = (name) => {
    const input = box.querySelector(`input[name="${name}"]`);
    const value = input.value.trim();
    if (!value) return;
    if (name === "skill") {
      if (search.skills.some((s) => same(s.name, value))) return toast("Compétence déjà présente", true);
      search.skills.push({ name: value, kind: "fort" });
    } else {
      if (has(search.locations, value)) return toast("Lieu déjà présent", true);
      search.locations.push(value);
    }
    redraw();
    box.querySelector(`input[name="${name}"]`).focus();
  };
  box.onclick = (e) => {
    const t = e.target.closest("button");
    if (!t || t.matches("[data-save]")) return;
    if (t.dataset.pick) {
      const field = t.dataset.pick;
      search[field] = has(search[field], t.dataset.value)
        ? search[field].filter((v) => !same(v, t.dataset.value)) : [...search[field], t.dataset.value];
    } else if (t.dataset.mode) {
      search.modes = search.modes.includes(t.dataset.mode)
        ? search.modes.filter((m) => m !== t.dataset.mode) : [...search.modes, t.dataset.mode];
    } else if (t.dataset.toggleSkill) {
      const s = search.skills[Number(t.dataset.toggleSkill)];
      s.kind = s.kind === "fort" ? "a_developper" : "fort";
    } else if (t.dataset.rmSkill) search.skills.splice(Number(t.dataset.rmSkill), 1);
    else if (t.dataset.rmLocation) search.locations.splice(Number(t.dataset.rmLocation), 1);
    else if (t.dataset.add) return addFrom(t.dataset.add);
    else return;
    redraw();
  };
  box.onkeydown = (e) => {
    if (e.key !== "Enter" || !e.target.matches('input[name="skill"], input[name="location"]')) return;
    e.preventDefault();
    addFrom(e.target.name);
  };
  box.oninput = (e) => {
    if (!["years", "salary"].includes(e.target.name)) return;
    search[e.target.name] = e.target.value;
    search.dirty = true;
    box.querySelector("[data-save]").disabled = false;
  };
  box.onsubmit = async (e) => {
    e.preventDefault();
    const number = (v) => (v === "" || v == null ? null : Number(v));
    const body = {
      years_experience: number(search.years),
      skills: search.skills,
      preferences: {
        ...profile.preferences, roles: search.roles, locations: search.locations, work_modes: search.modes,
        sectors: search.sectors, company_stages: search.stages, min_salary_keur: number(search.salary),
      },
    };
    search = null; // reconstruit depuis le profil enregistré
    await save(body, "Recherche enregistrée : les offres sont reclassées").catch(() => (search = draftFromProfile()));
  };
}

// --- Règles et entreprises exclues -----------------------------------------------

const RULE_SOURCE = { manuel: "saisie", import: "import", analyse: "analyse", feedback: "apprise de vos retours", chat: "assistant" };
const fmtDay = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short" });

/** Offre Free : fonction réservée au Pro (clic : fenêtre Pro). */
const proOnly = (feature) => (billing?.enabled && !billing.limits?.[feature] ? raw("data-pro") : "");
const proMark = (feature) => (proOnly(feature) ? html` ${proTag}` : "");

function renderRules() {
  const rules = profile.rules;
  const excluded = profile.excluded_companies;
  const synthesize = html`<button type="button" class="btn ${feedback.new >= feedback.threshold ? "" : "ghost"} sm" data-synthesize
    ${!gemini.enabled || !feedback.total ? "disabled" : ""} ${proOnly("rules_synthesis")}
    title="${feedback.total ? "Propose des règles à partir de vos J'aime et Pas intéressé" : "Donnez d'abord des retours sur des offres"}">
    Déduire des règles de mes retours${feedback.new ? ` (${feedback.new})` : ""}${proMark("rules_synthesis")}</button>`;
  $("#rules").innerHTML = html`
    ${head("Règles et exclusions", "En plus des critères : ce que l'IA doit éviter ou préférer.", synthesize)}
    <p class="small muted" data-rules-status hidden></p>
    <div id="rules-preview"></div>
    <div class="rows">${rules.length ? rules.map((r, i) => html`
      <div class="row-item rule ${r.active ? "" : "inactive"}">
        <span class="pill ${r.kind === "eviter" ? "st-refuse" : "st-offre"}">${r.kind === "eviter" ? "Éviter" : "Préférer"}</span>
        <span class="grow">
          <span>${r.text}</span>
          <span class="rule-meta small muted">
            ${RULE_SOURCE[r.source] ?? r.source}${r.created_at ? ` · ${fmtDay.format(new Date(r.created_at))}` : ""}
            ${r.examples.length ? html` · d'après ${r.examples.map((id) => html`<button type="button" class="link-btn example" data-job="${id}">${jobNames[id] ?? `offre #${id}`}</button>`)}` : ""}
          </span>
        </span>
        <label class="switch" title="${r.active ? "Désactiver (gardée mais ignorée)" : "Réactiver"}">
          <input type="checkbox" data-toggle-rule="${i}" ${r.active ? "checked" : ""} aria-label="Règle active"><span></span>
        </label>
        <button type="button" class="icon-btn" data-rm-rule="${i}" aria-label="Supprimer la règle">${ICONS.trash}</button>
      </div>`) : emptyRow("Aucune règle. Exemple : « Écarter les postes où TypeScript est au cœur du poste ».")}
    </div>
    <form class="inline-add" data-add-rule>
      <select class="input" name="kind" aria-label="Type de règle">${RULE_KINDS.map(([v, l]) => html`<option value="${v}">${l}</option>`)}</select>
      <input class="input" name="text" placeholder="Ex. postes avec astreintes" maxlength="500" required aria-label="Règle">
      <button class="btn sm">Ajouter</button>
    </form>
    <p class="small strong">Entreprises exclues</p>
    <div class="tags">${excluded.length ? excluded.map((c, i) => html`
      <span class="tag removable" title="${c.reason ?? ""}"><span class="tag-main">${c.name}</span>
        <button type="button" class="tag-x" data-rm-excluded="${i}" aria-label="Ne plus exclure ${c.name}">×</button></span>`)
      : html`<span class="small muted">Aucune</span>`}</div>
    <form class="inline-add" data-add-excluded>
      <input class="input" name="name" placeholder="Entreprise à exclure" maxlength="120" required aria-label="Entreprise">
      <button class="btn ghost sm">Exclure</button>
    </form>`.s;

  const box = $("#rules");
  box.onclick = async (e) => {
    if (e.target.closest("#rules-preview")) return; // géré par l'aperçu
    if (e.target.closest("[data-synthesize]")) {
      const status = $("[data-rules-status]", box);
      status.hidden = false;
      status.textContent = "Gemini analyse vos retours sur les offres…";
      try {
        const preview = await attempt(() => api("/profile/rules/synthesize", { method: "POST" }));
        feedback = await api("/feedback");
        renderPreview(preview, "#rules-preview");
      } catch { /* erreur déjà affichée */ } finally {
        status.hidden = true;
      }
      return;
    }
    const rm = e.target.closest("[data-rm-rule]");
    if (rm && window.confirm("Supprimer cette règle ? (vous pouvez aussi la désactiver pour la garder)")) {
      save({ rules: rules.filter((_, i) => i !== Number(rm.dataset.rmRule)) }, "Règle supprimée");
    }
    const ex = e.target.closest("[data-rm-excluded]");
    if (ex) save({ excluded_companies: excluded.filter((_, i) => i !== Number(ex.dataset.rmExcluded)) }, "Entreprise réintégrée");
    const job = e.target.closest("[data-job]");
    if (job) openJobDialog(Number(job.dataset.job));
  };
  box.onchange = (e) => {
    const t = e.target.closest("[data-toggle-rule]");
    if (!t) return;
    const i = Number(t.dataset.toggleRule);
    save({ rules: rules.map((r, j) => (j === i ? { ...r, active: t.checked } : r)) },
      t.checked ? "Règle réactivée" : "Règle désactivée (gardée, mais ignorée)");
  };
  box.onsubmit = (e) => {
    e.preventDefault();
    const f = e.target.elements;
    if (e.target.matches("[data-add-rule]")) {
      const text = f.text.value.trim();
      if (text) save({ rules: [...rules, { kind: f.kind.value, text, source: "manuel" }] }, "Règle ajoutée");
      return;
    }
    const name = f.name.value.trim();
    if (!name) return;
    if (excluded.some((c) => same(c.name, name))) return toast("Déjà exclue", true);
    save({ excluded_companies: [...excluded, { name, reason: null }] }, `${name} exclue`);
  };
}

// --- Barème de notation (avancé) ------------------------------------------------

// Libellés par défaut ; l'API envoie le barème de la variante de notation en cours (profile.criteria), avec en v2
// une définition personnelle pour le stade, le type de rôle et le produit.
const CRITERIA_LABELS = {
  competences: "Correspondance des compétences",
  stade: "Stade de l'entreprise et qualité des investisseurs",
  ownership: "Transversalité / ownership",
  ia_concrete: "Produit IA concret avec impact réel",
  xp: "Expérience demandée compatible",
  references: "Ressemblance avec les offres de référence",
};
const DEFAULT_WEIGHTS = { competences: 3, stade: 2, ownership: 2, ia_concrete: 1, xp: 1, references: 1 };
const SOURCE_NOTE = { preferences: "déduit de vos préférences", aucune: "non défini : ce critère ne départage pas les offres" };

function renderScoring() {
  const { weights, threshold } = profile.scoring;
  const definitions = profile.scoring.definitions ?? {};
  const criteria = profile.criteria?.length
    ? profile.criteria : Object.entries(CRITERIA_LABELS).map(([key, label]) => ({ key, label }));
  $("#scoring").innerHTML = html`
    <p class="small muted">Poids des critères de la note /10 donnée par l'IA, et seuil sous lequel une offre est écartée.</p>
    <form class="scoring" data-scoring>
      ${criteria.map((c) => html`
        <label class="scoring-row">
          <span class="small">${c.label}</span>
          <input class="input" type="number" name="${c.key}" min="0" max="10" step="0.5" value="${weights[c.key] ?? 0}" aria-label="Poids : ${c.label}">
        </label>
        ${c.personal ? html`
        <label class="scoring-def">
          <span class="small muted">Ce qui compte pour vous${c.source !== "utilisateur" && SOURCE_NOTE[c.source]
            ? ` · ${SOURCE_NOTE[c.source]}${c.definition ? ` (« ${c.definition} »)` : ""}` : ""}</span>
          <input class="input" type="text" name="def-${c.key}" maxlength="300" value="${definitions[c.key] ?? ""}"
            placeholder="${c.hint ?? ""}" aria-label="Définition : ${c.label}">
        </label>` : ""}`)}
      <label class="scoring-row threshold">
        <span class="small"><strong>Seuil</strong> · en dessous, l'offre est écartée</span>
        <input class="input" type="number" name="threshold" min="0" max="10" step="0.5" value="${threshold}" aria-label="Seuil">
      </label>
      <div class="import-actions">
        <button class="btn sm">Enregistrer le barème</button>
        <button type="button" class="btn ghost sm" data-default-scoring>Valeurs par défaut</button>
      </div>
    </form>`.s;

  const box = $("#scoring");
  box.onsubmit = (e) => {
    e.preventDefault();
    const f = e.target.elements;
    const next = Object.fromEntries(criteria.map((c) => [c.key, Number(f[c.key].value || 0)]));
    if (!Object.values(next).some((w) => w > 0)) return toast("Au moins un critère doit avoir un poids", true);
    // Définitions personnelles (v2) ; celles d'une autre variante sont conservées telles quelles
    const defs = { ...definitions };
    criteria.filter((c) => c.personal).forEach((c) => { defs[c.key] = f[`def-${c.key}`].value.trim(); });
    save({ scoring: { weights: next, threshold: Number(f.threshold.value || 0), definitions: defs } },
      "Barème enregistré : réévaluation en cours");
  };
  box.onclick = (e) => {
    if (e.target.closest("[data-default-scoring]")) {
      save({ scoring: { weights: DEFAULT_WEIGHTS, threshold: 6, definitions } }, "Barème par défaut rétabli");
    }
  };
}

// --- Identité professionnelle ---------------------------------------------------

function renderPro() {
  const p = profile;
  const rows = [
    ["Titre", p.headline],
    ["Point fort", p.positioning],
    ["Localisation", p.location],
    ["Langues", p.languages.map((l) => (l.level ? `${l.name} (${l.level})` : l.name)).join(", ")],
  ];
  $("#pro").innerHTML = html`
    ${head("En bref", "", editBtn("edit-pro", "Modifier l'identité professionnelle"))}
    <div class="rows">${rows.map(([k, v]) => html`
      <div class="row-item"><span class="muted small" style="min-width:110px">${k}</span><span class="grow">${v || html`<span class="muted">—</span>`}</span></div>`)}
    </div>
    ${p.summary ? html`<p class="small prewrap" style="margin-top:12px">${p.summary}</p>` : ""}`.s;

  $("#pro").onclick = (e) => {
    if (!e.target.closest("[data-edit-pro]")) return;
    openForm({
      title: "En bref",
      values: { ...p, languages: p.languages.map((l) => (l.level ? `${l.name} : ${l.level}` : l.name)).join("\n") },
      fields: [
        { name: "headline", label: "Titre", full: true, maxlength: 300, placeholder: "Ex. Développeur backend senior" },
        { name: "positioning", label: "Point fort / positionnement", type: "textarea", full: true, maxlength: 1000,
          hint: "En 1-2 phrases, ce que vous faites de mieux (ex. industrialiser des prototypes IA en production)" },
        { name: "location", label: "Localisation", maxlength: 200 },
        { name: "languages", label: "Langues", type: "textarea", full: true, hint: "Une par ligne, ex. « Anglais : courant »" },
        { name: "summary", label: "Résumé", type: "textarea", full: true, maxlength: 5000 },
      ],
      onSubmit: (v) => save({
        headline: v.headline,
        positioning: v.positioning,
        location: v.location,
        summary: v.summary,
        languages: (v.languages ?? "").split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
          const [name, ...level] = line.split(/\s*:\s*/);
          return { name, level: level.join(" : ") || null };
        }),
      }),
    });
  };
}

// --- Réponses types pour les candidatures -----------------------------------
// Reprises telles quelles dans les formulaires préparés (page Candidature) ; une valeur absente laisse le champ « À toi ».

const AUTH_LABEL = { oui: "Oui, sans visa", non: "Non, visa nécessaire" };

function defaultRows(d) {
  const salary = d.salary_min_keur || d.salary_max_keur
    ? `${d.salary_min_keur ?? "?"}–${d.salary_max_keur ?? "?"} k€ brut / an` : "";
  return [
    ["Nom", [d.first_name, d.last_name].filter(Boolean).join(" ")],
    ["Email de contact", d.email],
    ["Téléphone", d.phone],
    ["LinkedIn", d.linkedin_url],
    ["GitHub", d.github_url],
    ["Portfolio", d.portfolio_url],
    ["Ville", d.city],
    ["Travail en France / UE", AUTH_LABEL[d.work_authorization] ?? ""],
    ["Disponibilité", [d.availability, d.available_from ? `dès le ${d.available_from}` : ""].filter(Boolean).join(", ")],
    ["Salaire attendu", salary],
    ["Mode de travail", WORK_MODE_LABEL[d.work_mode] ?? ""],
    ["Langues", joinList(d.languages)],
  ];
}

function editDefaults() {
  const d = profile.preferences.application_defaults ?? {};
  return openForm({
    title: "Réponses types pour les candidatures",
    values: { ...d, languages: joinList(d.languages) },
    fields: [
      { name: "first_name", label: "Prénom", maxlength: 200 },
      { name: "last_name", label: "Nom", maxlength: 200 },
      { name: "email", label: "Email de contact", type: "email", maxlength: 320 },
      { name: "phone", label: "Téléphone", maxlength: 40, placeholder: "+33 6 12 34 56 78" },
      { name: "linkedin_url", label: "LinkedIn", type: "url", maxlength: 300 },
      { name: "github_url", label: "GitHub", type: "url", maxlength: 300 },
      { name: "portfolio_url", label: "Portfolio / site", type: "url", maxlength: 300 },
      { name: "city", label: "Ville", maxlength: 200, placeholder: "Paris" },
      { name: "work_authorization", label: "Autorisé à travailler en France / UE", type: "select",
        options: [["", "Non précisé"], ["oui", AUTH_LABEL.oui], ["non", AUTH_LABEL.non]] },
      { name: "work_mode", label: "Mode de travail souhaité", type: "select", options: [["", "Non précisé"], ...WORK_MODES] },
      { name: "availability", label: "Préavis / disponibilité", maxlength: 200, placeholder: "Préavis de 3 mois" },
      { name: "available_from", label: "Disponible à partir du", type: "date" },
      { name: "salary_min_keur", label: "Salaire attendu, bas (k€ brut / an)", type: "number", min: 0, max: 1000 },
      { name: "salary_max_keur", label: "Salaire attendu, haut (k€ brut / an)", type: "number", min: 0, max: 1000 },
      { name: "languages", label: "Langues", full: true, hint: "Séparées par des virgules", placeholder: "Français (natif), Anglais (courant)" },
    ],
    extra: html`<p class="small muted">Ces valeurs servent telles quelles dans les candidatures que vous préparez. Rien n'est envoyé
      à un site : vous copiez-collez vous-même. Elles ne changent pas le classement des offres.</p>`,
    onSubmit: async (v) => {
      profile = await api("/profile/application-defaults", { method: "PUT", body: { ...v, languages: splitList(v.languages) } });
      return profile;
    },
  }).then((r) => {
    if (r) {
      renderDefaults();
      toast("Réponses types enregistrées");
    }
    return r;
  });
}

function renderDefaults() {
  const d = profile.preferences.application_defaults ?? {};
  $("#apply-defaults").innerHTML = html`
    ${head("Réponses types", "Reprises dans chaque candidature préparée. Un champ laissé vide reste « À toi » dans le formulaire.",
      editBtn("edit-defaults", "Modifier les réponses types"))}
    <div class="rows">${defaultRows(d).map(([k, v]) => html`
      <div class="row-item"><span class="muted small" style="min-width:160px">${k}</span><span class="grow">${v || html`<span class="muted">—</span>`}</span></div>`)}
    </div>`.s;
  $("#apply-defaults").onclick = (e) => {
    if (e.target.closest("[data-edit-defaults]")) editDefaults().catch(() => {});
  };
}

// --- Expériences et formation -------------------------------------------------

const LIST_SECTIONS = {
  experiences: {
    title: "Expériences",
    hint: "Elles alimentent le CV adapté et le classement.",
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
    title: "Diplômes",
    hint: "",
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
    ${head(conf.title, conf.hint, addBtn)}
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

// --- Import (LinkedIn, CV) -------------------------------------------------

function renderImport() {
  const noAi = !gemini.enabled;
  $("#import").innerHTML = html`
    ${head("Remplir à partir de mon CV ou de LinkedIn", "La façon la plus rapide. Rien n'est enregistré avant votre validation.")}
    <div style="display:grid;gap:14px">
      <div class="import-actions">
        <label class="btn ${noAi ? "disabled" : ""}">Importer mon CV (PDF)<input type="file" accept=".pdf,application/pdf" data-import="pdf" hidden ${noAi ? "disabled" : ""}></label>
        <label class="btn ghost">Export LinkedIn (ZIP)<input type="file" accept=".zip,application/zip" data-import="linkedin-export" hidden></label>
        <button type="button" class="btn ghost" data-paste ${noAi ? "disabled" : ""}>Coller du texte</button>
      </div>
      <details class="guide">
        <summary>Où trouver ces fichiers sur LinkedIn ?</summary>
        <ol class="small">
          <li>PDF : sur votre profil LinkedIn, <strong>Plus</strong> → <strong>Enregistrer au format PDF</strong>.</li>
          <li>Export : <strong>Moi</strong> → <strong>Préférences et confidentialité</strong> → <strong>Confidentialité des données</strong> →
            <strong>Obtenir une copie de vos données</strong> (Profil, Postes, Compétences, Formation, Langues). Déposez le ZIP reçu sans le décompresser.</li>
        </ol>
      </details>
      <div class="import-actions">
        <button type="button" class="btn ghost sm" data-analyze ${noAi ? "disabled" : ""} ${proOnly("profile_analysis")}
          title="Déductions à partir de votre profil : point fort, rôles, compétences, années d'expérience">Analyser mon profil${proMark("profile_analysis")}</button>
        <button type="button" class="btn ghost sm" data-onboarding ${noAi ? "disabled" : ""}
          title="L'assistant vous pose quelques questions sur ce qui manque">Compléter avec l'assistant</button>
      </div>
      ${noAi ? html`<p class="small muted">PDF, texte et assistant : nécessitent Gemini (GEMINI_API_KEY non configurée sur le serveur).</p>` : ""}
      <p class="small muted" data-import-status hidden></p>
      <div id="import-preview"></div>
    </div>`.s;

  const box = $("#import");
  const status = $("[data-import-status]", box);
  const busy = (text) => {
    status.hidden = !text;
    status.textContent = text ?? "";
    $$(".import-actions button, .import-actions input", box).forEach((el) => {
      if (el.closest("#import-preview")) return;
      el.disabled = Boolean(text) || (noAi && el.dataset.import !== "linkedin-export");
    });
  };
  const run = async (fn, text) => {
    busy(text);
    try {
      renderPreview(await attempt(fn), "#import-preview");
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
    if (e.target.closest("#import-preview")) return; // géré par l'aperçu
    if (e.target.closest("[data-onboarding]")) return startOnboarding();
    if (e.target.closest("[data-analyze]")) {
      return run(() => api("/profile/analyze", { method: "POST" }), "Analyse de votre profil par Gemini…");
    }
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
function renderPreview(preview, target) {
  const box = $(target);
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
      renderPreview(null, target);
    }
    if (e.target.closest("[data-apply]")) {
      const accept = $$(".change input:checked", box).map((i) => i.value);
      if (!accept.length) return toast("Rien n'est coché", true);
      e.target.disabled = true;
      try {
        profile = await attempt(() => api(`/profile/proposals/${preview.import_id}/apply`, { method: "POST", body: { accept } }),
          `${accept.length} élément${accept.length > 1 ? "s" : ""} appliqué${accept.length > 1 ? "s" : ""}`);
        const hadQuestions = preview.questions?.length;
        renderPreview(null, target);
        search = null;
        renderProfile();
        if (hadQuestions || preview.kind === "import") offerOnboarding(target);
      } catch {
        e.target.disabled = false;
      }
    }
  };
}

/** Après un import ou une analyse : proposer de compléter le profil avec l'assistant. */
function offerOnboarding(target) {
  const box = $(target);
  box.innerHTML = html`<div class="import-preview">
    <p class="small">Profil mis à jour. Vérifiez « Ma recherche », ou laissez l'assistant vous poser les questions qui manquent.</p>
    <div class="import-actions">
      <button type="button" class="btn" data-onboarding>Compléter avec l'assistant</button>
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

// --- Abonnement ---------------------------------------------------------------

const fmtLongDay = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long", year: "numeric" });
const USAGE_LABEL = {
  chat_per_month: ["chat", "messages à l'assistant ce mois-ci"],
  active_processes: ["processes", "candidatures actives"],
  assessments_per_week: ["assessments", "évaluations ces 7 derniers jours"],
  imports_total: ["imports", "import de profil"],
};

function renderBilling() {
  const box = $("#billing");
  box.hidden = !billing?.enabled;
  if (!billing?.enabled) return;
  const until = billing.pro_until ? fmtLongDay.format(new Date(billing.pro_until)) : "";
  let plan, detail;
  if (billing.plan === "free") {
    plan = "Free";
    detail = html`<div class="rows">${Object.entries(USAGE_LABEL).filter(([k]) => billing.limits[k] != null).map(([k, [u, label]]) => html`
      <div class="row-item"><span class="grow muted small">${label}</span><span>${billing.usage[u] ?? 0} / ${billing.limits[k]}</span></div>`)}</div>`;
  } else if (billing.kind === "subscription") {
    plan = "Pro mensuel";
    detail = billing.past_due ? html`<p class="small exclusion">Paiement en échec : mettez à jour votre moyen de paiement.</p>`
      : billing.cancel_at_period_end ? html`<p class="small">Résilié, actif jusqu'au ${until}.</p>`
      : until ? html`<p class="small muted">Prochain renouvellement le ${until}.</p>` : "";
  } else if (billing.kind === "pass") {
    plan = "Pass 3 mois";
    detail = html`<p class="small">Actif jusqu'au ${until}, sans renouvellement automatique.</p>`;
  } else if (billing.trial) {
    plan = "Pro (essai)";
    detail = html`<p class="small">Essai gratuit jusqu'au ${fmtLongDay.format(new Date(billing.trial_until))}, sans
      carte bancaire. Ensuite, retour à l'offre Free sans rien perdre, sauf si vous passez Pro.</p>`;
  } else {
    plan = "Pro";
    detail = html`<p class="small muted">Accès Pro offert.</p>`;
  }
  box.innerHTML = html`
    <div class="section-head"><div class="intro"><h2>Offre ${plan}</h2></div></div>
    <div style="display:grid;gap:10px">
      ${detail}
      <div class="import-actions">
        ${billing.plan === "free" || billing.trial ? html`<button type="button" class="btn sm" data-pro>Passer Pro</button>` : ""}
        ${billing.plan === "pro" && billing.kind === "pass" ? html`<button type="button" class="btn ghost sm" data-pro>Prolonger de 3 mois</button>` : ""}
        ${billing.kind ? html`<button type="button" class="btn ghost sm" data-portal>Gérer mon abonnement</button>` : ""}
      </div>
    </div>`.s;
  box.onclick = async (e) => {
    const btn = e.target.closest("[data-portal]");
    if (!btn) return;
    btn.disabled = true;
    try {
      location.href = (await attempt(() => api("/billing/portal", { method: "POST" }))).url;
    } catch {
      btn.disabled = false;
    }
  };
}

/** Retour de Stripe Checkout (?checkout=success|cancel) : l'offre est activée par le webhook, on attend qu'il passe. */
async function handleCheckoutReturn() {
  const url = new URL(location.href);
  const state = url.searchParams.get("checkout");
  if (!state) return;
  url.searchParams.delete("checkout");
  history.replaceState(null, "", url);
  show("compte");
  if (state !== "success") return toast("Paiement annulé : aucun montant n'a été prélevé.");
  toast("Paiement reçu, merci ! Activation de votre offre Pro…");
  for (let i = 0; i < 5; i++) {
    await refreshCounts().catch(() => {});
    billing = (await loadMe()).billing;
    if (billing?.plan === "pro") break;
    await new Promise((r) => setTimeout(r, 2000));
  }
  renderBilling();
  renderImport();
  renderRules();
  if (billing?.plan === "pro") toast("Votre offre Pro est active.");
  else toast("Paiement en cours de traitement : rechargez la page dans quelques instants.", true);
}

// --- Page -------------------------------------------------------------------

function show(section) {
  for (const name of SECTIONS) {
    $(`[data-panel="${name}"]`).hidden = name !== section;
    $(`[data-section="${name}"]`).setAttribute("aria-selected", String(name === section));
  }
}

function renderProfile() {
  search ??= draftFromProfile();
  renderSearch();
  renderRules();
  renderScoring();
  renderPro();
  renderList("experiences");
  renderList("education");
  renderDefaults();
}

let firstRender = true;

async function render() {
  const [me, data, opts, chat, fb] = await Promise.all([
    loadMe(),
    api("/profile"),
    api("/options"),
    api("/chat/status").catch(() => ({ enabled: false })),
    api("/feedback").catch(() => ({ new: 0, total: 0, threshold: 3, recent: [] })),
    api("/jobs?brief=true").then((jobs) => (jobNames = Object.fromEntries(jobs.filter((j) => !j.locked)
      .map((j) => [j.id, `${plain(j.company.name)} – ${plain(j.title)}`])))).catch(() => {}),
  ]);
  const { user } = me;
  billing = me.billing;
  feedback = fb;
  profile = data;
  options = opts;
  gemini = chat;
  if (!search?.dirty) search = null; // une saisie en cours est conservée (ex. après une action du chat)
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
    ["Dernier import", last ? `${new Date(last.imported_at).toLocaleDateString("fr-FR")} · ${last.filename ?? last.source}` : "Aucun"],
  ];
  $("#details").innerHTML = html`${rows.map(([k, v]) => html`
    <div class="row-item"><span class="grow muted small">${k}</span><span>${v}</span></div>`)}`.s;

  // L'aperçu d'import en cours est conservé si la page est rafraîchie (ex. après une action du chat)
  if (!$("#import .import-preview")) renderImport();
  renderBilling();
  renderProfile();
  if (firstRender) {
    firstRender = false;
    // Liens directs : #reponses-types (page Candidature), #parcours, #compte
    const hash = location.hash.slice(1);
    show(hash === "reponses-types" ? "candidatures" : SECTIONS.includes(hash) ? hash : "recherche");
    handleCheckoutReturn();
    if (hash === "reponses-types") editDefaults().catch(() => {});
  }
}

$("#sections").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-section]");
  if (!btn) return;
  show(btn.dataset.section);
  history.replaceState(null, "", `#${btn.dataset.section}`);
});

// --- Vos données : suppression du compte (droit à l'effacement) ---------------------------------------
// L'export est un simple lien (/api/me/export, fichier JSON téléchargé).

$("#delete-account").addEventListener("click", async () => {
  const done = await openForm({
    title: "Supprimer mon compte",
    fields: [{ name: "confirm", label: "Tapez SUPPRIMER pour confirmer", required: true, full: true,
               hint: "Définitif : profil, candidatures, retours, notes, conversations et imports sont effacés immédiatement. "
                 + "Les offres et entreprises du radar, partagées, restent." }],
    extra: html`<p class="small muted">Un abonnement en cours doit d'abord être résilié. Pensez à exporter vos données avant.</p>`,
    submitLabel: "Supprimer définitivement",
    onSubmit: async ({ confirm }) => {
      if (confirm.trim().toUpperCase() !== "SUPPRIMER") throw new Error("Tapez SUPPRIMER pour confirmer");
      await api("/me", { method: "DELETE" });
      return true;
    },
  });
  if (done) location.href = "/";
});

boot(render);
