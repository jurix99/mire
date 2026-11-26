// Mire — contenu des offres Free / Pro, partagé par la landing (landing.js) et la fenêtre « Passer Pro » (app.js).
// Les chiffres viennent de l'API (PLAN_LIMITS côté serveur), jamais écrits en dur ici.

const plural = (n, word) => `${n} ${word}${n > 1 ? "s" : ""}`;

/** Avantages de chaque offre : [inclus (bool), texte]. free / pro : limites renvoyées par l'API. */
export function planFeatures(free = {}, pro = {}) {
  return {
    free: [
      [true, `${plural(free.visible_jobs ?? 2, "offre")} bien notée${(free.visible_jobs ?? 2) > 1 ? "s" : ""} pour vous, visible${(free.visible_jobs ?? 2) > 1 ? "s" : ""} en entier`],
      [false, "Votre meilleure offre et toutes les autres"],
      [true, `${plural(free.assessments_per_week ?? 20, "évaluation")} par semaine`],
      [true, `${free.active_processes ?? 10} process actifs`],
      [true, `${plural(free.imports_total ?? 1, "import")} de profil, ${plural(free.chat_per_month ?? 10, "message")} à l'assistant par mois`],
      [false, "Levées de fonds, investisseurs et ATS"],
      [false, "Analyse du profil et synthèse des règles"],
      [false, "Candidatures préparées (formulaire pré-rempli, CV adapté, lettre)"],
    ],
    pro: [
      [true, "Toutes les offres, la plus pertinente comprise"],
      [true, "Levées de fonds, investisseurs, effectif et ATS"],
      [true, "Notes et analyses de l'IA sur toutes les offres"],
      [true, "Évaluations prioritaires, sans plafond"],
      [true, "Process et imports de profil illimités"],
      [true, "Analyse du profil et synthèse des règles"],
      [true, `${pro.chat_per_month ?? 300} messages à l'assistant par mois`],
      [true, `${pro.prepare_application_per_day ?? 5} candidatures préparées par jour : formulaire pré-rempli, CV adapté, lettre`],
    ],
  };
}
