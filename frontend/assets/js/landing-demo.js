// Mire — démo cliquable de l'app sur la landing (#demo-app). Données FICTIVES, aucun appel réseau.
// Pages : tableau de bord, postes (+ fiche d'une offre), entreprises, process, assistant.

import { barChart } from "./charts.js";
import { lineChart } from "./linechart.js";

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

const COMPANIES = {
  NO: { name: "Nova Orbital", h: 210, stage: "Série A · 14 M€", when: "sept. 2026", size: "25-50", sector: "Spatial, données satellite",
        about: "Imagerie satellite pour l'assurance agricole. Lève pour construire sa plateforme de données." },
  HL: { name: "Halo Labs", h: 150, stage: "Seed · 4 M€", when: "oct. 2026", size: "11-25", sector: "Outils développeurs",
        about: "Observabilité pour les agents IA. Premières recrues techniques après la levée." },
  VR: { name: "Vertex Rail", h: 30, stage: "Série B · 32 M€", when: "juin 2026", size: "80-150", sector: "Logistique ferroviaire",
        about: "Optimisation du fret ferroviaire. Ouvre une équipe ML à Paris." },
  QT: { name: "Quanta", h: 300, stage: "Série A · 9 M€", when: "mai 2026", size: "25-50", sector: "Fintech",
        about: "Trésorerie automatisée pour PME. Recrute surtout sur le front." },
  LU: { name: "Lumen Bio", h: 100, stage: "Seed · 6 M€", when: "nov. 2026", size: "11-25", sector: "Biotech, IA",
        about: "Découverte de molécules assistée par IA. Cherche un premier ingénieur data." },
};

const JOBS = [
  { id: 1, co: "NO", title: "Founding Backend Engineer", where: "Paris · hybride", salary: "75-90 k€", score: 9.1, st: ["a", "À regarder"],
    crit: [["Compétences", 2.8, 3], ["Stade", 1.8, 2], ["Ownership", 2, 2], ["IA concrète", 1.5, 2], ["Expérience", 1, 1]],
    why: "Premier ingénieur backend, Python et Kubernetes, 75-90 k€, peu de déplacements. Correspond à vos règles." },
  { id: 2, co: "HL", title: "Platform Engineer", where: "Paris · remote partiel", salary: "65-80 k€", score: 8.6, st: ["n", "Nouveau"],
    crit: [["Compétences", 2.6, 3], ["Stade", 2, 2], ["Ownership", 1.7, 2], ["IA concrète", 1.8, 2], ["Expérience", 0.5, 1]],
    why: "Équipe de 4 ingénieurs, Go et Terraform. Demande 5 ans d'expérience, vous en avez 4 : léger écart." },
  { id: 3, co: "VR", title: "MLOps Engineer", where: "Paris · sur site", salary: "70-85 k€", score: 8.2, st: ["p", "Postulé"],
    crit: [["Compétences", 2.4, 3], ["Stade", 1.2, 2], ["Ownership", 1.6, 2], ["IA concrète", 2, 2], ["Expérience", 1, 1]],
    why: "MLOps sur des modèles en production, Python, GCP. Série B : moins d'ownership qu'en Seed." },
  { id: 4, co: "LU", title: "Data Engineer #1", where: "Paris · hybride", salary: "60-75 k€", score: 7.9, st: ["n", "Nouveau"],
    crit: [["Compétences", 2.2, 3], ["Stade", 2, 2], ["Ownership", 2, 2], ["IA concrète", 1.2, 2], ["Expérience", 0.5, 1]],
    why: "Premier poste data, pipelines de données scientifiques. Salaire sous votre cible." },
  { id: 5, co: "QT", title: "Senior Frontend Engineer", where: "Paris", salary: "65-75 k€", score: 5.4, st: ["x", "Écartée (auto)"], dim: true,
    crit: [["Compétences", 0.9, 3], ["Stade", 1.4, 2], ["Ownership", 1.2, 2], ["IA concrète", 0.4, 2], ["Expérience", 1, 1]],
    why: "Poste front (React) : hors de vos rôles visés. Écartée par votre règle « pas de poste front »." },
];

