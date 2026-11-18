import { $, STATUSES, boot, fmtToday, html, loadMe, pill } from "./app.js";

const HELP = {
  nouveau: "Poste repéré, pas encore étudié",
  a_postuler: "Poste retenu, candidature à préparer",
  postule: "Candidature envoyée",
  relance: "Pas de réponse : relance à faire ou faite",
  entretien: "Un ou plusieurs entretiens en cours",
  offre: "Proposition reçue",
  refuse: "Candidature refusée",
  abandonne: "Process arrêté de votre côté",
};

const memberSince = new Intl.DateTimeFormat("fr-FR", { month: "long", year: "numeric" });

async function render() {
  const { user, counts } = await loadMe();
  const name = user.name ?? [user.given_name, user.family_name].filter(Boolean).join(" ");
  $("#identity").innerHTML = html`
    ${user.picture
      ? html`<img class="avatar" src="${user.picture}" alt="" referrerpolicy="no-referrer">`
      : html`<span class="avatar">${(name || "?")[0].toUpperCase()}</span>`}
    <div><h2>${name || "Utilisateur"}</h2><p class="muted small">Connecté avec LinkedIn</p></div>`.s;

  const rows = [
    ["E-mail", user.email ?? "Non communiqué"],
    ["Membre depuis", memberSince.format(new Date(user.created_at))],
    ["Process suivis", counts.processes],
    ["Aujourd'hui", fmtToday()],
  ];
  $("#details").innerHTML = html`${rows.map(([k, v]) => html`
    <div class="row-item"><span class="grow muted small">${k}</span><span>${v}</span></div>`)}`.s;

  $("#legend").innerHTML = html`${STATUSES.map(([k]) => html`
    <div class="item"><span style="min-width:110px">${pill(k)}</span><span class="small muted">${HELP[k]}</span></div>`)}`.s;
}

boot(render);
