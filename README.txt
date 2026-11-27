Mire (ex-maquette Framer "Incredible Info", ex-"job radar")

------------------------------------------------------------------
Architecture
------------------------------------------------------------------
  frontend/   HTML/CSS/JS sans build (modules ES), reprend le design de la maquette Framer
              - assets/app.css      styles + couleurs des statuts (variables --st-*)
              - assets/js/app.js    code partagé : appels API, barre latérale, pastilles, logos, formulaires,
                                    J'aime / Pas intéressé, fiche d'une offre, chat flottant de l'assistant
              - assets/js/<page>.js une page = un script
              - pages de l'app      Offres (/postes, accueil), Entreprises, Candidatures (/mes-process), Profil
                                    (/mon-profil), Sources (admin : sources, passages, santé du moteur) ;
                                    /tableau-de-bord et /analytics (supprimées) redirigent vers /postes
              - index.html          landing publique à la racine « / », connecté ou non (assets/landing.css,
                                    assets/js/landing.js) ; l'app commence à /tableau-de-bord (tableau-de-bord.html).
                                    Un visiteur non connecté sur une page de l'app est renvoyé vers « / » (nginx en local,
                                    app/frontend.py en production) ;
                                    le bouton LinkedIn ramène à la page demandée. La déconnexion revient sur « / ».
  backend/    FastAPI (projet uv) : connexion LinkedIn OpenID Connect, session, API JSON /api/...
              - app/models.py       tables (SQLAlchemy)
              - app/services.py     fonctions métier partagées par l'API et les outils du chat
              - app/profile.py      profil : schémas (règles, barème…), fusion, résumé pour le chat et la notation
              - app/proposals.py    propositions de modification du profil, validées élément par élément
              - app/profile_api.py  /api/profile, imports (linkedin_export.py, pdf_import.py), analyse (analysis.py)
              - app/assessments.py  évaluation automatique des offres par utilisateur (+ tâche de fond)
              - app/ranking.py      classement des offres et des entreprises (reclassement, sans LLM)
              - app/feedback.py     retours J'aime / Pas intéressé ; rules_synthesis.py : synthèse des règles
              - app/radar_api.py    sources, passages, vérification des offres via les ATS (ats.py), dedup.py
              - app/agent_api.py    API de l'agent de recherche (/api/agent, jeton AGENT_TOKEN)
              - app/llm.py          client Gemini (SDK google-genai)
              - app/chat.py         /api/chat ; app/chat_tools.py : outils de l'assistant
              - app/tasks.py        file de tâches dans PostgreSQL (table tasks, SKIP LOCKED) ; handlers :
                                    task_handlers.py ; traitements hors API : python -m app.jobs (jobs.py)
              - app/matching.py     présélection avant Gemini : exclusions dures + classement (ranking.py) ;
                                    embeddings.py : modèle multilingue local via fastembed, textes lus en entier
              - app/frontend.py     front servi par FastAPI en production (sans nginx), limites de taille
              - app/taxonomy.py     LISTE FERMÉE des catégories (familles de postes, compétences, secteurs)
              - app/classify.py     classement de chaque offre sur la liste (règles, puis Gemini à énumérations)
              - app/market.py       agrégats du marché (page Analytics, baromètre public) ; analytics_api.py
              - app/trial.py        essai gratuit de la landing sans compte : lien LinkedIn -> 3 startups (Gemini +
                                    recherche Google), liens gardés 12 mois (trial_leads) ; export admin
                                    /api/admin/trial-leads?format=csv ; limites TRIAL_PER_IP, TRIAL_DAILY_CAP
              - app/engine_health.py  santé du moteur (offres, évaluations, bascule de modèle, file, alertes)
              - app/apply_forms.py  formulaires de candidature des offres (un adaptateur par ATS, GET publics)
              - app/application_prep.py  préparation d'une candidature : CV adapté, contrôle anti-invention,
                                    lettre, réponses ; app/pdf.py : PDF (WeasyPrint, image worker) ;
                                    app/application_api.py : API de la page Candidature
              - app/digest.py       récapitulatif des nouvelles offres par e-mail (job quotidien), réglage et
                                    désabonnement ; app/mailer.py : envoi SMTP (SMTP_HOST vide = aucun e-mail)
              - app/restore_check.py  test de restauration de la sauvegarde (workflow restore-test.yml)
              - tests/              tests pytest (SQLite, Gemini simulé : aucun appel réseau) ; test_postgres.py
                                    (marqueur postgres) : seulement avec TEST_POSTGRES_URL
              - scripts/eval_reference.py  évaluation de référence avec le vrai Gemini (voir plus bas)
              - app/relevance_eval.py + scripts/export_judged.py, eval_relevance.py : mesure de la
                                    pertinence de la notation sur les offres jugées (voir plus bas)
  agent/      agent de recherche : prompt.md, prompt-offres.md, mire_api.py ; run_agent.py (serveur : jobs
              mire-agent-*, plafond de dépense) ; run-agent.ps1, install-task.ps1 (poste Windows)
  docs/       processus de recherche (processus-recherche.md), API de l'agent (agent.md), exemple de profil,
              déploiement sur Azure (deploiement-azure.md)
              - migrations/         migrations Alembic (local : au démarrage ; production : job mire-migrate)
              - app/seed.py         données de démonstration
  db          PostgreSQL 17 + pgvector (volume Docker postgres-data)
  infra/      Terraform : bootstrap/ (une fois, en local) et main/ (appliqué par la CI)
  Dockerfile  images de production : cibles api et worker (linux/amd64)
  .github/    CI (tests, terraform plan) et déploiement (deploy.yml)