const PROCESS = {
  "Postulé": [["VR", "MLOps Engineer", "Postulé le 18/11"], ["HL", "Platform Engineer", "Postulé le 21/11"]],
  "Relance": [["LU", "Data Engineer #1", "Relance prévue le 27/11"]],
  "Entretien": [["NO", "Founding Backend Engineer", "Entretien CTO le 28/11"]],
  "Offre": [],
};

const CHAT = [
  ["Quels postes regarder en priorité ?",
   "Deux offres dépassent 8,5 : Founding Backend Engineer chez Nova Orbital (9,1), premier ingénieur backend juste après leur Série A, et Platform Engineer chez Halo Labs (8,6), une équipe de 4 qui vient de lever en Seed."],
  ["Qui a levé ce mois-ci ?",
   "Lumen Bio a levé 6 M€ en Seed en novembre et cherche son premier ingénieur data. Halo Labs a levé 4 M€ en octobre."],
  ["Où en sont mes process ?",
   "4 process actifs : 1 entretien (Nova Orbital, CTO le 28/11), 1 relance à faire chez Lumen Bio le 27/11, 2 candidatures envoyées sans réponse."],
];

// Analytics : 9 mois fictifs (part des startups qui exigent la compétence / recrutent dans la famille)
const MONTHS = ["mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov."]
  .map((m, i) => ({ label: `${m} 2026`, base: [118, 126, 131, 142, 128, 97, 151, 163, 172][i] }));
const TL_SKILLS = [
  ["python", "Python", [0.34, 0.35, 0.37, 0.36, 0.39, 0.41, 0.42, 0.44, 0.46]],
  ["llm", "LLM et IA générative", [0.12, 0.14, 0.15, 0.18, 0.21, 0.22, 0.26, 0.29, 0.31]],
  ["typescript", "TypeScript", [0.29, 0.28, 0.3, 0.29, 0.28, 0.27, 0.28, 0.27, 0.27]],
  ["kubernetes", "Kubernetes", [0.15, 0.16, 0.15, 0.17, 0.16, 0.18, 0.17, 0.19, 0.2]],
  ["react", "React", [0.26, 0.25, 0.25, 0.23, 0.22, 0.22, 0.21, 0.2, 0.19]],
  ["sql", "SQL", [0.22, 0.23, 0.22, 0.24, 0.23, 0.22, 0.24, 0.23, 0.24]],
];
// Un métier à la fois : les compétences de la tech ne se mélangent pas à celles de la vente
const TL_SALES = [
  ["outbound", "Prospection outbound", [0.52, 0.53, 0.55, 0.54, 0.56, 0.55, 0.57, 0.58, 0.58]],
  ["closing", "Closing / négociation", [0.38, 0.4, 0.39, 0.41, 0.42, 0.44, 0.43, 0.45, 0.47]],
  ["hubspot", "HubSpot", [0.18, 0.2, 0.22, 0.24, 0.25, 0.28, 0.3, 0.31, 0.33]],
  ["salesforce", "Salesforce", [0.31, 0.3, 0.29, 0.28, 0.27, 0.26, 0.25, 0.24, 0.24]],
  ["grands_comptes", "Vente grands comptes", [0.2, 0.21, 0.2, 0.22, 0.21, 0.23, 0.22, 0.22, 0.23]],
  ["ia_vente", "Outils d'IA pour la vente", [0.04, 0.05, 0.06, 0.08, 0.09, 0.11, 0.13, 0.15, 0.17]],
];
const DEMO_DOMAINS = { tech: TL_SKILLS, sales: TL_SALES };

const logo = (key, cls = "demo-logo") =>
  `<span class="${cls}" style="--h:${COMPANIES[key].h}">${key}</span>`;
