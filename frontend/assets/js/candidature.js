// Mire — page Candidature : formulaire de l'offre pré-rempli, CV adapté, lettre. Tout se relit, se modifie et se
// copie champ par champ. Mire n'envoie RIEN : l'utilisateur ouvre la page de candidature de l'entreprise, colle ses
// réponses, joint les PDF et clique lui-même sur « Envoyer ».

import { $, api, boot, fmtDate, html, logo, pill, plain, raw, refreshCounts, score, toast } from "./app.js";

const jobId = new URLSearchParams(location.search).get("job");
const base = `/jobs/${encodeURIComponent(jobId ?? "")}/application`;
const POLL_MS = 2000;
const POLL_MAX_MS = 120000;
const SAVE_DELAY = 700;
const LETTER_MAX_WORDS = 250;

const ORIGIN = {
  profil: ["Profil", "o-profil", "Repris de ton profil"],
  defaut: ["Réponse type", "o-defaut", "Repris de tes réponses types (Mon profil)"],
  genere: ["Généré", "o-genere", "Proposé par Mire à partir de ton profil : relis-le"],
  a_toi: ["À toi", "o-a_toi", "À remplir toi-même"],
};
const INPUT_TYPES = { text: "text", email: "email", phone: "tel", url: "url", date: "date" };
const FROM_DEFAULTS = new Set(["identite", "contact", "liens", "salaire", "autorisation_travail", "disponibilite"]);
const REGENERABLE = new Set(["question", "autre", "lettre"]);
const QUICK = ["plus court", "plus technique", "en anglais", "en français"];
const SENDABLE = new Set(["nouveau", "a_postuler", "prete"]);
const COPY_ICON = raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>`);
const OUT_ICON = raw(`<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>`);

let draft = null;
let pollTimer = null;
let pollStarted = 0;
const saveTimers = new Map();

const words = (t) => (t ?? "").trim().split(/\s+/).filter(Boolean).length;
const fileUrl = (kind) => `/api${base}/files/${kind}`;
const valueText = (v) => (Array.isArray(v) ? v.join(", ") : v === true ? "Oui" : v ?? "");

// --- En-tête et bandeau --------------------------------------------------------------------------------

function renderHead(job, application) {
  const url = draft?.apply_url;
  const title = plain(job.title) || "Offre réservée au Pro";
  const company = plain(job.company.name);
  $("#cand-head").innerHTML = html`
    <div class="intro">
      <p class="label">// Candidature</p>
      <h1>${title}</h1>
      <div class="cand-meta">
        ${logo(job.company, "sm")}<span>${company}</span>
        ${score(job.score)}
        ${application ? pill(application.status) : ""}
      </div>
    </div>
    <div class="cand-head-actions">
      ${url ? html`<a class="btn" href="${url}" target="_blank" rel="noopener">Ouvrir la page de candidature ${OUT_ICON}</a>` : ""}
      ${draft ? html`<button type="button" class="btn ghost" data-refresh ${draft.status === "en_preparation" ? raw("disabled") : ""}>Tout recharger</button>` : ""}
    </div>`.s;
  document.title = company ? `Candidature · ${company} · Mire` : "Candidature · Mire";
}

function renderBanner() {
  const items = [];
  if (draft.status === "en_preparation") {
    const late = pollStarted && Date.now() - pollStarted > POLL_MAX_MS;
    items.push(html`<div class="cand-banner busy" role="status"><span class="spinner" aria-hidden="true"></span>
      ${late ? "C'est plus long que prévu : rechargez la page dans un instant."
        : "Préparation en cours : lecture du formulaire de l'offre, CV adapté, lettre et réponses…"}</div>`);
  }
  if (draft.form.source === "standard") {
    items.push(html`<div class="cand-banner warn"><strong>Formulaire non lu : questions types.</strong>
      Vérifiez les champs sur la page de candidature de l'entreprise.${draft.form.error ? html` <span class="small muted">(${draft.form.error})</span>` : ""}</div>`);
  } else if (draft.form.source === "html_llm") {
    items.push(html`<div class="cand-banner">Formulaire lu par l'IA sur la page de l'offre : vérifiez qu'il n'y manque rien.</div>`);
  }
  if (draft.low_score) {
    items.push(html`<div class="cand-banner warn">Cette offre est notée ${draft.job.score?.toLocaleString("fr-FR")}/10 pour vous : elle correspond peu à votre profil.</div>`);
  }
  if (draft.job.closed) items.push(html`<div class="cand-banner warn">Cette offre semble fermée sur le site de l'entreprise.</div>`);
  const general = draft.warnings.filter((w) => ["gemini", "pdf", "profil"].includes(w.kind));
  for (const w of general) {
    items.push(html`<div class="cand-banner warn">${w.message}${w.kind === "profil" ? html` <a href="/mon-profil#parcours">Compléter mon profil</a>` : ""}</div>`);
  }
  $("#cand-banner").innerHTML = html`${items}`.s;
}

