// Démarrage : la première liste utile en deux minutes, sur la page Offres, pour un profil encore vide.
// 1. CV (PDF, lu par Gemini) ou export LinkedIn (ZIP), facultatif : un résumé de ce qui a été lu, à valider.
// 2. Trois questions, préremplies par le CV : métiers visés, où (villes et mode), salaire minimum.
// « Voir mes offres » enregistre le tout ; la liste se reclasse aussitôt (ranking.py).
// Les préférences lues dans le CV ne sont pas appliquées telles quelles : les réponses aux questions les remplacent.
import { api, attempt, html, toast } from "./app.js";

const LATER_KEY = "mire.demarrage.plus-tard";
const WORK_MODES = [["remote", "Remote"], ["hybride", "Hybride"], ["sur_site", "Sur site"]];

const norm = (s) => String(s ?? "").trim().toLowerCase();
const has = (list, v) => list.some((x) => norm(x) === norm(v));
const splitList = (v) => String(v ?? "").split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

function later() {
  try { return localStorage.getItem(LATER_KEY) === "1"; } catch { return false; }
}
function setLater() {
  try { localStorage.setItem(LATER_KEY, "1"); } catch { /* navigation privée : le panneau reviendra */ }
}

/** Profil jamais rempli (ni métier, ni compétence, ni import) et démarrage pas remis à plus tard ; ou ?demarrage=1. */
export function needsStart(profile) {
  if (new URLSearchParams(location.search).get("demarrage") === "1") return true;
  return !profile.preferences.roles.length && !profile.skills.length && !profile.raw_imports.length && !later();
}

/** Ce que l'import a lu, en une ligne : « Titre, 4 expériences, 18 compétences ». */
function summary(items) {
  const count = (field) => items.filter((i) => i.field === field && i.op !== "update").length;
  const parts = [
    items.some((i) => i.field === "headline") && "votre titre",
    count("experiences") && plural(count("experiences"), "expérience", "expériences"),
    count("education") && plural(count("education"), "formation", "formations"),
    count("skills") && plural(count("skills"), "compétence", "compétences"),
    count("languages") && plural(count("languages"), "langue", "langues"),
    items.some((i) => i.field === "years_experience") && "vos années d'expérience",
  ].filter(Boolean);
  return parts.join(", ");
}

/**
 * Affiche le panneau dans `box`. options : réponse de GET /api/options ; ai : Gemini disponible (lecture du PDF).
 * onDone(profile) : appelé après l'enregistrement (la page recharge et reclasse la liste).
 */