Tables :
  users                 un compte par identifiant LinkedIn (créé à la première connexion) ; récapitulatif par
                        e-mail : fréquence (weekly par défaut, daily, off), dernier envoi, jeton de désabonnement
  companies             entreprises (site web, levée, logo mis en cache, ATS + identifiant, page carrières,
                        dernière vérification, « vérifiée sans offre pertinente », effectif)
  jobs                  postes, rattachés à une entreprise ; URL normalisée unique (dédoublonnage), source,
                        mode, déplacements, salaire, publication, ouverture vérifiée, fermeture
  sources               où chercher (ATS, job boards, fonds VC, radars de levées, requêtes Google…), partagées
  search_runs           passages de recherche de l'agent (compteurs, résumé, top par utilisateur)
  applications          process de candidature : un utilisateur x un poste, avec statut
  application_events    historique des changements de statut (+ instantané de ce qui a été envoyé)
  application_forms     formulaire de candidature d'une offre lu sur l'ATS (partagé, relu après 7 jours)
  application_drafts    candidature préparée d'un process : réponses, CV adapté (structuré + texte), lettre, statut,
                        avertissements, tokens Gemini
  generated_files       PDF générés (CV adapté, lettre) ; exportés (RGPD) et supprimés avec le compte
  application_generations  journal des préparations / régénérations (quotas par jour, tokens)
  user_profiles         profil riche (1-1 avec users, créé à la volée) : identité pro, positionnement,
                        expériences, formation, compétences (points forts / à développer), préférences
                        (dont déplacements), règles « éviter / préférer » (origine, exemples, active),
                        entreprises exclues, offres de référence, barème, version (-> réévaluation)
  profile_imports       propositions en attente (import, analyse, synthèse des règles), validées élément
                        par élément (24 h)
  job_assessments       évaluation CALCULÉE d'une offre pour un utilisateur : note /10, détail par critère,
                        verdict (nouveau / écartée), raison, pourquoi, compétences ; note corrigée à la main
  job_feedback          retour courant sur une offre : J'aime / Pas intéressé (raisons, commentaire) ; classement et
                      synthèse des règles
