// Analytics : trois onglets (Marché, Toi face au marché, Mon suivi), onglet actif dans l'URL (?onglet=marche).
// Marché et « Toi face au marché » lisent des agrégats précalculés (API /analytics/market, /analytics/me) ;
// les indicateurs réservés au Pro arrivent sans aucune vraie valeur (aperçu factice, flouté ici).
import {
  $, $$, STATUS_LABEL, api, boot, fmtDate, html, loadMe, lockedBlock, logo, proBar, renderValue,
} from "./app.js";

const TABS = ["marche", "moi", "suivi"];
const SHOWN = 10; // éléments par liste avant « voir tout »
const params = new URLSearchParams(location.search);
const state = {
  tab: TABS.includes(params.get("onglet")) ? params.get("onglet") : "marche",
  domain: params.get("domaine") ?? "",
  family: params.get("famille") ?? "",
  period: params.get("periode") ?? "90j",
  countBy: params.get("par") ?? "entreprise",
};
const loaded = new Set();
let options = null;

// --- Mise en forme ------------------------------------------------------------------------------------

const pct = (x) => `${Math.round(x * 100)} %`;
const unitWord = (n, by = state.countBy) => (by === "entreprise" ? `${n} entreprise${n > 1 ? "s" : ""}` : `${n} offre${n > 1 ? "s" : ""}`);
const offers = (n) => `${n} offre${n > 1 ? "s" : ""}`;
const lowNote = (item) => (item?.low_data ? html`<span class="low-note">peu de données</span>` : "");

/** Barre horizontale : libellé, pourcentage et effectif, toujours ensemble. */
const bar = (label, share, count, { low = false, note = "" } = {}) => html`
  <div class="mbar ${low ? "is-low" : ""}">
    <span class="mbar-label">${label}</span>
    <span class="mbar-track" aria-hidden="true"><span class="mbar-fill" style="width:${Math.max(2, Math.min(100, share * 100))}%"></span></span>
    <span class="mbar-value">${pct(share)} <span class="muted">· ${count}</span>${note}</span>
  </div>`;

const shareBars = (items, by) => items.map((i) => bar(i.label, i.share, unitWord(i.units, by), { low: i.low_data, note: lowNote(i) }));

/** Liste de 10 éléments au plus, avec « voir tout » pour la suite (déjà reçue). */
function capped(id, items, render) {
  if (!items.length) return html`<p class="small muted">Rien au-dessus du seuil de 5 offres.</p>`;
  const head = items.slice(0, SHOWN);
  const rest = items.slice(SHOWN);
  return html`<div class="mlist">${head.map(render)}</div>
    ${rest.length ? html`<details class="see-all"><summary>Voir tout (${items.length})</summary>
      <div class="mlist">${rest.map(render)}</div></details>` : ""}`;
}

const card = (ind, body, { wide = false } = {}) => html`
  <article class="card pad-lg mcard ${wide ? "wide" : ""}">
    <p class="label">${ind.title}</p>
    <h2 class="mcard-head">${ind.headline}</h2>
    ${body}
  </article>`;

/** Indicateur réservé au Pro : bloc flouté d'un seul tenant (texte factice), nombre d'éléments cachés. */
const lockedCard = (ind) => html`
  <article class="card pad-lg mcard">
    <p class="label">${ind.title}</p>
    <h2 class="mcard-head">${ind.title}</h2>
    ${lockedBlock(html`<div class="mlist">${(ind.preview ?? []).map((p, i) => bar(p.label.placeholder, 0.6 - i * 0.1, p.value.placeholder))}</div>`)}
    <p class="small muted">${ind.hidden ? `${ind.hidden} élément${ind.hidden > 1 ? "s" : ""} réservé${ind.hidden > 1 ? "s" : ""} au Pro` : "Réservé au Pro"}</p>
  </article>`;

// --- Onglet Marché ------------------------------------------------------------------------------------

