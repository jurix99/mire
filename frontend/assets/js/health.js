// Santé du moteur, pertinence par profil et liste fermée : page Sources, administrateurs.
import { $, api, html } from "./app.js";

const pct = (x) => `${Math.round(x * 100)} %`;

const RUN_LABEL = { collecte: "Collecte (ATS)", decouverte: "Découverte (levées)", recherche: "Recherche (offres)",
  offres: "Offres hors ATS" };

function dayBars(values, days) {
  const max = Math.max(1, ...values);
  return html`<div class="daybars" role="img" aria-label="${values.join(", ")}">${values.map((v, i) => html`
    <span class="${v ? "" : "zero"}" style="height:${Math.max(4, (v / max) * 100)}%" title="${days[i]} : ${v}"></span>`)}</div>`;
}

async function renderEngine() {
  const e = await api("/admin/engine-health").catch(() => null);
  if (!e) return;
  const box = $("#m-engine");
  box.hidden = false;
  const sum = (a) => a.reduce((x, y) => x + y, 0);
  const ev = e.evaluations, q = e.file, r = e.retours;
  const hours = (h) => (h == null ? "–" : h < 48 ? `${h} h` : `${Math.round(h / 24)} j`);
  box.innerHTML = html`
    <div class="section-head"><h2>Santé du moteur</h2><span class="small muted">${e.fenetre_jours} derniers jours · administrateurs</span></div>
    ${e.alertes.length
      ? html`<ul class="engine-alerts">${e.alertes.map((a) => html`<li class="small">${a}</li>`)}</ul>`
      : html`<p class="small engine-ok">Aucune alerte.</p>`}
    <div class="engine-grid">
      <div><p class="small strong">Offres ajoutées : ${sum(e.offres.ajoutees)}</p>${dayBars(e.offres.ajoutees, e.jours)}
        <p class="small muted">${sum(e.offres.fermees)} fermées · ${e.offres.ouvertes} ouvertes</p></div>
      <div><p class="small strong">Évaluations : ${ev.total}</p>${dayBars(ev.par_jour, e.jours)}
        <p class="small muted">Bascule sur un modèle de secours : ${ev.part_bascule == null ? "–" : pct(ev.part_bascule)}
          · délai médian ${hours(ev.delai_median_h)}</p></div>
      <div><p class="small strong">File et étapes en attente</p>
        <p class="small">${q.en_attente} tâche(s) en file (plus ancienne : ${hours(q.plus_ancienne_h)}) · ${q.echecs_24h} échec(s) en 24 h</p>
        <p class="small">${q.embeddings_en_attente} offre(s) sans embedding · ${q.a_classer} à classer</p></div>
      <div><p class="small strong">Retours des utilisateurs</p>
        <p class="small">${r.jaime} « J'aime » · ${r.pas_interesse} « Pas intéressé »${r.part_jaime == null ? "" : ` (${pct(r.part_jaime)} de J'aime)`}</p>
        <p class="small muted">${Object.entries(r.raisons).map(([k, n]) => `${k} ${n}`).join(" · ") || "Aucune raison donnée"}</p></div>
    </div>
    <div class="rows" style="margin-top:12px">${Object.entries(e.passages).map(([kind, run]) => html`
      <div class="row-item"><span class="grow">${RUN_LABEL[kind] ?? kind}</span>
        <span class="small muted">${run ? `${run.statut} · il y a ${hours(run.il_y_a_h)} · ${run.offres_ajoutees} offre(s) · ${run.entreprises} entreprise(s)` : "jamais"}</span></div>`)}</div>
    <p class="small muted">Modèles : ${Object.entries(ev.par_modele).map(([m, n]) => `${m} ${n}`).join(" · ") || "–"} (nominal : ${ev.modele_nominal ?? "–"})</p>`.s;
}

async function renderBeta() {
  const b = await api("/admin/beta").catch(() => null);
  if (!b || !b.utilisateurs.length) return;
  const box = $("#m-beta");
  box.hidden = false;
  const p = (x) => (x == null ? "–" : pct(x));
  box.innerHTML = html`
    <div class="section-head"><h2>Pertinence par profil</h2><span class="small muted">bêta · administrateurs</span></div>
    <p class="small">Précision : part d'offres aimées ou retenues parmi les ${b.top} mieux notées que la personne a jugées
      (mesurée à partir de ${b.min_jugees} jugées). Médiane : <strong>${p(b.precision_mediane)}</strong> sur ${b.mesures} profil(s)
      ${b.sous_50 ? html` · <strong>${b.sous_50}</strong> sous 50 %` : ""}.</p>
    <div class="beta-table" role="table">
      <div class="beta-row head" role="row"><span>Profil</span><span>Notées</span><span>Retenues</span><span>Jugées</span>
        <span>Précision</span><span>Critères</span></div>
      ${b.utilisateurs.map((u) => html`
      <div class="beta-row" role="row"><span class="grow">#${u.user_id} · ${u.profil}</span><span>${u.notees}</span>
        <span>${u.retenues}</span><span>${u.positives}/${u.jugees}</span>
        <span class="${u.precision_top != null && u.precision_top < 0.5 ? "low" : ""}">${p(u.precision_top)}</span>
        <span>${u.criteres_definis}/3</span></div>`)}
    </div>
    <p class="small muted">« Critères » : définitions personnelles remplies (stade, type de rôle, produit), utilisées par la notation v2.</p>`.s;
}

export async function renderHealth() {
  renderEngine();
  renderBeta();
  const h = await api("/admin/taxonomy-health").catch(() => null);
  if (!h) return;
  const box = $("#m-health");
  box.hidden = false;
  box.innerHTML = html`
    <div class="section-head"><h2>Contrôle de la liste fermée</h2><span class="small muted">Visible par les administrateurs</span></div>
    <p class="small">Version ${h.taxonomy_version} · ${h.classified} offres classées · ${h.pending} en attente ·
      <strong>${pct(h.autre_share)}</strong> en famille « autre » (${h.autre})</p>
    <div class="grid-2">
      <div><p class="small strong">Libellés hors liste les plus fréquents</p>
        <div class="rows">${h.misses.slice(0, 20).map((m) => html`<div class="row-item"><span class="grow">${m.label}</span>
          <span class="small muted">${m.kind} · ${m.count}</span></div>`)}</div></div>
      <div><p class="small strong">Compétences jamais rencontrées (${h.never_seen_skills.length})</p>
        <p class="small muted">${h.never_seen_skills.map((s) => s.label).join(", ") || "Aucune"}</p></div>
    </div>
    <p class="small muted">Ajouter ou retirer une entrée : modifier backend/app/taxonomy.py, augmenter TAXONOMY_VERSION, déployer.</p>`.s;
}