company_feedback      retour courant sur une entreprise (J'aime / Pas intéressé) ; classement
  chat_conversations    conversations avec l'assistant (la plus récente est active)
  chat_messages         messages (utilisateur / assistant) et outils appelés
  chat_actions          écritures proposées par l'assistant, exécutées seulement après confirmation :
                        sert aussi de journal (statut, résultat, date d'exécution)
  subscriptions         droits au Pro achetés via Stripe, écrits uniquement par le webhook : abonnement
                        mensuel (statut Stripe, fin de période, résiliation à la fin de la période, impayé
                        depuis) ou Pass 3 mois (paid / refunded, fin du Pass) ; renonciation au droit de
                        rétractation horodatée. users.stripe_customer_id relie le compte au client Stripe.
  stripe_events         identifiants des événements Stripe déjà traités (webhook idempotent)
  tasks                 file des tâches de fond (collect, embed_jobs, match_user, assess_user, check_company,
                        daily) : statut, tentatives, exécution différée, erreur
  (pgvector)            jobs.embedding / user_profiles.embedding (384 dimensions), job_assessments.similarity

Statuts posés par l'utilisateur : nouveau, à regarder, prête à envoyer (uniquement par « Marquer prête à envoyer »
de la page Candidature), postulé, relance, entretien, offre, refusé,
pas intéressé, abandonné. Statut affiché : celui posé par l'utilisateur, sinon « Offre fermée » si l'offre n'est plus ouverte, sinon le
verdict de l'évaluation (« Nouveau » ou « Écartée (auto) »). Couleurs : variables --st-* dans frontend/assets/app.css.

Logos : récupérés par le backend depuis "URL du logo" si renseignée, sinon depuis le favicon du site web
(services Google puis DuckDuckGo : seul le domaine de l'entreprise leur est envoyé), puis mis en cache en base.
Import manuel possible depuis la fiche entreprise. Sans logo, des initiales colorées s'affichent.

------------------------------------------------------------------
Lancer en local avec Docker (WSL) + connexion LinkedIn
------------------------------------------------------------------
Config : copier .env.example en .env et renseigner LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET / SESSION_SECRET
         (app LinkedIn avec le produit "Sign In with LinkedIn using OpenID Connect",
          URL de redirection : http://localhost:8080/auth/callback).
         Assistant (facultatif) : GEMINI_API_KEY (https://aistudio.google.com/apikey),
           GEMINI_MODEL (défaut gemini-3.8-flash), GEMINI_FALLBACK_MODEL (liste, défaut
           gemini-3.5-flash-lite,gemini-3.1-flash-lite : utilisés si le précédent est saturé ou hors quota),
           GEMINI_EVAL_MODEL (liste, notation des offres, modèles les moins chers d'abord),
           ASSESSMENT_WORKER (true/false, tâche de fond d'évaluation), ASSESSMENT_INTERVAL_S (défaut 120).
         Agent de recherche (facultatif) : AGENT_TOKEN (jeton de /api/agent ; vide = API désactivée),
           AGENT_SCHEDULE (texte affiché, défaut « le lundi à 7h43 »). Voir docs/agent.md.
         Collecte : COLLECTOR (true/false), COLLECT_TIME (HH:MM, heure de Paris, défaut 06:30).
         Tâches de fond : RUN_BACKGROUND_THREADS (défaut true : évaluation, planificateur de collecte et file de
           tâches tournent dans le processus de l'API, comme avant ; false en production), MATCH_TOP_K (offres
           les mieux classées envoyées à Gemini, défaut 50), FINISH_BUDGET_S (fin de passage de l'agent, défaut 90).
           Embeddings : activés avec docker compose (groupe uv « worker », modèle téléchargé au premier calcul dans le
           volume embedding-models ; premier calcul de ~450 offres : environ 3 min). Hors Docker : uv sync
           --group worker et FASTEMBED_CACHE_PATH=<dossier du modèle>. Sans eux, le classement se fait sans le
           critère sémantique.
         Journaux : LOG_FORMAT (json par défaut ; text dans docker-compose.yml).
         Offre Pro (facultatif, voir « Offres Free / Pro et paiement Stripe ») : BILLING_ENABLED (défaut false :
           tout le monde est Pro et admin), PRO_USER_EMAILS, ADMIN_EMAILS, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
           STRIPE_PRICE_MONTHLY, STRIPE_PRICE_PASS_3M, PUBLIC_URL (URLs de retour de Checkout).
           Sans clé : chat grisé, import PDF / texte, analyse et évaluation automatique désactivés ;
           le reste fonctionne.
         Après modification de .env : docker compose up -d backend   (un simple restart ne relit pas .env)

Lancer (dans WSL, depuis ce dossier) :
  docker compose up -d
  -> http://localhost:8080        site avec connexion LinkedIn (landing sur « / », offres sur /postes)
  -> http://localhost:8081        site sans connexion, avec un utilisateur "dev" (dev local uniquement)
  -> localhost:5432               PostgreSQL (base/utilisateur jobradar, mot de passe POSTGRES_PASSWORD, "jobradar" par défaut)

Données de démo de la maquette (6 entreprises, 8 FAUX postes si la base est vide, 3 process de démo par nouvel
utilisateur) : désactivées par défaut. Activer pour une démo : SEED_DEMO: "true" dans docker-compose.yml.

Commandes utiles :
  docker compose logs -f backend                         logs de l'API
  docker compose exec db psql -U jobradar                console SQL
  docker compose down -v                                 tout arrêter ET effacer la base

Passage à l'image pgvector (base créée avant, avec postgres:17-alpine) : les données sont compatibles, mais
l'ordre de tri du texte diffère entre Alpine (musl) et Debian. Une fois, après le premier `docker compose up -d` :
  docker compose exec db psql -U jobradar -c "REINDEX DATABASE jobradar" -c "ALTER DATABASE jobradar REFRESH COLLATION VERSION"

Modifier le modèle (backend/app/models.py), puis générer et vérifier la migration :
  docker compose run --rm backend uv run alembic revision --autogenerate -m "description"
  docker compose restart backend                         (applique la migration)

Après modif de backend/pyproject.toml : docker compose run --rm --no-deps backend uv lock

Routes : /auth/login, /auth/callback, /auth/me, /auth/logout
         /healthz, /readyz                            SANS session : sondes (liveness ; readiness avec SELECT 1)
         /api/public/landing, /api/public/logos/{id}   SANS session (landing) : prix et limites (PLAN_LIMITS),
                                                      chiffres du radar, logos des seules entreprises visibles en Free
         /api/me, /api/dashboard, /api/analytics, /api/statuses
         /api/me/export, DELETE /api/me               RGPD : export JSON de toutes vos données, suppression du compte
         /api/me/digest                               GET, PUT {frequency: weekly|daily|off} : récapitulatif par e-mail
         /api/public/email/desabonnement?t=<jeton>    SANS session : GET page de confirmation (ne change rien),
                                                      POST désabonnement (bouton, ou « en un clic » des messageries)
         /api/analytics/market?domain=&family=&period=&count_by=   onglet Marché (Free : masqué en partie)
         /api/analytics/me                            onglet « Toi face au marché » (Pro)
         /api/admin/taxonomy-health                   admin : part « autre », libellés hors liste, compétences absentes
         /api/public/barometer                        SANS session : baromètre (agrégats, 24 h de cache)
         /mentions-legales, /cgv, /confidentialite    pages légales, publiques (sans session)
         /api/companies[/{id}[/logo]], /api/jobs[/{id}[/status]], /api/applications[/{id}]
                                                      offres et entreprises avec leur pertinence (match) ;
                                                      /api/jobs?brief=true : sans les descriptions
         /api/options                                 choix des filtres et du profil (métiers, secteurs, lieux…)
         /api/companies/{id}/reaction                 PUT {value: like|dislike|null}
         /api/profile                                 GET, PATCH (champs envoyés remplacés)
         /api/profile/import/linkedin-export          POST, ZIP de l'export de données LinkedIn (20 Mo max)
         /api/profile/import/pdf                      POST, PDF du profil ou CV (10 Mo max, via Gemini)
         /api/profile/import/text                     POST {text}, texte collé (via Gemini)
         /api/profile/proposals/{id}                  GET ; DELETE l'abandonne
         /api/profile/proposals/{id}/apply            POST {accept: [ids d'éléments]} (absent : éléments cochés)
         /api/profile/analyze                         POST, déductions sur le profil -> proposition
         /api/profile/rules/synthesize                POST, règles déduites des retours -> proposition
         /api/jobs/{id}/feedback                      POST {sentiment: like|dislike, reasons, text} (remplace le
                                                      retour précédent) ; DELETE le retire
         /api/jobs/{id}/similar                       GET, 5 offres ouvertes les plus proches (pgvector, sans LLM)
         /api/jobs/{id}/application/prepare           POST {refresh?, want_letter?} : prépare la candidature (Pro)
         /api/jobs/{id}/application/draft             GET, PATCH {answers: [{field_id, value}], cv_text, cover_letter}
         /api/jobs/{id}/application/draft/fields/{field_id}/regenerate   POST {instruction} (« cover_letter » : la lettre)
         /api/jobs/{id}/application/files/{cv|lettre} GET, PDF (propriétaire seulement)
         /api/jobs/{id}/application/ready | /sent     POST : « Prête à envoyer » / « Postulé » + instantané
         /api/applications/{id}/events/{event_id}/snapshot   GET, ce qui a été envoyé
         /api/profile/application-defaults            PUT, réponses types des candidatures
         /api/feedback, /api/feedback/reasons         GET, retours récents (nombre de nouveaux), raisons
         /api/jobs/{id}/assessment/manual             PUT {score|null}, correction manuelle de la note
         /api/assessments/status, /api/assessments/run   GET état, POST évaluer tout de suite (10 offres)
         /api/companies/{id}/check                    POST, vérifie les offres dans la liste de l'ATS
         /api/companies/{id}/postings/import          POST {ats_job_ids}, ajoute des offres listées par l'ATS
         /api/sources[/{id}]                          GET, POST, PATCH (activer / désactiver)
         /api/runs                                    GET, passages (découverte, collecte) + résumé personnel
         /api/collect/status, /api/collect/run        GET état, POST collecte immédiate (tâche mise en file)
         /api/agent/...                               API de l'agent, par jeton (docs/agent.md)
         /api/chat/status, /api/chat/history          GET
         /api/chat                                    POST {message, mode: normal|onboarding}
         /api/chat/reset                              POST, nouvelle conversation
         /api/chat/actions/{id}/confirm | /cancel     POST, exécute ou abandonne une proposition
         /api/billing/checkout                        POST {plan: monthly|pass_3m, waive_withdrawal} -> {url}
         /api/billing/portal                          POST -> {url} du Customer Portal Stripe
         /api/billing/webhook                         POST, Stripe uniquement (signature, sans session)

Tests (depuis backend/, sans Docker) : uv run pytest
  (sur le réseau de l'entreprise : UV_SYSTEM_CERTS=1 pour que uv accepte le proxy HTTPS)
  Tests PostgreSQL (SKIP LOCKED, pgvector, migrations), sur une base JETABLE (schéma public recréé) :
    docker run --rm -d -p 55432:5432 -e POSTGRES_PASSWORD=test pgvector/pgvector:pg17
    TEST_POSTGRES_URL=postgresql+psycopg://postgres:test@localhost:55432/postgres uv run pytest -m postgres
Évaluation de référence avec le vrai Gemini (base SQLite temporaire, la base de l'app n'est pas touchée) :
  docker compose exec backend uv run python scripts/eval_reference.py
  -> rejoue les retours de docs/processus-recherche.md (§7), vérifie que les règles proposées couvrent les
     thèmes de docs/exemple-profil.txt et que Remanence est mieux notée qu'Ooak Data et Bluesand.
Mesure de la pertinence de la notation (à refaire avant / après chaque changement du moteur) :
  1. figer les offres jugées d'un utilisateur (J'aime, Pas intéressé, Postulé…), une fois, sur la vraie base :
     docker compose exec backend uv run python scripts/export_judged.py        -> eval_data/user-<id>.json
     (contient le profil : eval_data/ est ignoré par git, ne pas partager)
  2. rejouer la notation de production (exclusions dures + Gemini) en base temporaire et mesurer :
     docker compose exec backend uv run python scripts/eval_relevance.py eval_data/user-1.json --name avant
     -> précision @5/@10/@20 (part d'offres retenues ou aimées parmi les mieux notées), AUC, positives
        écartées et négatives retenues (avec leurs raisons) ; --repeat 3 : stabilité des notes ;
        --models : autre modèle ; --variant v2 : notation v2 (voir assessments.py, SCORING_VARIANT) ;
        validation croisée sur 2 plis par défaut (--folds 1 : profil tel quel)
  3. comparer deux mesures : scripts/eval_relevance.py --compare avant apres

------------------------------------------------------------------
Importer son profil LinkedIn
------------------------------------------------------------------
La connexion LinkedIn (OpenID Connect) ne donne que le nom, l'e-mail, la photo et la langue.
LinkedIn ne fournit pas le profil complet aux applications, et Mire ne lit jamais linkedin.com.
Depuis la page « Mon profil », bloc « Importer depuis LinkedIn » :
  1. Export de données (recommandé, sans Gemini) :
     LinkedIn > Moi > Préférences et confidentialité > Confidentialité des données >
     Obtenir une copie de vos données > choisir Profil, Postes, Compétences, Formation, Langues
     (ou l'archive complète) > Demander l'archive. Déposer le ZIP reçu par e-mail, sans le décompresser.
     Fichiers lus : Profile.csv, Positions.csv, Skills.csv, Education.csv, Languages.csv (le reste est ignoré).
     Le ZIP est lu en mémoire (limites de taille et de nombre de fichiers), jamais extrait sur disque.
  2. PDF : sur votre profil LinkedIn, Plus > Enregistrer au format PDF (ou un CV). Texte extrait puis
     structuré par Gemini.
  3. Texte collé : même traitement que le PDF.
Chaque import affiche un aperçu à cocher. Les ajouts sont cochés ; le remplacement d'une valeur déjà
saisie ne l'est pas. Rien n'est supprimé : compétences, expériences et règles existantes sont gardées.

------------------------------------------------------------------
Assistant (chat Gemini)
------------------------------------------------------------------
Bouton en bas à droite sur toutes les pages. Contexte envoyé à Gemini : instruction système (rôle, règles,
date du jour, résumé du profil) + les 20 derniers messages + la description des outils.
  Lecture (exécutée directement) : get_profile, list_jobs, get_job (avec le détail de la note),
    list_applications, get_dashboard
  Écriture (proposition à confirmer) : update_profile, set_job_status, update_application, create_job,
    rate_job (correction manuelle de la note)
  Affichage : ask_question (réponses rapides sous une question)
Mode entretien (« Compléter mon profil avec l'assistant ») : le serveur calcule les sujets non renseignés
(lieux, mode, stades, salaire, cloud, déplacements, secteurs, à éviter, offres aimées, rôles) ; l'assistant
pose une question à la fois, uniquement sur eux, avec des réponses rapides ; chaque réponse devient une
proposition update_profile à confirmer.
Sécurité :
  - les outils n'agissent que sur l'utilisateur connecté (son id vient de la session, jamais du modèle) ;
  - aucun outil de suppression ;
  - une écriture n'est exécutée qu'après clic sur « Confirmer » (recalculée sur les données à jour) ;
    les changements de statut passent par application_events, comme dans l'API ;
  - profil, postes et contenus importés sont présentés au modèle comme des données, pas des consignes ;
  - 30 messages / 10 min par utilisateur, 4000 caractères par message ;
  - la clé Gemini reste côté serveur ; les actions exécutées sont journalisées (table chat_actions,
    logs « mire.chat.actions »).

------------------------------------------------------------------
Profil appris et évaluation des offres (voir docs/processus-recherche.md)
------------------------------------------------------------------
1. Faits : import (EXTRACT_INSTRUCTION reste factuelle).
2. Déductions : « Analyser mon profil » -> positionnement, rôles classés, compétences fortes / à développer,
   années d'expérience (calculées depuis les dates, stages exclus), questions. Badge « déduit », justification,
   confiance ; validation une par une ; une valeur saisie n'est jamais remplacée sans être cochée.
3. Évaluation automatique, par utilisateur, sans confirmation (donnée calculée) :
   - exclusions dures sans LLM : entreprise exclue, stage / alternance, expérience demandée >= la vôtre + 2 ans ;
   - Gemini (par lots) : correspondance par critère, ajustement par les règles, exclusion issue d'une règle
     (seulement si l'offre l'exige explicitement, jamais pour une offre aimée), « pourquoi », compétences ;
   - note /10 et verdict calculés côté serveur avec VOTRE barème et VOTRE seuil (Mon profil > Barème) ;
   - règle d'or : un statut posé par vous ou une note corrigée ne sont jamais modifiés ; quand le profil
     change, seules les offres non traitées sont réévaluées.
4. Retours : « J'aime » / « Pas intéressé » en un clic, sur les offres et les entreprises, annulables ; ils
   reclassent tout de suite et NE relancent PAS la notation Gemini (un J'aime ajoute une offre de référence sans
   changer la version du profil). Raisons facultatives (« Dire pourquoi ») ; « Déduire des règles de mes
   retours » (Profil) : règles proposées citant les offres d'origine, validées une par une. « Déjà passé en
   entretien » propose d'exclure l'entreprise.

------------------------------------------------------------------
Classement des offres et des entreprises (backend/app/ranking.py)
------------------------------------------------------------------
Calculé à la lecture (environ 0,1 s pour 450 offres), sans appel LLM. Pertinence de 0 à 99 :
- adéquation : métier visé (liste fermée, familles proches), compétences exigées couvertes, niveau, lieu et
  télétravail, salaire, secteur, stade, proximité sémantique (embeddings) ; mêlée à la note Gemini (60 %) quand
  l'offre est évaluée ; une information absente n'est pas comptée ;
- goût : critères communs avec les offres aimées / écartées (une raison « trop front » vise le front, pas tout le
  profil de l'offre), proximité sémantique de ces offres, entreprises aimées ;
- fraîcheur (petit bonus), malus aux candidatures spontanées.
Les réglages sont en tête du fichier. « Pas dans les propositions » : offre ou entreprise écartée, exclusion
dure (recalculée à la lecture : un changement de profil s'applique aussitôt), écartée par Gemini, fermée.
Le même classement choisit les offres envoyées à Gemini. La visibilité Free (entitlements.rank_jobs) n'en
dépend pas : écarter des offres ne doit pas en dévoiler d'autres.
Les réglages ne sont pas encore mesurés sur des jugements réels : après quelques semaines de retours, comparer
la pertinence (scripts/export_judged.py) avant de les ajuster.

------------------------------------------------------------------
Radar et agent de recherche (voir docs/processus-recherche.md et docs/agent.md)
------------------------------------------------------------------
- Dédoublonnage : même URL normalisée (sans utm_*, ref…, « / » final) ou même entreprise + même titre
  -> refusé (409). Une offre déjà vue n'est jamais reproposée.
- Vérification par les ATS (Ashby, Lever, Greenhouse, Recruitee) : bouton « Vérifier les offres » d'une
  entreprise (ATS + identifiant renseignés). Offre absente de la liste -> « Offre fermée » chez ceux qui ne
  l'ont pas traitée (plus évaluée). Une liste vide ou une erreur ne ferme rien. Les nouvelles offres listées
  peuvent être ajoutées, quelle que soit leur date de publication (encore listées = ouvertes).
Trois processus :
  1. Découverte (chaque lundi à 7h43, agent Claude Code ; en production : job mire-agent-decouverte, voir
     docs/deploiement-azure.md, sinon tâche planifiée locale) : entreprises françaises qui ont levé des fonds
     + identifiant de leur ATS, testé par Mire avant d'être enregistré.
       powershell -ExecutionPolicy Bypass -File agent\install-task.ps1             installer la tâche
       powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1                découverte à la demande
     Outils limités à la recherche web, la lecture de pages et l'API /api/agent. Journal : agent\logs\.
  2. Collecte (chaque jour à COLLECT_TIME, défaut 6h30, par Mire, sans LLM) : toutes les offres des ATS
     lisibles, description complète comprise ; offres disparues fermées ; ATS inconnu détecté automatiquement
     (au plus une fois par semaine). Rattrapée au démarrage si la dernière date de plus de 20 h.
     Bouton « Collecter maintenant » dans Sources > Passages.
  3. Pertinence (en continu, par Mire) : pour chaque utilisateur, pré-filtre gratuit (entreprise exclue,
     stage, expérience, métier hors cible, lieu explicitement à l'étranger), puis Gemini note le reste avec
     son barème. Les passages et leur résumé apparaissent dans Sources > Passages et le bloc « Veille ».

------------------------------------------------------------------
Récapitulatif des nouvelles offres par e-mail (backend/app/digest.py)
------------------------------------------------------------------
Ce qui fait revenir dans Mire entre deux visites. Envoyé par le job quotidien, après l'évaluation :
- à qui : comptes avec une adresse e-mail (celle de LinkedIn), selon la fréquence choisie dans Profil > Compte :
  chaque semaine (défaut), chaque jour ou jamais ; Free compris (même masquage que l'app) ;
- quoi : les offres AJOUTÉES depuis le dernier récapitulatif, ouvertes, sans process, proposées par le classement
  (ni écartées, ni exclues) et pertinentes (pertinence >= 60) ; les 5 meilleures, avec lieu, mode, salaire,
  « vérifiée il y a X h sur le site de l'entreprise » et, en Pro, les raisons du classement. Free : les offres
  floutées sont seulement comptées (ni titre ni entreprise), avec un lien vers « Passer Pro » ;
- rien de nouveau : pas d'e-mail ;
- boutons Voir l'offre / J'aime / Pas intéressé : des liens /postes?offre=<id>&retour=like|dislike. La page, une fois
  connecté, ouvre l'offre et applique le retour (annulable) ; sans session, rien n'est enregistré, et la connexion
  ramène au lien ;
- désabonnement : lien avec un jeton propre à l'utilisateur (users.email_token) et en-têtes List-Unsubscribe /
  List-Unsubscribe-Post (désabonnement en un clic des messageries).
Configuration : SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_REPLY_TO (voir .env.example) ; SMTP_HOST vide =
aucun e-mail. En production : variable GitHub MIRE_DIGEST_ENABLED et secret smtp-password (docs/deploiement-azure.md).
Réglages (seuil, nombre d'offres, fréquences) en tête de digest.py.

------------------------------------------------------------------
Offres Free / Pro et paiement Stripe
------------------------------------------------------------------
Prix (TTC, TVA française via Stripe Tax) : Pro mensuel 15 €/mois sans engagement (abonnement Stripe), Pass
3 mois 39 € en paiement unique (90 jours, sans renouvellement ; un Pass acheté pendant un autre le prolonge
de 90 jours à partir de sa fin). Pas d'essai gratuit. Toutes les limites sont dans PLAN_LIMITS
(backend/app/entitlements.py), nulle part ailleurs.

Free : les offres ouvertes sont classées par pertinence pour l'utilisateur (retenues avant écartées, note,
similarité, date ; entitlements.rank_jobs). La plus pertinente reste floutée (aperçu du Pro), les 2
suivantes sont en clair avec leur note, toutes les autres sont floutées (seule la date relative reste) ;
une offre suivie (process) reste en clair, et ne libère pas sa place dans le classement ; pas de process
sur une offre floutée (402). Entreprise sans aucune offre en clair : carte entièrement floutée sauf le
secteur. Levées, investisseurs, effectif, ATS, page carrières toujours verrouillés ; « pourquoi » et
compétences toujours verrouillés ; récapitulatif par e-mail (offres floutées seulement comptées) ;
20 évaluations Gemini par semaine, après les utilisateurs Pro ; 10
process actifs ; 1 import de profil ; ni analyse du profil ni synthèse des règles ; 10 messages à
l'assistant par mois (Pro : 300). Landing (visiteur non connecté) : entreprises ayant une offre de plus
de 7 jours.

Masquage côté serveur : pour un utilisateur Free, l'API remplace chaque valeur réservée par
{locked: true, placeholder} où placeholder est un texte FACTICE de longueur voisine (services.job_out,
services.company_out, outils du chat). La vraie valeur ne quitte jamais le serveur ; le front floute le
texte factice (classe .locked, helper renderValue de app.js ; ligne ou carte entière : lockedBlock, un
seul flou uniforme et un seul badge) et un clic ouvre la fenêtre « Passer Pro ».
Quota atteint ou fonction réservée : HTTP 402 {code: "quota", kind, limit, used} ou {code: "pro_required"}.

Plan (entitlements.plan_of) : Pro si abonnement actif, impayé (past_due) depuis moins de 3 jours, résilié
avec une fin de période future, ou Pass en cours ; sinon Free. BILLING_ENABLED=false (défaut) : tout le monde
est Pro, rien n'est masqué. PRO_USER_EMAILS (liste séparée par des virgules) : comptes toujours Pro.

Radar partagé (entreprises, postes, logos, sources) : seuls les admins le modifient. ADMIN_EMAILS (liste
séparée par des virgules, e-mails LinkedIn) ; BILLING_ENABLED=false : tout le monde est admin.
Dépendance RequireAdmin (entitlements.py) -> 403 {code: "admin_required"} sur POST/PATCH/DELETE
/api/companies, PUT/DELETE /api/companies/{id}/logo, POST/PATCH/DELETE /api/jobs, POST /api/companies/{id}/check
et /postings/import, GET/POST/PATCH /api/sources, GET /api/collect/status, POST /api/collect/run ; l'outil create_job de l'assistant est
refusé aussi. Le front masque ces boutons et la page Sources (attribut data-admin, classe body.not-admin). L'agent (API à jeton)
et la collecte continuent d'alimenter le radar. Restent ouverts à tous : statuts, process, retours, notes.

Configuration du tableau de bord Stripe (mode test, puis à refaire en mode live) :
  1. Produits : créer le produit « Mire Pro » avec deux prix, TTC (« Inclure la taxe dans le prix ») :
       - 15,00 € récurrent mensuel           -> son identifiant price_… dans STRIPE_PRICE_MONTHLY
       - 39,00 € paiement unique             -> STRIPE_PRICE_PASS_3M
     Catégorie fiscale : « Logiciel en tant que service (SaaS) - usage personnel ».
  2. Stripe Tax : activer, renseigner l'adresse de l'entreprise et l'immatriculation TVA France.
  3. Customer Portal (Paramètres > Billing > Portail client) : autoriser la mise à jour du moyen de paiement,
     l'historique des factures, la résiliation « à la fin de la période de facturation » (pas immédiate) ;
     désactiver le changement de formule.
  4. Webhook (Développeurs > Webhooks) : URL https://<domaine>/api/billing/webhook, événements :
       checkout.session.completed, customer.subscription.created, customer.subscription.updated,
       customer.subscription.deleted, invoice.payment_failed, charge.refunded
     Son secret de signature (whsec_…) -> STRIPE_WEBHOOK_SECRET.
  5. Clé secrète (sk_test_… / sk_live_…, de préférence une clé restreinte) -> STRIPE_SECRET_KEY.
     Aucune clé publique n'est nécessaire (redirection vers Checkout) ; aucune clé n'est envoyée au front
     ni écrite dans les journaux.
  6. Paramètres > Checkout : coordonnées de l'entreprise, conditions générales, politique de remboursement.
nginx laisse /api/billing/webhook passer sans auth_request (sur 8080 et 8081) : seule la signature compte.

Droit de rétractation : la fenêtre « Passer Pro » impose de cocher la renonciation (accès immédiat à un
service numérique) avant la redirection ; elle est horodatée sur la ligne subscriptions et rappelée sous
le bouton de paiement de Checkout.

Tester en local (Stripe CLI, mode test) :
  stripe login
  stripe listen --forward-to localhost:8080/api/billing/webhook
  -> copier le secret whsec_… affiché dans STRIPE_WEBHOOK_SECRET, puis docker compose up -d backend
  Payer avec la carte 4242 4242 4242 4242 (date future, CVC quelconque) ; 4000 0000 0000 0341 : paiement
  refusé au renouvellement. Rejouer un événement : stripe events resend evt_…
  Déclencher sans passer par Checkout : stripe trigger customer.subscription.updated (le webhook relit
  toujours l'abonnement depuis l'API : l'utilisateur doit exister, metadata.user_id ou client connu).
Renouvellement et fin de période avec les test clocks (Billing > Test clocks) :
  1. Créer une horloge, un client rattaché à cette horloge, puis un abonnement au prix mensuel avec
     metadata user_id=<id de l'utilisateur Mire> (ou renseigner users.stripe_customer_id avec ce client).
  2. Avancer l'horloge d'un mois : invoice.paid puis customer.subscription.updated (nouvelle fin de période).
  3. Résilier dans le portal, avancer au-delà de la fin : customer.subscription.deleted -> plan Free.
  4. Carte 4000 0000 0000 0341 puis avancer : invoice.payment_failed, statut past_due (Pro encore 3 jours).

------------------------------------------------------------------
Ancien export Framer
------------------------------------------------------------------
Les fichiers *.html et _fr/ à la racine sont l'ancien export Framer. Ils ne sont plus servis
(nginx sert uniquement frontend/) et peuvent être supprimés.

------------------------------------------------------------------
Production (Azure Container Apps)
------------------------------------------------------------------
Guide complet : docs/deploiement-azure.md (premier déploiement, secrets, domaine, migration des données,
exploitation, coûts). En bref :
  - mire-api (API + front servi par FastAPI, sans nginx), jobs mire-daily (cron), mire-worker (KEDA sur la
    file de tâches) et mire-migrate ; PostgreSQL Flexible Server privé avec pgvector ; secrets dans Key Vault ;
  - infra/bootstrap une fois en local, puis tout par GitHub Actions (OIDC) : push sur main = images GHCR,
    migrations, terraform apply, test de fumée ;
  - traitements à la main : az containerapp job start -g rg-mire-prod -n mire-daily (ou mire-worker) ;
  - en local, `python -m app.jobs daily | worker | migrate` fonctionne aussi :
      docker compose exec backend uv run python -m app.jobs daily

------------------------------------------------------------------
Pages légales et RGPD
------------------------------------------------------------------
Pages : frontend/mentions-legales.html, cgv.html, confidentialite.html (publiques ; liens dans le pied de la
landing, la barre latérale et la fenêtre Pro). Les informations sur l'éditeur sont à compléter : repérer
[À compléter] (identité, SIRET, adresse, contact, directeur de la publication, médiateur de la consommation).
Droits des utilisateurs (backend/app/account.py, « Mon profil > Vos données ») : export JSON, suppression
définitive du compte (refusée tant qu'un abonnement se renouvelle) ; comptes sans connexion depuis 3 ans
supprimés par le job quotidien. Fichiers importés (CV, export LinkedIn) jamais conservés.
Rétractation : pas de case à cocher ; la mention est affichée au-dessus des boutons de paiement, le clic vaut
demande d'accès immédiat et renonciation (horodatée : subscriptions.withdrawal_waived_at), rappelée sur la
page Stripe (custom_text).

------------------------------------------------------------------
Classement des offres sur la liste fermée, et agrégats du marché
------------------------------------------------------------------
La page Analytics a été retirée de l'app (peu utile pour trouver et postuler). Le classement des offres sur la
liste fermée reste essentiel (filtres et pertinence) ; les agrégats et leurs API (/api/analytics*, baromètre
public) existent toujours côté serveur, sans page.
Liste fermée (backend/app/taxonomy.py, seule source de vérité) : 6 domaines, 20 familles de postes (+ autre),
séniorité (4 niveaux), contrat (stage et alternance exclus des statistiques), 114 compétences en 13 familles
avec synonymes, 16 secteurs du produit. On ne crée JAMAIS de catégorie à partir d'une offre : une valeur hors
liste devient « autre » et est comptée (table taxonomy_misses, section admin de l'onglet Marché).
Ajouter / retirer une entrée : modifier taxonomy.py, augmenter TAXONOMY_VERSION, déployer (la liste est
rechargée par la migration, par `python -m app.jobs taxonomy` et à chaque daily ; les offres d'une version
antérieure sont reclassées).

Classement (classify.py, une fois par offre, refait si le titre ou la description change) : règles d'abord
(mots-clés d'intitulé, département de l'ATS, synonymes, salaire, contrat), puis Gemini (GEMINI_EVAL_MODEL) par
lots de 8 offres, avec un schéma de sortie en énumérations ; tout est revalidé. Plafond : CLASSIFY_MAX_PER_RUN
(300) par daily, le reste en tâche classify_jobs ; quota Gemini atteint : arrêt propre, reprise 30 min plus tard.
  python -m app.jobs classify            classe ce qui est en attente
  python -m app.jobs classify --all      reclasse tout le stock (premier déploiement, changement de la liste)

Agrégats : vue matérialisée mv_market_jobs (une ligne par offre classée), REFRESH CONCURRENTLY à la fin de
daily ; indicateurs calculés depuis ces faits (market.py), sans LLM à l'affichage. Seuils (market.py) :
catégorie cachée sous 5 offres, « peu de données » sous 20 ; comptage par entreprise (défaut) ou par offre.
Free : compétences et postes en clair sur 90 jours (top 5), le reste flouté (PLAN_LIMITS market_*).

------------------------------------------------------------------
Candidatures préparées (page Candidature, Pro)
------------------------------------------------------------------
Mire PRÉPARE la candidature ; il n'envoie RIEN. L'utilisateur ouvre la page de candidature de l'entreprise (bouton
« Ouvrir la page de candidature »), colle chaque réponse (bouton Copier), joint les PDF et clique lui-même sur
« Envoyer ». Ni extension de navigateur, ni automatisation, ni connexion à un compte de l'utilisateur sur un ATS.

Parcours : « Préparer ma candidature » (Offres, fiche d'une offre, colonne « À préparer » des Candidatures)
-> tâche fetch_apply_form (sauf formulaire en cache de moins de 7 jours) -> tâche prepare_application -> PDF
-> page /candidature?job=<id> (le front interroge l'API toutes les 2 s, 2 min au plus).
  1. Formulaire (apply_forms.py), uniquement des GET publics, même client que ats.py :
     - Greenhouse : API du job board, /v1/boards/{board}/jobs/{id}?questions=true ;
     - Ashby : le formulaire n'est ni dans posting-api ni dans le HTML de la page (application React). Mire fait la
       requête GraphQL de LECTURE « ApiJobPosting » de la page (jobs.ashbyhq.com/api/non-user-graphql), en GET ;
     - Lever : HTML de /apply ; Teamtailor : HTML de /applications/new ;
     - Welcome to the Jungle, YC (workatastartup.com) : candidature depuis un compte sur la plateforme -> formulaire
       standard, lien vers l'offre ;
     - repli : Gemini lit le HTML nettoyé et tronqué (sortie structurée), sinon formulaire standard (« Formulaire non
       lu : questions types »).
  2. Préparation (application_prep.py) : réponses types et profil d'abord ; UN appel Gemini pour le CV adapté, la
     lettre (seulement si le formulaire en demande une, ou sur demande) et les questions. Offre et pages = données non
     fiables, tronquées ; ni le nom, ni l'email, ni le téléphone ne sont envoyés à Gemini.
     Contrôle anti-invention du CV (check_cv) : employeur, école, intitulé, dates, compétence hors profil, puce sans
     source, chiffre absent des sources, compétence manquante citée -> retiré avec un avertissement visible.
     Champs sensibles : diversité et consentement jamais remplis ; salaire, autorisation de travail, disponibilité
     seulement depuis les réponses types (Mon profil > Réponses types pour les candidatures) ; listes à choix limitées
     à leurs options ; sinon « À toi ». Sans assez de puces dans les expériences, le CV n'est pas généré.
  3. « Marquer prête à envoyer » : refusé tant qu'un champ obligatoire est vide ou « À toi » ; « J'ai envoyé ma
     candidature » : statut Postulé, date de candidature, instantané (réponses, CV, lettre) consultable dans Mes process.
Quotas (PLAN_LIMITS) : prepare_application_per_day 0 en Free (fenêtre Pro), 5 en Pro ; regenerate_field_per_day
0 / 30. Jour civil, heure de Paris ; une préparation échouée (Gemini indisponible) n'est pas comptée.
Coût : tokens enregistrés sur le brouillon (application_drafts.tokens_in / tokens_out) et dans application_generations.
PDF : WeasyPrint (groupe uv « worker ») et Pango ne sont que dans l'image worker. En local (docker compose, sans ce
groupe), les PDF sont indisponibles : la page le signale et propose de copier le texte.