const RENDER = {
  skills: (d, by) => html`
    ${capped("skills", d.top, (i) => shareBars([i], by)[0])}
    ${d.by_family?.length ? html`<details class="see-all"><summary>Par famille de compétences</summary>
      ${d.by_family.map((f) => html`<p class="small strong">${f.label}</p><div class="mlist">${shareBars(f.items.slice(0, SHOWN), by)}</div>`)}
    </details>` : ""}`,
  trends: (d) => {
    const row = (i) => html`<div class="trend ${i.pp > 0 ? "up" : "down"}">
      <span>${i.label}</span>
      <span class="trend-pp">${i.pp > 0 ? "▲ +" : "▼ −"}${Math.abs(i.pp).toLocaleString("fr-FR")} pts</span>
      <span class="small muted">${pct(i.share_before)} → ${pct(i.share_recent)}</span></div>`;
    return html`<div class="trends">
      <div><p class="small strong">En hausse</p>${d.up.length ? d.up.map(row) : html`<p class="small muted">Aucune hausse nette</p>`}</div>
      <div><p class="small strong">En baisse</p>${d.down.length ? d.down.map(row) : html`<p class="small muted">Aucune baisse nette</p>`}</div>
    </div>
    <p class="small muted">30 derniers jours face aux 90 jours précédents (${unitWord(d.base_recent)} / ${unitWord(d.base_before)}).</p>`;
  },
  roles: (d, by) => html`
    ${!state.domain && d.domains?.length ? html`<p class="small strong">Par domaine</p><div class="mlist">${shareBars(d.domains, by)}</div>` : ""}
    <p class="small strong">Par famille</p>
    ${capped("roles", d.families, (i) => shareBars([i], by)[0])}
    ${d.seniority?.length ? html`<p class="small strong">Par séniorité</p><div class="mlist">${shareBars(d.seniority, by)}</div>` : ""}`,
  stacks: (d) => html`<div class="mlist">${d.items.map((i) => html`
    <div class="row-item"><div class="grow"><span class="title">${i.skills.join(" + ")}</span>
      <span class="small muted"> · ${i.family_label}</span></div>
      <span class="small">${pct(i.share)} <span class="muted">· ${offers(i.jobs)}</span></span>${lowNote(i)}</div>`)}</div>`,
  salaries: (d) => html`${capped("salaries", d.items, (i) => html`
    <div class="row-item ${i.low_data ? "is-low" : ""}">
      <div class="grow"><span class="title">${i.family_label}</span>
        <span class="small muted"> · ${i.seniority_label}</span> <span class="pill plain">${i.kind === "ote" ? "OTE" : "Fixe"}</span></div>
      <span class="small"><strong>${i.median} k€</strong> <span class="muted">(${i.q1}–${i.q3}) · ${offers(i.jobs)}</span></span>${lowNote(i)}
    </div>`)}
    <p class="small muted">Médiane et écart interquartile du milieu de fourchette, brut annuel, sur ${offers(d.with_salary)} qui affichent un salaire.</p>`,
  work_modes: (d) => html`${capped("work_modes", d.items, (i) => html`
    <div class="wm-row ${i.low_data ? "is-low" : ""}">
      <span class="mbar-label">${i.label}</span>
      <span class="wm-stack" aria-label="${i.modes.map((m) => `${m.label} ${pct(m.share)}`).join(", ")}">
        ${i.modes.map((m) => html`<span class="wm-${m.key}" style="width:${m.share * 100}%"></span>`)}</span>
      <span class="mbar-value">${i.modes.map((m) => `${m.label[0]} ${pct(m.share)}`).join(" · ")} <span class="muted">· ${offers(i.jobs)}</span></span>
    </div>`)}
    <p class="small muted wm-legend"><span class="wm-remote"></span>Remote <span class="wm-hybride"></span>Hybride <span class="wm-sur_site"></span>Sur site</p>`,
  after_funding: (d) => html`
    ${d.median_delay_days != null ? html`<p class="big-number">${d.median_delay_days} <span>jours</span></p>
      <p class="small muted">Délai médian entre l'annonce de la levée et la première offre, sur ${d.companies} startups${d.low_data ? " (peu de données)" : ""}.</p>` : ""}
    ${d.by_round.length ? html`<p class="small strong">Tech et commercial selon le stade</p>${d.by_round.map((r) => html`
      <div class="wm-row"><span class="mbar-label">${r.label}</span>
        <span class="wm-stack"><span class="wm-remote" style="width:${r.tech_share * 100}%"></span><span class="wm-hybride" style="width:${r.gtm_share * 100}%"></span></span>
        <span class="mbar-value">Tech ${pct(r.tech_share)} · Ventes et marketing ${pct(r.gtm_share)} <span class="muted">· ${offers(r.jobs)}</span></span></div>`)}` : ""}
    ${d.first_families.length ? html`<p class="small strong">Familles ouvertes en premier</p><div class="mlist">${d.first_families.map((f) => html`
      <div class="row-item"><span class="grow">${f.label}</span><span class="small muted">${f.companies} startups</span></div>`)}</div>` : ""}`,
  lifetimes: (d) => html`${capped("lifetimes", d.items, (i) => html`
    <div class="row-item ${i.low_data ? "is-low" : ""}"><span class="grow">${i.label}</span>
      <span class="small"><strong>${i.median_days} j</strong> <span class="muted">· ${offers(i.jobs)} fermées</span></span>${lowNote(i)}</div>`)}`,
  markets: (d, by) => capped("markets", d.items, (i) => shareBars([i], by)[0]),
  investors: (d) => capped("investors", d.items, (i) => html`
    <div class="row-item"><span class="grow">${i.label}</span>
      <span class="small"><strong>${offers(i.jobs)}</strong> <span class="muted">ouvertes · ${i.companies} startups</span></span></div>`),
};

function drawFilters() {
  const domains = [{ key: "", label: "Tous" }, ...options.domains];
  $("#m-domains").innerHTML = html`${domains.map((d) => html`
    <button type="button" class="chip" data-domain="${d.key}" aria-pressed="${state.domain === d.key}">${d.label}</button>`)}`.s;
  const families = options.families.filter((f) => !state.domain || f.domain === state.domain);
  if (state.family && !families.some((f) => f.key === state.family)) state.family = "";
  $("#m-family").innerHTML = html`<option value="">Toutes les familles</option>${families.map((f) => html`
    <option value="${f.key}" ${f.key === state.family ? "selected" : ""}>${f.label}</option>`)}`.s;
  $("#m-period").innerHTML = html`${options.periods.map((p) => html`
    <option value="${p.key}" ${p.key === state.period ? "selected" : ""}>${p.label}</option>`)}`.s;
  $("#m-countby").innerHTML = html`${options.count_by.map((c) => html`
    <button type="button" data-countby="${c.key}" aria-pressed="${state.countBy === c.key}">${c.label}</button>`)}`.s;
}

function syncUrl() {
  const url = new URL(location.href);
  url.searchParams.set("onglet", state.tab);
  for (const [k, v] of [["domaine", state.domain], ["famille", state.family], ["periode", state.period], ["par", state.countBy]]) {
    if (v && state.tab === "marche") url.searchParams.set(k, v);
    else url.searchParams.delete(k);
  }
  history.replaceState(null, "", url);
}

async function renderMarket() {
  const q = new URLSearchParams({ period: state.period, count_by: state.countBy });
  if (state.domain) q.set("domain", state.domain);
  if (state.family) q.set("family", state.family);
  const data = await api(`/analytics/market?${q}`);
  options = data.options;
  state.period = data.filters.period;
  drawFilters();
  syncUrl();
  $("#m-perimeter").textContent = data.perimeter.text;
  $("#m-pro").innerHTML = data.plan === "free"
    ? proBar(`En Free : compétences et postes sur 90 jours (top 5) ; tendances, salaires, stacks, levées et fonds réservés au Pro`
      + (data.period_locked ? " · période ramenée à 90 jours" : "")).s
    : "";
  const order = ["skills", "trends", "roles", "stacks", "salaries", "work_modes", "after_funding", "lifetimes", "markets", "investors"];
  $("#m-cards").innerHTML = html`${order.map((key) => {
    const ind = data.indicators[key];
    if (!ind) return "";
    if (ind.locked) return lockedCard(ind);
    const hidden = ind.hidden ? html`<p class="small muted">${ind.hidden} de plus avec le Pro</p>` : "";
    return card(ind, html`${RENDER[key](ind, data.filters.count_by)}${hidden}`, { wide: ["skills", "roles"].includes(key) });
  })}`.s;
}

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

async function renderHealth() {
  const me = await loadMe().catch(() => null);
  if (!me?.user?.is_admin) return;
  renderEngine();
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

// --- Onglet Toi face au marché ------------------------------------------------------------------------

async function renderMe() {
  const d = await api("/analytics/me");
  if (d.locked) {
    $("#me-pro").innerHTML = proBar("Vos écarts de compétences, votre couverture des offres et vos salaires : réservés au Pro").s;
    $("#me-scope").textContent = "";
    $("#me-cards").innerHTML = html`${["missing", "coverage", "strengths", "salary"].map((k) => html`
      <article class="card pad-lg mcard"><p class="label">${d[k].title}</p><h2 class="mcard-head">${d[k].headline}</h2>
        ${lockedBlock(html`<div class="mlist">${(d[k].preview ?? []).map((p, i) => bar(p.label.placeholder, 0.55 - i * 0.1, p.value.placeholder))}</div>`)}
      </article>`)}`.s;
    return;
  }
  $("#me-pro").innerHTML = "";
  $("#me-scope").textContent = d.family_label
    ? `Comparaison avec les offres « ${d.family_label} » des 90 derniers jours et avec vos offres bien notées.`
    : "Indiquez les rôles visés dans votre profil pour vous comparer à votre famille de postes.";
  const s = d.salary.data;
  $("#me-cards").innerHTML = html`
    ${card(d.missing, d.missing.items.length
      ? html`<div class="mlist">${d.missing.items.map((i) => bar(i.label, i.share, offers(i.jobs), { low: i.low_data, note: lowNote(i) }))}</div>
        <p class="small muted">Sur vos ${offers(d.missing.base)} notées 7 et plus.</p>`
      : html`<p class="small muted">Pas encore assez d'évaluations.</p>`)}
    ${card(d.coverage, d.coverage.share != null
      ? html`<p class="big-number">${pct(d.coverage.share)}</p><p class="small muted">${d.coverage.covered} offres sur ${d.coverage.base}${d.coverage.low_data ? " (peu de données)" : ""}.</p>`
      : "")}
    ${card(d.strengths, d.strengths.items.length
      ? html`<div class="mlist">${d.strengths.items.map((i) => bar(i.label, i.share, offers(i.jobs), { low: i.low_data, note: lowNote(i) }))}</div>`
      : "")}
    ${card(d.salary, s ? html`
      <p class="big-number">${s.median} <span>k€</span></p>
      <p class="small muted">Écart interquartile ${s.q1}–${s.q3} k€ · ${offers(s.jobs)}${s.seniority_label ? ` · ${s.seniority_label.toLowerCase()}` : ""}${s.low_data ? " (peu de données)" : ""}.</p>
      ${s.expected ? html`<p class="small">Vos attentes : ${s.expected} k€ minimum ·
        ${s.share_meeting_expectation != null ? `${pct(s.share_meeting_expectation)} des fourchettes les atteignent` : ""}</p>`
        : html`<p class="small muted">Renseignez votre salaire minimum dans votre profil pour vous situer.</p>`}` : "")}`.s;
}

// --- Onglet Mon suivi (inchangé) ----------------------------------------------------------------------

async function renderTracking() {
  const data = await api("/analytics");

  const max = Math.max(1, ...data.by_status.map((s) => s.count));
  $("#by-status").innerHTML = html`${data.by_status.map((s) => html`
    <div class="bar-row st-${s.status}">
      <span>${STATUS_LABEL[s.status]}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(s.count / max) * 100}%"></div></div>
      <span class="n">${s.count}</span>
    </div>`)}`.s;

  const top = Math.max(1, data.funnel[0].count);
  $("#funnel").innerHTML = html`${data.funnel.map((f) => html`
    <div class="funnel-step">
      <span>${f.label}</span>
      <div class="funnel-bar" style="width:${Math.max(8, (f.count / top) * 100)}%">${f.count}</div>
    </div>`)}`.s;
  const [, applied, interviews] = data.funnel;
  $("#rate").textContent = applied.count
    ? `${Math.round((interviews.count / applied.count) * 100)} % des candidatures mènent à un entretien`
    : "";

  const wmax = Math.max(1, ...data.weekly.map((w) => w.events));
  $("#weeks").innerHTML = html`${data.weekly.map((w) => html`
    <div class="week" title="${w.events} changement(s) la semaine du ${fmtDate(w.week)}">
      <span>${w.events || ""}</span>
      <div class="col ${w.events ? "" : "zero"}" style="height:${(w.events / wmax) * 110}px"></div>
      <span>${fmtDate(w.week)}</span>
    </div>`)}`.s;

  $("#by-company").innerHTML = html`${data.by_company.map((r) => html`
    <div class="row-item">
      ${logo(r.company, "sm")}
      <div class="grow"><span class="title">${renderValue(r.company.name)}</span></div>
      <span class="small muted">${r.jobs} poste${r.jobs > 1 ? "s" : ""}</span>
      <span class="pill ${r.processes ? "st-postule" : "plain"}">${r.processes} process</span>
    </div>`)}`.s;
}

// --- Onglets ------------------------------------------------------------------------------------------

const RENDERERS = { marche: () => Promise.all([renderMarket(), renderHealth()]), moi: renderMe, suivi: renderTracking };

async function show(tab) {
  state.tab = tab;
  for (const t of TABS) {
    $(`#tab-${t}`).hidden = t !== tab;
    const btn = $(`#tabbtn-${t}`);
    btn.setAttribute("aria-selected", String(t === tab));
    btn.tabIndex = t === tab ? 0 : -1;
  }
  syncUrl();
  if (!loaded.has(tab)) {
    loaded.add(tab);
    await RENDERERS[tab]();
  }
}

$(".tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-tab]");
  if (btn) show(btn.dataset.tab);
});
$(".tabs").addEventListener("keydown", (e) => {
  if (!["ArrowRight", "ArrowLeft"].includes(e.key)) return;
  const i = TABS.indexOf(state.tab) + (e.key === "ArrowRight" ? 1 : -1);
  const next = TABS[(i + TABS.length) % TABS.length];
  show(next);
  $(`#tabbtn-${next}`).focus();
});
$("#m-domains").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-domain]");
  if (!chip) return;
  state.domain = chip.dataset.domain;
  renderMarket();
});
$("#m-family").addEventListener("change", (e) => { state.family = e.target.value; renderMarket(); });
$("#m-period").addEventListener("change", (e) => { state.period = e.target.value; renderMarket(); });
$("#m-countby").addEventListener("click", (e) => {
  const b = e.target.closest("[data-countby]");
  if (!b) return;
  state.countBy = b.dataset.countby;
  renderMarket();
});

boot(() => show(state.tab));