// --- Formulaire -----------------------------------------------------------------------------------------

function originBadge(a) {
  const [label, cls, title] = ORIGIN[a.origin] ?? ORIGIN.a_toi;
  return html`<span class="origin ${cls}" title="${title}">${label}</span>`;
}

function inputFor(f) {
  const a = f.answer;
  const id = `f-${f.field_id}`;
  const v = a.value;
  const common = raw(`id="${id}" data-input="${f.field_id}" ${f.required ? 'aria-required="true"' : ""}`);
  switch (f.kind) {
    case "textarea": {
      const max = f.max_length ? raw(`maxlength="${Number(f.max_length)}"`) : "";
      return html`<textarea class="input" rows="${Math.min(12, Math.max(3, Math.ceil(String(v ?? "").length / 90)))}" ${common} ${max}>${v ?? ""}</textarea>
        <span class="counter small muted" data-counter="${f.field_id}">${counterText(f, v)}</span>`;
    }
    case "select":
      return html`<select class="input" ${common}><option value="">— Choisir —</option>
        ${f.options.map((o) => html`<option value="${o}" ${raw(o === v ? "selected" : "")}>${o}</option>`)}</select>`;
    case "multiselect": {
      const chosen = new Set(Array.isArray(v) ? v : v ? [v] : []);
      return html`<div class="check-row" id="${id}" data-multi="${f.field_id}">${f.options.map((o) => html`
        <label class="check"><input type="checkbox" value="${o}" ${raw(chosen.has(o) ? "checked" : "")}> ${o}</label>`)}</div>`;
    }
    case "checkbox":
      return html`<label class="check cand-consent"><input type="checkbox" ${common} data-bool ${raw(v === true ? "checked" : "")}>
        Je cocherai cette case moi-même sur le site, après avoir lu le texte</label>`;
    case "file": {
      const kind = f.category === "cv" ? "cv" : f.category === "lettre" ? "lettre" : null;
      const file = kind && draft.files[kind];
      if (file) return html`<a class="btn ghost sm" href="${fileUrl(kind)}" download="${file.filename}">Télécharger ${file.filename}</a>`;
      if (kind && (kind === "cv" ? draft.cv_text : draft.cover_letter)) {
        return html`<p class="small muted">PDF en cours de génération… copiez le texte en attendant.</p>`;
      }
      return html`<input class="input" type="text" ${common} value="${v ?? ""}" placeholder="Je joins mon propre fichier (nom du fichier)">`;
    }
    default: {
      const max = f.max_length ? raw(`maxlength="${Number(f.max_length)}"`) : "";
      return html`<input class="input" type="${INPUT_TYPES[f.kind] ?? "text"}" ${common} ${max} value="${typeof v === "string" ? v : ""}">`;
    }
  }
}

function counterText(f, v) {
  const text = String(v ?? "");
  if (f.max_length) return `${text.length} / ${f.max_length} caractères`;
  return text ? `${words(text)} mots` : "";
}

