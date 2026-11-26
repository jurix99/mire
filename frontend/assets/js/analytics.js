// Analytics : trois onglets (Marché, Toi face au marché, Mon suivi), onglet actif dans l'URL (?onglet=marche).
// Marché et « Toi face au marché » lisent des agrégats précalculés (API /analytics/market, /analytics/me) ;
// les indicateurs réservés au Pro arrivent sans aucune vraie valeur (regroupés ici dans un encart « Pro »).
//
// Onglet Marché, en trois parties : « Qui recrute » (tous métiers), « Par métier » (un domaine à la fois : les
// compétences de la tech ne se mélangent pas à celles de la vente), « Après la levée ». Chaque carte = une question
// et UN graphique adapté (charts.js, linechart.js) : pas de phrase qui répète le graphique, le détail est dans
// l'infobulle.
import {
  $, $$, STATUS_LABEL, api, boot, fmtDate, html, loadMe, logo, proBar, renderValue,
} from "./app.js";
import { COLORS, SERIES, barChart, dumbbell, rangeChart, stackedBars } from "./charts.js";
import { lineChart } from "./linechart.js";

const TABS = ["marche", "moi", "suivi"];
// Domaines proposés dans « Par métier » (« Généraliste » n'a pas de compétences propres)
const DOMAINS = [["tech", "Tech"], ["produit_design", "Produit et design"], ["gtm_ventes", "Ventes"],
  ["gtm_marketing", "Marketing"], ["operations", "Opérations"]];
const params = new URLSearchParams(location.search);
const state = {
  tab: TABS.includes(params.get("onglet")) ? params.get("onglet") : "marche",
  domain: DOMAINS.some(([k]) => k === params.get("domaine")) ? params.get("domaine") : "tech",
  period: params.get("periode") ?? "90j",
};
const loaded = new Set();
const byDomain = new Map(); // réponses « Par métier » déjà reçues (période courante)

const pct = (x) => `${Math.round(x * 100)} %`;
const plural = (n, word) => `${n} ${word}${n > 1 ? "s" : ""}`;

/** Carte : une question en titre, une ligne qui définit la mesure, le graphique (rempli ensuite par id). */
const chartCard = (id, title, sub, { wide = false } = {}) => html`
  <article class="card pad-lg mcard ${wide ? "wide" : ""}">
    <div class="mcard-top"><h2 class="mcard-title">${title}</h2>${sub ? html`<p class="mcard-sub">${sub}</p>` : ""}</div>
    <div id="${id}"></div>
  </article>`;

/** Indicateurs réservés au Pro : un seul encart par partie, pas une carte floutée par indicateur. */
const proCard = (titles) => (titles.length ? html`
  <article class="card pad-lg mcard pro-teaser">
    <h2 class="mcard-title">Avec le Pro</h2>
    <ul>${titles.map((t) => html`<li>${t}</li>`)}</ul>
    ${proBar("Toutes les analyses du marché, sur 6 mois")}
  </article>` : "");

const isOpen = (ind) => ind && !ind.locked;

// --- Qui recrute (tous métiers) -------------------------------------------------------------------------------

function renderWho(data, opts) {
  const roles = data.indicators.roles;
  const domainOf = Object.fromEntries(opts.families.map((f) => [f.key, f.domain]));
  const domainLabel = Object.fromEntries(opts.domains.map((d) => [d.key, d.label]));
  // Métiers regroupés par domaine (domaines dans l'ordre du nombre d'offres), offres publiées sur la période
  const fams = (roles.families ?? []).filter((f) => domainOf[f.key]);
  const totals = {};
  for (const f of fams) totals[domainOf[f.key]] = (totals[domainOf[f.key]] ?? 0) + f.jobs;
  fams.sort((a, b) => (totals[domainOf[b.key]] - totals[domainOf[a.key]]) || (b.jobs - a.jobs));
  $("#m-who").innerHTML = html`
    ${chartCard("ch-families", "Les métiers qui recrutent", "Offres publiées sur la période", { wide: true })}`.s;
  barChart($("#ch-families"), {
    items: fams.map((f) => ({
      label: f.label, value: f.jobs, display: String(f.jobs), group: domainLabel[domainOf[f.key]],
      tip: `${plural(f.jobs, "offre")} dans ${plural(f.units, "startup")}`,
    })),
  });
}

// --- Par métier (un domaine) ----------------------------------------------------------------------------------