const fmt = (n) => n.toLocaleString("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export function initDemo(root) {
  if (!root) return;
  const main = root.querySelector("#demo-main");
  const path = root.querySelector("#demo-path");
  const toastEl = root.querySelector("#demo-toast");
  const navButtons = [...root.querySelectorAll("[data-view]")];
  const feedback = {}; // id -> "like" | "nope"
  let toastTimer;

  const toast = (text) => {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toastEl.hidden = true), 3200);
  };

  const jobRow = (j) => `<button type="button" class="row${j.dim ? " dim" : ""}" data-job="${j.id}">
      ${logo(j.co)}<div><b>${esc(j.title)}</b><small>${esc(COMPANIES[j.co].name)} · ${esc(j.where)} · ${esc(COMPANIES[j.co].stage)}</small></div>
      <span class="score${j.score < 6 ? " low" : ""}">${fmt(j.score)}</span><span class="st st-${j.st[0]}">${esc(j.st[1])}</span></button>`;

  const views = {
    dashboard: () => `<h4>Bonjour Camille</h4><p class="sub">12 nouvelles offres ce matin, 4 au-dessus de 7,5 pour vous.</p>
      <div class="kpis"><div class="kpi"><strong>4</strong><span>à traiter</span></div>
        <div class="kpi"><strong>4</strong><span>process actifs</span></div>
        <div class="kpi"><strong>1</strong><span>entretien cette semaine</span></div></div>
      <button type="button" class="teaser" data-view="analytics">
        <span class="tiny">// Marché ce mois-ci</span>
        <span><b>Python</b> exigé par 46 % des startups qui recrutent · <b class="up">+12 pts depuis mars</b></span>
        <svg viewBox="0 0 80 24" aria-hidden="true"><polyline points="0,20 10,19 20,17 30,18 40,15 50,13 60,12 70,10 80,8"
          fill="none" stroke="#2a78d6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <span class="go">Voir les courbes →</span>
      </button>
      <p class="tiny">// À traiter en priorité · cliquez une offre</p>
      <div class="rows">${JOBS.slice(0, 3).map(jobRow).join("")}</div>`,
    jobs: () => `<h4>Postes</h4><p class="sub">Classés par note, selon votre barème. Les offres hors de vos règles sont écartées automatiquement.</p>
      <div class="rows">${JOBS.map(jobRow).join("")}</div>`,
    analytics: () => `<div class="an-head"><h4>Marché</h4>
        <div class="seg-demo" role="group" aria-label="Métier">
          <button type="button" data-tl="tech" aria-pressed="${tlView === "tech"}">Tech</button>
          <button type="button" data-tl="sales" aria-pressed="${tlView === "sales"}">Ventes</button></div></div>
      <div class="an-grid">
        <section class="an-card"><h5>Les compétences exigées</h5><p class="an-sub">Part des startups qui recrutent</p>
          <div id="demo-bars"></div></section>
        <section class="an-card"><h5>Leur évolution</h5><p class="an-sub">Mois par mois</p>
          <div id="demo-chart"></div></section>
      </div>`,
    companies: () => `<h4>Entreprises</h4><p class="sub">Les startups du radar, avec leur dernière levée.</p>
      <div class="cards">${Object.entries(COMPANIES).map(([k, c]) => `<div class="card">
        <div class="top">${logo(k)}<div><b>${esc(c.name)}</b><br><small class="muted">${esc(c.sector)}</small></div></div>
        <div><span class="fund">${esc(c.stage)}</span> <span class="tagc">${esc(c.when)}</span> <span class="tagc">${esc(c.size)} pers.</span></div>
        <p>${esc(c.about)}</p></div>`).join("")}</div>`,
    process: () => `<h4>Mes process</h4><p class="sub">Chaque candidature, sa prochaine étape et son historique.</p>
      <div class="board">${Object.entries(PROCESS).map(([col, items]) => `<div class="col"><p>${col}<span>${items.length}</span></p>
        ${items.map(([k, t, n]) => `<button type="button" class="pc" data-job="${JOBS.find((j) => j.title === t)?.id ?? ""}">
          <b>${esc(t)}</b><small>${esc(COMPANIES[k].name)} · ${esc(n)}</small></button>`).join("")}</div>`).join("")}</div>`,
    chat: () => `<h4>Assistant</h4><p class="sub">Posez une question sur le radar ou sur vos candidatures.</p>
      <div class="chat" id="demo-chat"><div class="bubble">Bonjour Camille, que voulez-vous savoir ?</div></div>
      <div class="suggest">${CHAT.map(([q], i) => `<button type="button" data-ask="${i}">${esc(q)}</button>`).join("")}</div>`,
  };

  const detail = (j) => {
    const c = COMPANIES[j.co];
    const fb = feedback[j.id];
    return `<button type="button" class="back" data-view-back>← Retour aux postes</button>
      <div class="detail-head">${logo(j.co)}<div><h4>${esc(j.title)}</h4>
        <p class="sub" style="margin:0">${esc(c.name)} · ${esc(j.where)} · ${esc(j.salary)}</p></div>
        <span class="score${j.score < 6 ? " low" : ""}">${fmt(j.score)}</span></div>
      <div>${j.crit.map(([n, v, max]) => `<div class="crit"><span>${n}</span><i style="--w:${(v / max) * 100}%"></i><b>${fmt(v)} / ${max}</b></div>`).join("")}</div>
      <div class="why"><span class="tiny">Pourquoi</span>${esc(j.why)}</div>
      <p class="sub"><span class="fund">${esc(c.stage)}</span> levée en ${esc(c.when)} · ${esc(c.about)}</p>
      <div class="actions">
        <button type="button" data-fb="like" class="${fb === "like" ? "on" : ""}">♥ J'aime</button>
        <button type="button" data-fb="nope" class="${fb === "nope" ? "on" : ""}">Pas intéressé</button>
        <button type="button" data-follow>Suivre ce process</button>
      </div>`;
  };

  let current = "dashboard";
  let openJob = null;
  let tlView = "tech";

  const drawChart = () => {
    const list = DEMO_DOMAINS[tlView];
    const last = MONTHS.length - 1;
    barChart(main.querySelector("#demo-bars"), {
      items: [...list].sort((a, b) => b[2][last] - a[2][last]).map(([, label, values]) => ({
        label, value: values[last], display: `${Math.round(values[last] * 100)} %`,
        tip: `${Math.round(values[last] * MONTHS[last].base)} startups sur ${MONTHS[last].base} en ${MONTHS[last].label}`,
      })),
    });
    lineChart(main.querySelector("#demo-chart"), {
      months: MONTHS, unit: "startups",
      series: list.map(([key, label, values]) => ({ key, label, values })),
      selected: list.slice(0, 3).map(([key]) => key),
    });
  };

  const render = () => {
    navButtons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === current)));
    const label = { dashboard: "tableau de bord", jobs: "postes", analytics: "analytics", companies: "entreprises",
                    process: "mes process", chat: "assistant" }[current];
    path.textContent = openJob ? `mire · postes · ${openJob.title.toLowerCase()}` : `mire · ${label}`;
    main.innerHTML = openJob ? detail(openJob) : views[current]();
    if (!openJob && current === "analytics") drawChart();
    main.style.animation = "none";
    void main.offsetWidth; // relance l'animation d'apparition
    main.style.animation = "";
  };

  root.addEventListener("click", (e) => {
    const nav = e.target.closest("[data-view]");
    if (nav) { current = nav.dataset.view; openJob = null; return render(); }
    const tl = e.target.closest("[data-tl]");
    if (tl) { tlView = tl.dataset.tl; return render(); }
    if (e.target.closest("[data-view-back]")) { current = "jobs"; openJob = null; return render(); }
    const jobBtn = e.target.closest("[data-job]");
    if (jobBtn && jobBtn.dataset.job) {
      openJob = JOBS.find((j) => j.id === Number(jobBtn.dataset.job));
      current = "jobs";
      return render();
    }
    const fb = e.target.closest("[data-fb]");
    if (fb && openJob) {
      const value = fb.dataset.fb;
      feedback[openJob.id] = feedback[openJob.id] === value ? undefined : value;
      render();
      if (feedback[openJob.id] === "like") toast("Noté. Après quelques « J'aime », Mire vous proposera une règle : « Priorité aux postes de premier ingénieur ».");
      else if (feedback[openJob.id] === "nope") toast("Noté. Règle proposée : « Écarter les postes sur site à 100 % ». Vous la validez ou non.");
      return;
    }
    if (e.target.closest("[data-follow]")) return toast("Ajouté à « Mes process », statut « À postuler ».");
    const ask = e.target.closest("[data-ask]");
    if (ask) {
      const [q, a] = CHAT[Number(ask.dataset.ask)];
      const chat = main.querySelector("#demo-chat");
      chat.insertAdjacentHTML("beforeend", `<div class="bubble me">${esc(q)}</div><div class="bubble typing"></div>`);
      const bubble = chat.lastElementChild;
      const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
      let i = reduce ? a.length : 0;
      const step = () => {
        i = Math.min(a.length, i + 3);
        bubble.textContent = a.slice(0, i);
        if (i < a.length) setTimeout(step, 16);
        else bubble.classList.remove("typing");
      };
      step();
      main.scrollTop = main.scrollHeight;
    }
  });

  render();
}