function warningLine(f) {
  const a = f.answer;
  const text = a.warning ?? (a.origin === "a_toi" && !a.edited ? (f.required ? "Obligatoire : à remplir toi-même" : "À remplir toi-même si tu le souhaites") : null);
  if (!text) return "";
  const link = FROM_DEFAULTS.has(f.category) && a.origin === "a_toi"
    ? html` <a href="/mon-profil#reponses-types">Compléter dans mon profil</a>` : "";
  return html`<p class="cand-warn">${text}${link}</p>`;
}

function fieldBlock(f) {
  const a = f.answer;
  const copyable = !["checkbox", "file"].includes(f.kind);
  const regen = REGENERABLE.has(f.category) && ["text", "textarea", "select"].includes(f.kind);
  const sources = (a.sources ?? []).filter((s) => draft.sources[s]);
  return html`
    <div class="cand-field ${a.origin === "a_toi" && !a.edited && f.required ? "is-todo" : ""}" id="field-${f.field_id}" data-field="${f.field_id}">
      <div class="cand-field-head">
        <label for="f-${f.field_id}">${f.label}${f.required ? html`<span class="req" title="Obligatoire"> *</span>` : ""}</label>
        <span class="cand-badges">${originBadge(a)}${a.edited ? html`<span class="origin o-edited">Modifié</span>` : ""}</span>
      </div>
      ${f.description ? html`<p class="small muted">${f.description}</p>` : ""}
      ${inputFor(f)}
      ${warningLine(f)}
      <div class="cand-tools">
        ${copyable ? html`<button type="button" class="btn ghost sm" data-copy="${f.field_id}">${COPY_ICON} Copier</button>` : ""}
        ${regen ? html`<button type="button" class="btn ghost sm" data-regen="${f.field_id}">Régénérer</button>` : ""}
        <span class="small muted" data-saved="${f.field_id}"></span>
      </div>
      <div class="regen" data-regen-form="${f.field_id}" hidden></div>
      ${sources.length ? html`<details class="cand-sources"><summary>Sur quoi c'est basé</summary>
        <ul>${sources.map((s) => html`<li>${draft.sources[s]}</li>`)}</ul></details>` : ""}
    </div>`;
}

function renderForm() {
  if (draft.status === "en_preparation" && !draft.fields.length) {
    $("#cand-form").innerHTML = html`<div class="section-head"><div class="intro"><p class="label">// Formulaire</p>
      <h2>Lecture du formulaire…</h2></div></div><p class="muted small">Les champs apparaîtront dans l'ordre du site de l'entreprise.</p>`.s;
    return;
  }
  $("#cand-form").innerHTML = html`
    <div class="section-head"><div class="intro"><p class="label">// Formulaire</p><h2>Dans l'ordre du site</h2></div></div>
    <p class="small muted">Relisez, modifiez si besoin (enregistré automatiquement), puis copiez chaque réponse dans le formulaire de
      l'entreprise. Les cases de consentement et les questions personnelles restent à vous.</p>
    <div class="cand-fields">${draft.fields.map(fieldBlock)}</div>`.s;
}

// --- CV adapté ------------------------------------------------------------------------------------------

/** Texte du CV (« ## » sections, « ### » postes, « - » puces) -> aperçu lisible. */
function cvPreview(text) {
  const lines = (text ?? "").split("\n");
  const [name = "", headline = "", contact = ""] = lines;
  const out = [html`<p class="cv-name">${name}</p>`, headline ? html`<p class="cv-headline">${headline}</p>` : "",
    contact ? html`<p class="cv-contact">${contact}</p>` : ""];
  let list = [];
  const flush = () => { if (list.length) { out.push(html`<ul>${list}</ul>`); list = []; } };
  for (const raw0 of lines.slice(3)) {
    const line = raw0.trim();
    if (line.startsWith("- ")) { list.push(html`<li>${line.slice(2)}</li>`); continue; }
    flush();
    if (!line) continue;
    if (line.startsWith("### ")) out.push(html`<p class="cv-role">${line.slice(4)}</p>`);
    else if (line.startsWith("## ")) out.push(html`<p class="cv-section">${line.slice(3)}</p>`);
    else out.push(html`<p>${line}</p>`);
  }
  flush();
  return html`${out}`;
}