export function drawStart(box, { profile, options, ai, onDone, onLater }) {
  const p0 = profile.preferences;
  const state = {
    step: 1, busy: "", proposal: null, keep: true,
    roles: [...p0.roles], locations: p0.locations.length ? [...p0.locations] : profile.location ? [profile.location] : [],
    modes: [...p0.work_modes], salary: p0.min_salary_keur ?? "",
  };

  /** Préremplit les questions avec ce que le CV a lu (sans écraser une réponse déjà donnée). */
  function prefill(items) {
    const values = (field) => items.filter((i) => i.field === field).map((i) => i.value);
    for (const r of values("preferences.roles")) if (!has(state.roles, r)) state.roles.push(r);
    const places = [...values("preferences.locations"), ...values("location")].filter((v) => typeof v === "string");
    if (!state.locations.length) state.locations = [...new Set(places)].slice(0, 3);
    for (const m of values("preferences.work_modes")) if (!state.modes.includes(m)) state.modes.push(m);
    const salary = values("preferences.min_salary_keur")[0];
    if (state.salary === "" && salary != null) state.salary = String(salary);
  }

  function familyChips() {
    const groups = new Map();
    for (const f of options.families) groups.set(f.group, [...(groups.get(f.group) ?? []), f.label]);
    const known = options.families.map((f) => f.label);
    const others = state.roles.filter((r) => !has(known, r));
    const chip = (label) => html`<button type="button" class="chip pick" data-role="${label}"
      aria-pressed="${has(state.roles, label)}">${label}</button>`;
    return html`${[...groups].map(([group, labels]) => html`<div class="pick-row">
        <span class="small muted pick-group">${group}</span><div class="chip-row">${labels.map(chip)}</div></div>`)}
      ${others.length ? html`<div class="pick-row"><span class="small muted pick-group">Autres</span>
        <div class="chip-row">${others.map(chip)}</div></div>` : ""}`;
  }

  function stepCv() {
    const p = state.proposal;
    if (p) {
      const read = summary(p.items);
      return html`<div class="start-step">
        <p><strong>CV lu${p.filename ? html` · <span class="muted">${p.filename}</span>` : ""}.</strong>
          ${read ? html`Mire a trouvé ${read}.` : "Rien de nouveau à ajouter à votre profil."}</p>
        ${read ? html`<label class="check small"><input type="checkbox" data-keep ${state.keep ? "checked" : ""}>
          Ajouter ces éléments à mon profil (modifiables ensuite dans Profil)</label>` : ""}
      </div>`;
    }
    return html`<div class="start-step">
      <p class="small muted">Mire en tire vos compétences et votre expérience : c'est ce qui classe le mieux les offres.
        Le fichier n'est pas conservé.</p>
      <div class="import-actions">
        <label class="btn ${ai ? "" : "disabled"}">Déposer mon CV (PDF)
          <input type="file" accept=".pdf,application/pdf" data-import="pdf" hidden ${ai && !state.busy ? "" : "disabled"}></label>
        <label class="btn ghost">Export LinkedIn (ZIP)
          <input type="file" accept=".zip,application/zip" data-import="linkedin-export" hidden ${state.busy ? "disabled" : ""}></label>
        <button type="button" class="btn ghost" data-skip ${state.busy ? "disabled" : ""}>Je n'ai pas de CV sous la main</button>
      </div>
      ${state.busy ? html`<p class="small" role="status">${state.busy}</p>` : ""}
    </div>`;
  }

  function stepQuestions() {
    return html`<div class="start-step search-form">
      <fieldset class="field"><legend>1. Quel métier cherchez-vous ?</legend>${familyChips()}</fieldset>
      <fieldset class="field"><legend>2. Où ?</legend>
        <input class="input" name="locations" list="start-locations" maxlength="300" value="${state.locations.join(", ")}"
          placeholder="Ex. Paris, Lyon" aria-label="Villes, séparées par des virgules">
        <datalist id="start-locations">${options.locations.map((l) => html`<option value="${l}"></option>`)}</datalist>
        <div class="chip-row" style="margin-top:8px">${WORK_MODES.map(([key, label]) => html`
          <button type="button" class="chip pick" data-mode="${key}" aria-pressed="${state.modes.includes(key)}">${label}</button>`)}</div>
      </fieldset>
      <label class="field">3. Salaire minimum, en k€ brut par an (facultatif)
        <input class="input" type="number" name="salary" min="0" max="1000" value="${state.salary}" style="max-width:160px"></label>
    </div>`;
  }

  function draw() {
    box.innerHTML = html`<section class="card start" aria-labelledby="start-title">
      <div class="start-head">
        <h2 id="start-title">Votre première liste en deux minutes</h2>
        <button type="button" class="link-btn small" data-later>Plus tard</button>
      </div>
      <ol class="start-steps small">
        <li class="${state.step === 1 ? "on" : "done"}">Votre CV</li>
        <li class="${state.step === 2 ? "on" : ""}">Trois questions</li>
        <li>Vos offres, classées</li>
      </ol>
      ${state.step === 1 ? stepCv() : ""}
      ${state.step === 2 ? html`${state.proposal ? stepCv() : ""}${stepQuestions()}
        <div class="import-actions">
          <button type="button" class="btn" data-done ${state.busy ? "disabled" : ""}>Voir mes offres</button>
          ${state.busy ? html`<span class="small" role="status">${state.busy}</span>` : ""}
        </div>` : ""}
    </section>`.s;
  }

  async function upload(input) {
    const file = input.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    state.busy = input.dataset.import === "pdf" ? "Lecture de votre CV… (10 à 20 secondes)" : "Lecture de l'archive…";
    draw();
    try {
      state.proposal = await attempt(() => api(`/profile/import/${input.dataset.import}`, { method: "POST", form }));
      prefill(state.proposal.items);
      state.step = 2;
    } catch { /* erreur déjà affichée : l'utilisateur peut réessayer ou passer */ }
    state.busy = "";
    draw();
  }

  async function finish() {
    state.locations = splitList(box.querySelector('input[name="locations"]')?.value);
    state.salary = box.querySelector('input[name="salary"]')?.value ?? "";
    if (!state.roles.length) return toast("Choisissez au moins un métier : c'est le critère qui compte le plus.", true);
    state.busy = "Enregistrement…";
    draw();
    try {
      let current = profile;
      const p = state.proposal;
      // Les préférences lues dans le CV sont remplacées par les réponses aux questions
      const accept = p && state.keep
        ? p.items.filter((i) => i.checked && !i.field.startsWith("preferences.")).map((i) => i.id) : [];
      if (accept.length) {
        current = await attempt(() => api(`/profile/proposals/${p.import_id}/apply`, { method: "POST", body: { accept } }));
      } else if (p) {
        api(`/profile/proposals/${p.import_id}`, { method: "DELETE" }).catch(() => {});
      }
      const salary = state.salary === "" ? null : Number(state.salary);
      current = await attempt(() => api("/profile", { method: "PATCH", body: { preferences: {
        ...current.preferences, roles: state.roles, locations: state.locations, work_modes: state.modes,
        min_salary_keur: salary,
      } } }));
      box.innerHTML = "";
      await onDone(current);
    } catch {
      state.busy = "";
      draw();
    }
  }

  box.onchange = (e) => {
    const input = e.target.closest("[data-import]");
    if (input) return upload(input);
    if (e.target.matches("[data-keep]")) state.keep = e.target.checked;
  };
  box.oninput = (e) => {
    if (e.target.name === "salary") state.salary = e.target.value;
    if (e.target.name === "locations") state.locations = splitList(e.target.value);
  };
  box.onclick = (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.matches("[data-later]")) {
      setLater();
      box.innerHTML = "";
      return onLater?.();
    }
    if (t.matches("[data-skip]")) { state.step = 2; return draw(); }
    if (t.matches("[data-done]")) return finish();
    if (t.dataset.role) {
      const v = t.dataset.role;
      state.roles = has(state.roles, v) ? state.roles.filter((r) => norm(r) !== norm(v)) : [...state.roles, v];
    } else if (t.dataset.mode) {
      const m = t.dataset.mode;
      state.modes = state.modes.includes(m) ? state.modes.filter((x) => x !== m) : [...state.modes, m];
    } else return;
    t.setAttribute("aria-pressed", String(t.getAttribute("aria-pressed") !== "true"));
  };
  box.onkeydown = (e) => {
    if (e.key === "Enter" && e.target.matches("input")) { e.preventDefault(); finish(); }
  };

  draw();
}