const ROLE_NOTE = { gtm_ventes: true, gtm_marketing: true }; // marchés visés : seulement ventes et marketing

function renderDomain(data) {
  const ind = data.indicators;
  const cards = [];
  const draws = [];
  const locked = [];
  const sk = ind.skills;
  if (isOpen(sk) && sk.top?.length) {
    cards.push(chartCard("ch-skills", "Les compétences exigées", "Part des startups du métier qui l'exigent"));
    draws.push(() => barChart($("#ch-skills"), {
      items: sk.top.slice(0, 10).map((i) => ({
        label: i.label, value: i.share, display: pct(i.share),
        tip: `${plural(i.units, "startup")} sur ${i.base} · ${plural(i.jobs, "offre")}${i.low_data ? " · peu de données" : ""}`,
      })),
      max: Math.max(...sk.top.map((i) => i.share)),
    }));
  }
  const tl = ind.timeline;
  if (isOpen(tl) && tl.months?.length >= 2 && tl.skills?.length) {
    cards.push(chartCard("ch-timeline", "Leur évolution", "Part des startups du métier qui l'exigent, mois par mois"));
    draws.push(() => {
      const series = tl.skills.map((s) => ({ key: s.key, label: s.label, values: s.points.map((p) => p.share) }));
      lineChart($("#ch-timeline"), { months: tl.months, series, selected: series.slice(0, 3).map((s) => s.key), unit: "startups" });
    });
  }
  const tr = ind.trends;
  if (isOpen(tr)) {
    const items = [...tr.up, ...tr.down].sort((a, b) => b.pp - a.pp);
    if (items.length) {
      cards.push(chartCard("ch-trends", "Ce qui monte, ce qui baisse", "30 derniers jours face aux 90 jours d'avant"));
      draws.push(() => dumbbell($("#ch-trends"), {
        legend: ["90 jours d'avant", "30 derniers jours"],
        items: items.map((i) => ({
          label: i.label, before: i.share_before, after: i.share_recent,
          display: `${i.pp > 0 ? "+" : "−"}${Math.abs(Math.round(i.pp))} pts`,
          tip: `${pct(i.share_before)} → ${pct(i.share_recent)} des startups`,
        })),
      }));
    }
  } else if (tr) locked.push("Les compétences qui montent ou baissent");
  const ro = ind.roles;
  if (isOpen(ro) && ro.seniority_jobs?.length) {
    cards.push(chartCard("ch-seniority", "Le niveau recherché", "Offres qui précisent la séniorité"));
    draws.push(() => stackedBars($("#ch-seniority"), {
      legend: ro.seniority_jobs.map((s, i) => ({ key: s.key, label: s.label, color: COLORS.ramp[i + 1] ?? COLORS.ramp[4] })),
      rows: [{ parts: ro.seniority_jobs.map((s) => ({ key: s.key, value: s.share, tip: plural(s.jobs, "offre") })) }],
    }));
  }
  const wm = ind.work_modes;
  if (isOpen(wm) && wm.items?.length) {
    const legend = [{ key: "remote", label: "Remote", color: SERIES[0] }, { key: "hybride", label: "Hybride", color: SERIES[1] },
      { key: "sur_site", label: "Sur site", color: SERIES[2] }];
    cards.push(chartCard("ch-workmodes", "Remote, hybride ou sur site", "Offres qui le précisent, par métier"));
    draws.push(() => stackedBars($("#ch-workmodes"), {
      legend,
      rows: wm.items.slice(0, 6).map((i) => ({
        label: i.label, parts: i.modes.map((m) => ({ key: m.key, value: m.share, tip: plural(m.jobs, "offre") })),
      })),
    }));
  } else if (wm?.locked) locked.push("Remote, hybride ou sur site, par métier");
  const sa = ind.salaries;
  if (isOpen(sa) && sa.items?.length >= 2) { // une seule fourchette ne compare rien
    cards.push(chartCard("ch-salaries", "Les salaires affichés", "Brut annuel : fourchette du milieu (25-75 %) et médiane"));
    draws.push(() => rangeChart($("#ch-salaries"), {
      unit: " k€",
      rows: sa.items.map((i) => ({
        label: i.kind === "ote" ? `${i.family_label} (OTE)` : i.family_label, q1: i.q1, med: i.median, q3: i.q3,
        display: `${i.median} k€`,
        tip: `${i.q1} – ${i.q3} k€ · ${plural(i.jobs, "offre")}${i.low_data ? " · peu de données" : ""}`,
      })),
    }));
  } else if (sa?.locked) locked.push("Les salaires affichés, par métier");
  const mk = ind.markets;
  if (ROLE_NOTE[state.domain]) {
    if (isOpen(mk) && mk.items?.length) {
      cards.push(chartCard("ch-markets", "Les marchés visés", "Part des startups qui recrutent pour ce marché"));
      draws.push(() => barChart($("#ch-markets"), {
        items: mk.items.slice(0, 8).map((i) => ({
          label: i.label, value: i.share, display: pct(i.share), tip: `${plural(i.units, "startup")} · ${plural(i.jobs, "offre")}`,
        })),
      }));
    } else if (mk?.locked) locked.push("Les marchés visés");
  }
  $("#m-domain-cards").innerHTML = cards.length
    ? html`${cards}${proCard(locked)}`.s
    : html`<p class="small muted">Pas encore assez d'offres dans ce métier sur la période.</p>${proCard(locked)}`.s;
  draws.forEach((draw) => draw());
}