function comparison(cv) {
  const c = cv?.comparison;
  if (!c) return "";
  const tags = (list, cls = "") => list.length ? html`<div class="tags">${list.map((s) => html`<span class="tag ${cls}">${s}</span>`)}</div>` : html`<span class="small muted">—</span>`;
  return html`<details class="cand-compare"><summary>Comparer avec mon profil</summary>
    <div class="cand-compare-body">
      <p class="small"><strong>Mis en avant</strong></p>
      ${tags(c.put_forward.experiences)}${tags(c.put_forward.skills, "have")}
      <p class="small"><strong>Reformulé</strong></p>
      ${c.reformulated.length ? html`<ul class="small">${c.reformulated.map((r) => html`<li>${r.text}<br><span class="muted">← ${r.sources.join(" · ")}</span></li>`)}</ul>` : html`<span class="small muted">—</span>`}
      <p class="small"><strong>Retiré pour cette offre</strong></p>
      ${tags([...c.removed.experiences, ...c.removed.skills])}
      ${c.removed.bullets.length ? html`<ul class="small muted">${c.removed.bullets.map((b) => html`<li>${b}</li>`)}</ul>` : ""}
    </div></details>`;
}

function renderCv() {
  const box = $("#cand-cv");
  const retired = draft.warnings.filter((w) => w.kind === "cv_retire");
  const suggestions = draft.cv?.suggestions ?? [];
  const head = html`<div class="section-head"><div class="intro"><p class="label">// CV adapté</p><h2>À l'offre et à l'entreprise</h2></div></div>`;
  if (draft.status === "en_preparation") {
    box.innerHTML = html`${head}<p class="muted small">Rédaction du CV à partir de votre profil…</p>`.s;
    return;
  }
  if (!draft.cv_text) {
    const why = draft.warnings.find((w) => w.kind === "profil");
    box.innerHTML = html`${head}<p class="small">${why ? why.message : "Pas de CV adapté pour cette candidature."}</p>
      <p><a class="btn ghost sm" href="/mon-profil#parcours">Compléter mon profil</a></p>
      ${retired.length ? html`<ul class="cand-warn-list">${retired.map((w) => html`<li>${w.message}</li>`)}</ul>` : ""}`.s;
    return;
  }
  const file = draft.files.cv;
  box.innerHTML = html`${head}
    <div class="cand-tools">
      ${file ? html`<a class="btn sm" href="${fileUrl("cv")}" download="${file.filename}">Télécharger le PDF</a>`
        : html`<span class="small muted">PDF en cours de génération…</span>`}
      <button type="button" class="btn ghost sm" data-copy-cv>${COPY_ICON} Copier le texte</button>
      <button type="button" class="btn ghost sm" data-edit-cv aria-pressed="false">Modifier le texte</button>
    </div>
    <div class="cv-paper" data-cv-preview>${cvPreview(draft.cv_text)}</div>
    <div data-cv-editor hidden>
      <textarea class="input cv-text" rows="18" data-cv-text aria-label="Texte du CV">${draft.cv_text}</textarea>
      <p class="small muted">« ## » : section, « ### » : poste, « - » : puce. Enregistré automatiquement ; le PDF est refait.
        <span data-saved="cv"></span></p>
    </div>
    ${comparison(draft.cv)}
    ${retired.length ? html`<div class="cand-banner warn"><strong>Retiré par le contrôle (absent de votre profil) :</strong>
      <ul class="cand-warn-list">${retired.map((w) => html`<li>${w.message}</li>`)}</ul></div>` : ""}
    ${suggestions.length ? html`<div class="cand-banner"><strong>Compétences demandées absentes de votre profil :</strong>
      <ul class="cand-warn-list">${suggestions.map((s) => html`<li>${s}</li>`)}</ul>
      <a class="small" href="/mon-profil#parcours">Compléter mon profil</a></div>` : ""}`.s;
}

