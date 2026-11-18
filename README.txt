Mire (ex-maquette Framer "Incredible Info", ex-"job radar")

------------------------------------------------------------------
Architecture
------------------------------------------------------------------
  frontend/   HTML/CSS/JS sans build (modules ES), reprend le design de la maquette Framer
              - assets/app.css      styles + couleurs des statuts (variables --st-*)
              - assets/js/app.js    code partagé : appels API, barre latérale, pastilles, logos, formulaires
              - assets/js/<page>.js une page = un script
  backend/    FastAPI (projet uv) : connexion LinkedIn OpenID Connect, session, API JSON /api/...
              - app/models.py       tables (SQLAlchemy)
              - migrations/         migrations Alembic (appliquées automatiquement au démarrage)
              - app/seed.py         données de démonstration
  db          PostgreSQL 17 (volume Docker postgres-data)

Tables :
  users                 un compte par identifiant LinkedIn (créé à la première connexion)
  companies             entreprises (site web, levée, logo mis en cache)
  jobs                  postes, rattachés à une entreprise
  applications          process de candidature : un utilisateur x un poste, avec statut
  application_events    historique des changements de statut

Statuts : nouveau, à postuler, postulé, relance, entretien, offre, refusé, abandonné.
Un poste sans process est « nouveau » pour l'utilisateur. Couleurs : variables --st-* dans frontend/assets/app.css.

Logos : récupérés par le backend depuis "URL du logo" si renseignée, sinon depuis le favicon du site web
(services Google puis DuckDuckGo : seul le domaine de l'entreprise leur est envoyé), puis mis en cache en base.
Import manuel possible depuis la fiche entreprise. Sans logo, des initiales colorées s'affichent.

------------------------------------------------------------------
Lancer en local avec Docker (WSL) + connexion LinkedIn
------------------------------------------------------------------
Config : copier .env.example en .env et renseigner LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET / SESSION_SECRET
         (app LinkedIn avec le produit "Sign In with LinkedIn using OpenID Connect",
          URL de redirection : http://localhost:8080/auth/callback).

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

------------------------------------------------------------------
Ancien export Framer
------------------------------------------------------------------
Les fichiers *.html et _fr/ à la racine sont l'ancien export Framer. Ils ne sont plus servis
(nginx sert uniquement frontend/) et peuvent être supprimés.