// --- Après la levée (tous métiers) ----------------------------------------------------------------------------

function renderFunding(data) {
  const ind = data.indicators;
  const cards = [];
  const draws = [];
  const locked = [];
  const af = ind.after_funding;
  const lt = ind.lifetimes;
  if (isOpen(af) || isOpen(lt)) {
    const tiles = [];
    if (isOpen(af) && af.median_delay_days != null) {
      tiles.push(html`<div class="stat"><p class="big-number">${af.median_delay_days} <span>jours</span></p>
        <p class="small muted">entre la levée et la première offre (médiane, ${plural(af.companies, "startup")})</p></div>`);
    }
    if (isOpen(lt) && lt.median_days != null) {
      tiles.push(html`<div class="stat"><p class="big-number">${lt.median_days} <span>jours</span></p>
        <p class="small muted">en ligne avant d'être pourvue ou retirée (médiane, ${plural(lt.closed, "offre")})</p></div>`);
    }
    if (tiles.length) cards.push(html`<article class="card pad-lg mcard stats-card">${tiles}</article>`);
  }
  if (isOpen(af) && af.by_round?.length) {
    cards.push(chartCard("ch-rounds", "Qui recrute-t-on selon le stade", "Répartition des offres après la levée"));
    draws.push(() => stackedBars($("#ch-rounds"), {
      legend: [{ key: "tech", label: "Tech", color: SERIES[0] }, { key: "gtm", label: "Ventes et marketing", color: SERIES[1] },
        { key: "other", label: "Autres métiers", color: SERIES[2] }],
      rows: af.by_round.map((r) => ({
        label: r.label, parts: [
          { key: "tech", value: r.tech_share, tip: plural(r.tech_jobs, "offre") },
          { key: "gtm", value: r.gtm_share, tip: plural(r.gtm_jobs, "offre") },
          { key: "other", value: Math.max(0, 1 - r.tech_share - r.gtm_share), tip: `${plural(r.companies, "startup")} au total` },
        ],
      })),
    }));
  } else if (af?.locked) locked.push("Le délai entre la levée et la première offre", "Qui recrute-t-on selon le stade");
  if (lt?.locked) locked.push("La durée de vie d'une offre");
  const inv = ind.investors;
  if (isOpen(inv) && inv.items?.length) {
    cards.push(chartCard("ch-investors", "Les fonds dont le portefeuille recrute", "Startups du portefeuille qui recrutent"));
    draws.push(() => barChart($("#ch-investors"), {
      items: inv.items.slice(0, 10).map((i) => ({
        label: i.label, value: i.companies, display: String(i.companies), tip: `${plural(i.jobs, "offre")} ouvertes`,
      })),
    }));
  } else if (inv?.locked) locked.push("Les fonds dont le portefeuille recrute");
  $("#m-funding").innerHTML = html`${cards}${proCard(locked)}`.s;
  draws.forEach((draw) => draw());
}

// --- Onglet Marché ------------------------------------------------------------------------------------

function syncUrl() {
  const url = new URL(location.href);
  url.searchParams.set("onglet", state.tab);
  for (const [k, v] of [["domaine", state.domain], ["periode", state.period]]) {
    if (state.tab === "marche") url.searchParams.set(k, v);
    else url.searchParams.delete(k);
  }
  for (const old of ["famille", "par"]) url.searchParams.delete(old);
  history.replaceState(null, "", url);
}