// --- Lettre ----------------------------------------------------------------------------------------------

function renderLetter() {
  const box = $("#cand-letter");
  const head = html`<div class="section-head"><div class="intro"><p class="label">// Lettre</p><h2>Lettre de motivation</h2></div></div>`;
  const warnings = draft.warnings.filter((w) => w.kind === "lettre");
  if (draft.status === "en_preparation") {
    box.hidden = !draft.want_letter && !draft.fields.some((f) => f.category === "lettre");
    box.innerHTML = html`${head}<p class="muted small">Rédaction…</p>`.s;
    return;
  }
  box.hidden = false;
  if (!draft.cover_letter) {
    box.innerHTML = html`${head}<p class="small muted">Le formulaire de cette offre ne demande pas de lettre.</p>
      <button type="button" class="btn ghost sm" data-want-letter>Rédiger une lettre quand même</button>
      <span class="small muted">(compte comme une préparation du jour)</span>`.s;
    return;
  }
  const file = draft.files.lettre;
  box.innerHTML = html`${head}
    <textarea class="input letter-text" rows="14" data-letter aria-label="Lettre de motivation">${draft.cover_letter}</textarea>
    <p class="small muted"><span data-letter-count>${words(draft.cover_letter)}</span> / ${LETTER_MAX_WORDS} mots · enregistré automatiquement
      <span data-saved="letter"></span></p>
    <div class="cand-tools">
      <button type="button" class="btn ghost sm" data-copy-letter>${COPY_ICON} Copier</button>
      ${file ? html`<a class="btn ghost sm" href="${fileUrl("lettre")}" download="${file.filename}">Télécharger le PDF</a>`
        : html`<span class="small muted">PDF en cours de génération…</span>`}
      <button type="button" class="btn ghost sm" data-regen="cover_letter">Régénérer</button>
    </div>
    <div class="regen" data-regen-form="cover_letter" hidden></div>
    ${warnings.length ? html`<ul class="cand-warn-list">${warnings.map((w) => html`<li>${w.message}</li>`)}</ul>` : ""}`.s;
}

// --- Bas de page : prête, envoyée --------------------------------------------------------------------------

function renderActions() {
  const box = $("#cand-actions");
  const status = draft.application.status;
  if (!SENDABLE.has(status)) {
    box.innerHTML = html`<p><strong>Candidature envoyée${draft.application.applied_on ? ` le ${fmtDate(draft.application.applied_on)}` : ""}.</strong>
      Ce que vous avez envoyé est gardé dans l'historique du process.</p>
      <a class="btn ghost sm" href="/mes-process">Voir mes candidatures</a>`.s;
    return;
  }
  const busy = draft.status === "en_preparation";
  const missing = draft.missing ?? [];
  box.innerHTML = html`
    <p class="small muted">Mire n'envoie rien : ouvrez la page de candidature, collez chaque réponse, joignez les PDF et cliquez
      vous-même sur « Envoyer » sur le site de l'entreprise.</p>
    ${missing.length && !busy ? html`<div class="cand-missing"><p class="small"><strong>À compléter avant de marquer prête :</strong></p>
      <ul>${missing.map((m) => html`<li><a href="#field-${m.field_id}">${m.label}</a></li>`)}</ul></div>` : ""}
    <div class="cand-tools">
      ${status === "prete" ? html`<span class="pill st-prete">Prête à envoyer</span>` : html`
      <button type="button" class="btn" data-ready ${raw(busy || missing.length ? "disabled" : "")}>Marquer prête à envoyer</button>`}
      <button type="button" class="btn ${status === "prete" ? "" : "ghost"}" data-sent ${raw(busy ? "disabled" : "")}>J'ai envoyé ma candidature</button>
    </div>`.s;
}

