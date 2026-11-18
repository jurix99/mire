Mire (ex-maquette Framer "Incredible Info", ex-"job radar")

------------------------------------------------------------------
Architecture
------------------------------------------------------------------
  frontend/   HTML/CSS/JS sans build (modules ES), reprend le design de la maquette Framer
              - assets/app.css      styles + couleurs des statuts (variables --st-*)
              - assets/js/app.js    code partagé : appels API, barre latérale, pastilles, logos, formulaires,
                                    chat flottant de l'assistant
              - assets/js/<page>.js une page = un script
  backend/    FastAPI (projet uv) : connexion LinkedIn OpenID Connect, session, API JSON /api/...
              - app/models.py       tables (SQLAlchemy)
              - app/services.py     fonctions métier partagées par l'API et les outils du chat
              - app/profile.py      profil : schémas (règles, barème…), fusion, résumé pour le chat et la notation
              - app/proposals.py    propositions de modification du profil, validées élément par élément
              - app/profile_api.py  /api/profile, imports (linkedin_export.py, pdf_import.py), analyse (analysis.py)
              - app/assessments.py  évaluation automatique des offres par utilisateur (+ tâche de fond)
              - app/feedback.py     retours J'aime / Pas intéressé ; rules_synthesis.py : synthèse des règles
              - app/llm.py          client Gemini (SDK google-genai)
              - app/chat.py         /api/chat ; app/chat_tools.py : outils de l'assistant
              - tests/              tests pytest (SQLite, Gemini simulé : aucun appel réseau)
              - scripts/eval_reference.py  évaluation de référence avec le vrai Gemini (voir plus bas)
  docs/       processus de recherche d'offres (processus-recherche.md) et exemple de profil complet
              - migrations/         migrations Alembic (appliquées automatiquement au démarrage)
              - app/seed.py         données de démonstration
  db          PostgreSQL 17 (volume Docker postgres-data)

Tables :
  users                 un compte par identifiant LinkedIn (créé à la première connexion)
  companies             entreprises (site web, levée, logo mis en cache)
  jobs                  postes, rattachés à une entreprise
  applications          process de candidature : un utilisateur x un poste, avec statut
  application_events    historique des changements de statut
  user_profiles         profil riche (1-1 avec users, créé à la volée) : identité pro, positionnement,
                        expériences, formation, compétences (points forts / à développer), préférences
                        (dont déplacements), règles « éviter / préférer » (origine, exemples, active),
                        entreprises exclues, offres de référence, barème, version (-> réévaluation)
  profile_imports       propositions en attente (import, analyse, synthèse des règles), validées élément
                        par élément (24 h)
  job_assessments       évaluation CALCULÉE d'une offre pour un utilisateur : note /10, détail par critère,
                        verdict (nouveau / écartée), raison, pourquoi, compétences ; note corrigée à la main
  job_feedback          retours J'aime / Pas intéressé (raisons, commentaire), pour la synthèse des règles
  chat_conversations    conversations avec l'assistant (la plus récente est active)
  chat_messages         messages (utilisateur / assistant) et outils appelés
  chat_actions          écritures proposées par l'assistant, exécutées seulement après confirmation :
                        sert aussi de journal (statut, résultat, date d'exécution)

Statuts posés par l'utilisateur : nouveau, à regarder, postulé, relance, entretien, offre, refusé,
pas intéressé, abandonné. Statut affiché : celui posé par l'utilisateur, sinon le verdict de l'évaluation
(« Nouveau » ou « Écartée (auto) »). Couleurs : variables --st-* dans frontend/assets/app.css.

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
           Sans clé : chat grisé, import PDF / texte, analyse et évaluation automatique désactivés ;
           le reste fonctionne.
         Après modification de .env : docker compose up -d backend   (un simple restart ne relit pas .env)

Lancer (dans WSL, depuis ce dossier) :
  docker compose up -d
  -> http://localhost:8080        site avec connexion LinkedIn
  -> http://localhost:8081        site sans connexion, avec un utilisateur "dev" (dev local uniquement)
  -> localhost:5432               PostgreSQL (base/utilisateur jobradar, mot de passe POSTGRES_PASSWORD, "jobradar" par défaut)

Au premier démarrage, la base est remplie avec les données de démo de la maquette (6 entreprises, 8 postes),
et chaque nouvel utilisateur reçoit 3 process de démo. Désactiver : SEED_DEMO: "false" dans docker-compose.yml.

Commandes utiles :
  docker compose logs -f backend                         logs de l'API
  docker compose exec db psql -U jobradar                console SQL
  docker compose down -v                                 tout arrêter ET effacer la base

Modifier le modèle (backend/app/models.py), puis générer et vérifier la migration :
  docker compose run --rm backend uv run alembic revision --autogenerate -m "description"
  docker compose restart backend                         (applique la migration)

Après modif de backend/pyproject.toml : docker compose run --rm --no-deps backend uv lock

Routes : /auth/login, /auth/callback, /auth/me, /auth/logout
         /api/me, /api/dashboard, /api/analytics, /api/statuses
         /api/companies[/{id}[/logo]], /api/jobs[/{id}[/status]], /api/applications[/{id}]
         /api/profile                                 GET, PATCH (champs envoyés remplacés)
         /api/profile/import/linkedin-export          POST, ZIP de l'export de données LinkedIn (20 Mo max)
         /api/profile/import/pdf                      POST, PDF du profil ou CV (10 Mo max, via Gemini)
         /api/profile/import/text                     POST {text}, texte collé (via Gemini)
         /api/profile/proposals/{id}                  GET ; DELETE l'abandonne
         /api/profile/proposals/{id}/apply            POST {accept: [ids d'éléments]} (absent : éléments cochés)
         /api/profile/analyze                         POST, déductions sur le profil -> proposition
         /api/profile/rules/synthesize                POST, règles déduites des retours -> proposition
         /api/jobs/{id}/feedback                      POST {sentiment: like|dislike, reasons, text}
         /api/feedback, /api/feedback/reasons         GET, retours récents (nombre de nouveaux), raisons
         /api/jobs/{id}/assessment/manual             PUT {score|null}, correction manuelle de la note
         /api/assessments/status, /api/assessments/run   GET état, POST évaluer tout de suite (10 offres)
         /api/chat/status, /api/chat/history          GET
         /api/chat                                    POST {message, mode: normal|onboarding}
         /api/chat/reset                              POST, nouvelle conversation
         /api/chat/actions/{id}/confirm | /cancel     POST, exécute ou abandonne une proposition

Tests (depuis backend/, sans Docker) : uv run pytest
  (sur le réseau de l'entreprise : UV_SYSTEM_CERTS=1 pour que uv accepte le proxy HTTPS)
Évaluation de référence avec le vrai Gemini (base SQLite temporaire, la base de l'app n'est pas touchée) :
  docker compose exec backend uv run python scripts/eval_reference.py
  -> rejoue les retours de docs/processus-recherche.md (§7), vérifie que les règles proposées couvrent les
     thèmes de docs/exemple-profil.txt et que Remanence est mieux notée qu'Ooak Data et Bluesand.

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
4. Retours : « J'aime » (offre de référence, direct) / « Pas intéressé » (statut + raisons). Après 3 nouveaux
   retours, « Mettre à jour mes règles » : règles proposées citant les offres d'origine, validées une par une.
   « Déjà passé en entretien » propose d'exclure l'entreprise.

------------------------------------------------------------------
Ancien export Framer
------------------------------------------------------------------
Les fichiers *.html et _fr/ à la racine sont l'ancien export Framer. Ils ne sont plus servis
(nginx sert uniquement frontend/) et peuvent être supprimés.