const marketQuery = (domain) => {
  const q = new URLSearchParams({ period: state.period, count_by: "entreprise" });
  if (domain) q.set("domain", domain);
  return api(`/analytics/market?${q}`);
};

async function renderDomainTab() {
  $$("#m-domains [data-domain]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.domain === state.domain)));
  syncUrl();
  if (!byDomain.has(state.domain)) byDomain.set(state.domain, marketQuery(state.domain));
  renderDomain(await byDomain.get(state.domain));
}

async function renderMarket() {
  byDomain.clear();
  const data = await marketQuery(null);
  state.period = data.filters.period;
  $("#m-period").innerHTML = html`${data.options.periods.map((p) => html`
    <option value="${p.key}" ${p.key === state.period ? "selected" : ""}
      ${data.allowed_periods && !data.allowed_periods.includes(p.key) ? "disabled" : ""}>${p.label}</option>`)}`.s;
  $("#m-perimeter").textContent = `${plural(data.perimeter.jobs, "offre")} · ${plural(data.perimeter.companies, "startup")} financée${data.perimeter.companies > 1 ? "s" : ""}`;
  $("#m-domains").innerHTML = html`${DOMAINS.map(([k, label]) => html`
    <button type="button" data-domain="${k}" aria-pressed="${k === state.domain}">${label}</button>`)}`.s;
  renderWho(data, data.options);
  renderFunding(data);
  await renderDomainTab();
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

async function renderHealth() {
  const me = await loadMe().catch(() => null);
  if (!me?.user?.is_admin) return;
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

// --- Onglet Toi face au marché ------------------------------------------------------------------------

async function renderMe() {
  const d = await api("/analytics/me");
  if (d.locked) {
    $("#me-scope").textContent = "";
    $("#me-cards").innerHTML = proCard([d.missing.title, d.coverage.title, d.strengths.title, d.salary.title]).s;
    return;
  }
  $("#me-scope").textContent = d.family_label
    ? `Face aux offres « ${d.family_label} » des 90 derniers jours et à vos offres bien notées.`
    : "Indiquez les rôles visés dans votre profil pour vous comparer à votre famille de postes.";
  const s = d.salary.data;
  const cards = [];
  const draws = [];
  if (d.missing.items.length) {
    cards.push(chartCard("ch-missing", "Ce qui vous manque le plus souvent", "Part de vos offres notées 7 et plus qui l'exigent"));
    draws.push(() => barChart($("#ch-missing"), { items: d.missing.items.map((i) => ({
      label: i.label, value: i.share, display: pct(i.share), tip: plural(i.jobs, "offre") })) }));
  }
  if (d.strengths.items.length) {
    cards.push(chartCard("ch-strengths", "Vos atouts recherchés", "Part des offres de votre famille qui les exigent"));
    draws.push(() => barChart($("#ch-strengths"), { items: d.strengths.items.map((i) => ({
      label: i.label, value: i.share, display: pct(i.share), tip: plural(i.jobs, "offre") })) }));
  }
  const tiles = [];
  if (d.coverage.share != null) {
    tiles.push(html`<div class="stat"><p class="big-number">${pct(d.coverage.share)}</p>
      <p class="small muted">des offres de votre famille dont vous cochez l'essentiel (${d.coverage.covered} sur ${d.coverage.base})</p></div>`);
  }
  if (s) {
    tiles.push(html`<div class="stat"><p class="big-number">${s.median} <span>k€</span></p>
      <p class="small muted">salaire médian affiché dans votre famille (${s.q1}–${s.q3} k€)${s.expected && s.share_meeting_expectation != null
        ? ` ; ${pct(s.share_meeting_expectation)} atteignent vos ${s.expected} k€` : ""}</p></div>`);
  }
  if (tiles.length) cards.push(html`<article class="card pad-lg mcard stats-card">${tiles}</article>`);
  $("#me-cards").innerHTML = cards.length ? html`${cards}`.s : html`<p class="small muted">Pas encore assez d'évaluations.</p>`.s;
  draws.forEach((draw) => draw());
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
  const b = e.target.closest("[data-domain]");
  if (!b || b.dataset.domain === state.domain) return;
  state.domain = b.dataset.domain;
  renderDomainTab();
});
$("#m-period").addEventListener("change", (e) => { state.period = e.target.value; renderMarket(); });

boot(() => show(state.tab));