function renderAll() {
  renderHead(draft.job, draft.application);
  renderBanner();
  renderForm();
  renderCv();
  renderLetter();
  renderActions();
}

// --- Données ----------------------------------------------------------------------------------------------

function schedulePoll() {
  clearTimeout(pollTimer);
  if (draft?.status !== "en_preparation") return;
  pollStarted ||= Date.now();
  if (Date.now() - pollStarted > POLL_MAX_MS) { renderBanner(); return; }
  pollTimer = setTimeout(async () => {
    if (!document.body.contains($("#cand-form"))) return; // page quittée
    try {
      draft = await api(`${base}/draft`);
    } catch { /* nouvel essai au prochain tour */ }
    if (draft.status !== "en_preparation") {
      pollStarted = 0;
      renderAll();
      refreshCounts();
      if (draft.files && (draft.cv_text && !draft.files.cv)) setTimeout(reloadQuietly, 4000); // PDF juste après
    } else {
      renderBanner();
    }
    schedulePoll();
  }, POLL_MS);
}

async function reloadQuietly() {
  if (!document.body.contains($("#cand-form"))) return;
  try {
    const fresh = await api(`${base}/draft`);
    const changed = JSON.stringify(fresh.files) !== JSON.stringify(draft.files);
    draft = fresh;
    if (changed) { renderForm(); renderCv(); renderLetter(); }
  } catch { /* rien */ }
}

async function render() {
  if (!jobId) {
    $("#cand-head").innerHTML = html`<div class="intro"><p class="label">// Candidature</p><h1>Aucune offre choisie</h1>
      <p class="muted">Ouvrez une offre dans <a href="/postes">Offres</a> puis « Préparer ma candidature ».</p></div>`.s;
    return;
  }
  try {
    draft = await api(`${base}/draft`);
  } catch (err) {
    if (!/Aucune candidature/.test(err.message)) throw err;
    const job = await api(`/jobs/${encodeURIComponent(jobId)}`);
    draft = null;
    renderHead(job, job.application);
    $("#cand-banner").innerHTML = "";
    $("#cand-form").innerHTML = html`<p>Cette candidature n'est pas encore préparée.</p>
      <p class="small muted">Mire lit le formulaire de l'offre et le pré-remplit avec votre profil : CV adapté, lettre si demandée,
        réponses aux questions. Vous relisez, puis vous envoyez vous-même.</p>
      <button type="button" class="btn" data-prepare="${job.id}" data-score="${typeof job.score === "number" ? job.score : ""}">Préparer ma candidature</button>`.s;
    for (const id of ["#cand-cv", "#cand-letter", "#cand-actions"]) $(id).hidden = true;
    return;
  }
  for (const id of ["#cand-cv", "#cand-letter", "#cand-actions"]) $(id).hidden = false;
  renderAll();
  schedulePoll();
}

// --- Enregistrement automatique ------------------------------------------------------------------------

function currentValue(fieldId) {
  const multi = $(`[data-multi="${CSS.escape(fieldId)}"]`);
  if (multi) {
    const values = [...multi.querySelectorAll("input:checked")].map((i) => i.value);
    return values.length ? values : null;
  }
  const el = $(`[data-input="${CSS.escape(fieldId)}"]`);
  if (!el) return null;
  if (el.hasAttribute("data-bool")) return el.checked ? true : null;
  return el.value === "" ? null : el.value;
}

function markSaved(key, text) {
  const el = $(`[data-saved="${CSS.escape(key)}"]`);
  if (el) el.textContent = text;
}

function queueSave(key, body) {
  clearTimeout(saveTimers.get(key));
  markSaved(key, "…");
  saveTimers.set(key, setTimeout(async () => {
    try {
      draft = await api(`${base}/draft`, { method: "PATCH", body });
      markSaved(key, "Enregistré");
      refreshAfterSave(key);
    } catch (err) {
      markSaved(key, "");
      if (!err.handled) toast(err.message, true);
    }
  }, SAVE_DELAY));
}

/** Après un enregistrement : badges du champ et bas de page, sans redessiner les champs en cours de saisie. */
function refreshAfterSave(key) {
  const f = draft.fields.find((x) => x.field_id === key);
  const block = f && $(`[data-field="${CSS.escape(key)}"]`);
  if (block) {
    $(".cand-badges", block).innerHTML = html`${originBadge(f.answer)}${f.answer.edited ? html`<span class="origin o-edited">Modifié</span>` : ""}`.s;
    $(".cand-warn", block)?.remove();
    block.classList.toggle("is-todo", f.answer.origin === "a_toi" && !f.answer.edited && f.required);
  }
  if (key === "cv") $("[data-cv-preview]").innerHTML = cvPreview(draft.cv_text).s;
  // Champs « coller le texte » du formulaire : ils suivent le CV / la lettre (la saisie en cours n'est pas dans le formulaire)
  if (key === "cv" || key === "letter") renderForm();
  renderActions();
}

function onFieldInput(e) {
  const multi = e.target.closest("[data-multi]");
  const input = e.target.closest("[data-input]");
  const fieldId = multi?.dataset.multi ?? input?.dataset.input;
  if (!fieldId) return;
  const f = draft.fields.find((x) => x.field_id === fieldId);
  const counter = $(`[data-counter="${CSS.escape(fieldId)}"]`);
  if (counter && f) counter.textContent = counterText(f, input?.value);
  queueSave(fieldId, { answers: [{ field_id: fieldId, value: currentValue(fieldId) }] });
}

// --- Copier, régénérer ---------------------------------------------------------------------------------------

async function copy(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    toast("Copie impossible : sélectionnez le texte et copiez-le à la main.", true);
    return;
  }
  const previous = btn.innerHTML;
  btn.textContent = "Copié";
  btn.classList.add("is-copied");
  setTimeout(() => { btn.innerHTML = previous; btn.classList.remove("is-copied"); }, 1500);
}

function openRegen(fieldId) {
  const box = $(`[data-regen-form="${CSS.escape(fieldId)}"]`);
  if (!box) return;
  if (!box.hidden) { box.hidden = true; return; }
  const left = draft.remaining?.regenerate;
  box.innerHTML = html`
    <div class="chip-row">${QUICK.map((q) => html`<button type="button" class="chip" data-instr="${q}">${q}</button>`)}</div>
    <div class="regen-row">
      <input class="input" maxlength="200" placeholder="Consigne courte (facultative)" aria-label="Consigne pour régénérer" data-instr-input>
      <button type="button" class="btn sm" data-regen-go="${fieldId}">Régénérer</button>
    </div>
    ${left != null ? html`<p class="small muted">${left} régénération${left > 1 ? "s" : ""} restante${left > 1 ? "s" : ""} aujourd'hui.</p>` : ""}`.s;
  box.hidden = false;
  $("[data-instr-input]", box).focus();
}

async function regenerate(fieldId, instruction, btn) {
  btn.disabled = true;
  btn.textContent = "…";
  try {
    draft = await api(`${base}/draft/fields/${encodeURIComponent(fieldId)}/regenerate`, { method: "POST", body: { instruction } });
    if (fieldId === "cover_letter") { renderLetter(); renderForm(); } else renderForm();
    renderActions();
    toast("Nouvelle proposition : relisez-la");
  } catch (err) {
    if (!err.handled) toast(err.message, true);
    btn.disabled = false;
    btn.textContent = "Régénérer";
  }
}

// --- Écouteurs (sur <main> : la page peut être quittée sans rechargement) ------------------------------------

function wire() {
  const main = $("main");
  if (main.dataset.wired) return;
  main.dataset.wired = "1";
  main.addEventListener("input", (e) => {
    if (e.target.closest("#cand-form")) return onFieldInput(e);
    if (e.target.matches("[data-cv-text]")) return queueSave("cv", { cv_text: e.target.value });
    if (e.target.matches("[data-letter]")) {
      $("[data-letter-count]").textContent = words(e.target.value);
      return queueSave("letter", { cover_letter: e.target.value });
    }
  });
  main.addEventListener("change", (e) => {
    if (e.target.closest("#cand-form") && (e.target.matches("select, input[type=checkbox], input[type=date]"))) onFieldInput(e);
  });
  main.addEventListener("click", async (e) => {
    const t = e.target;
    if (t.closest("[data-copy]")) {
      const id = t.closest("[data-copy]").dataset.copy;
      return copy(valueText(currentValue(id)), t.closest("[data-copy]"));
    }
    if (t.closest("[data-copy-cv]")) return copy($("[data-cv-text]")?.value ?? draft.cv_text ?? "", t.closest("[data-copy-cv]"));
    if (t.closest("[data-copy-letter]")) return copy($("[data-letter]")?.value ?? "", t.closest("[data-copy-letter]"));
    if (t.closest("[data-edit-cv]")) {
      const btn = t.closest("[data-edit-cv]");
      const editor = $("[data-cv-editor]");
      editor.hidden = !editor.hidden;
      btn.setAttribute("aria-pressed", String(!editor.hidden));
      btn.textContent = editor.hidden ? "Modifier le texte" : "Terminer";
      if (!editor.hidden) $("[data-cv-text]").focus();
      return;
    }
    if (t.closest("[data-regen]")) return openRegen(t.closest("[data-regen]").dataset.regen);
    if (t.closest("[data-instr]")) {
      const box = t.closest(".regen");
      $("[data-instr-input]", box).value = t.closest("[data-instr]").dataset.instr;
      return;
    }
    if (t.closest("[data-regen-go]")) {
      const btn = t.closest("[data-regen-go]");
      return regenerate(btn.dataset.regenGo, $("[data-instr-input]", btn.closest(".regen")).value.trim(), btn);
    }
    if (t.closest("[data-refresh]")) {
      const left = draft?.remaining?.prepare;
      if (!window.confirm(`Relire le formulaire de l'offre et tout régénérer ? Vos modifications seront remplacées.${left != null ? ` Compte comme une préparation du jour (${left} restante${left > 1 ? "s" : ""}).` : ""}`)) return;
      return startPreparation({ refresh: true });
    }
    if (t.closest("[data-want-letter]")) {
      if (!window.confirm("Rédiger une lettre ? La candidature est préparée à nouveau (une préparation du jour) ; vos modifications seront remplacées.")) return;
      return startPreparation({ want_letter: true });
    }
    if (t.closest("[data-ready]")) {
      const btn = t.closest("[data-ready]");
      btn.disabled = true;
      try {
        draft = await api(`${base}/ready`, { method: "POST" });
        toast("Prête à envoyer : ouvrez la page de candidature et collez vos réponses");
        renderAll();
        refreshCounts();
      } catch (err) {
        if (!err.handled) toast(err.message, true);
        btn.disabled = false;
      }
      return;
    }
    if (t.closest("[data-sent]")) {
      if (!window.confirm("Vous avez bien envoyé la candidature sur le site de l'entreprise ? Le process passe en « Postulé » "
        + "et ce que vous avez envoyé est gardé dans son historique.")) return;
      try {
        draft = await api(`${base}/sent`, { method: "POST" });
        toast("Candidature enregistrée comme envoyée. Bonne chance !");
        renderAll();
        refreshCounts();
      } catch (err) {
        if (!err.handled) toast(err.message, true);
      }
    }
  });
}

async function startPreparation(body) {
  try {
    draft = await api(`${base}/prepare`, { method: "POST", body });
  } catch (err) {
    if (!err.handled) toast(err.message, true);
    return;
  }
  pollStarted = Date.now();
  renderAll();
  schedulePoll();
}

wire();
boot(render);
