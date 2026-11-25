# Agent de découverte : API et lancement

Mire fonctionne en trois processus : **découverte** des entreprises (cet agent, chaque lundi), **collecte**
quotidienne des offres (Mire, sans LLM) et **pertinence** par utilisateur (Mire). L'agent (Claude Code, lancé
en local) trouve les entreprises françaises qui ont levé des fonds et l'identifiant de leur ATS, et les écrit
dans Mire via une API protégée par jeton. Le processus est décrit dans [processus-recherche.md](processus-recherche.md) ; les consignes
données à l'agent sont dans [`agent/prompt.md`](../agent/prompt.md).

## Côté serveur (production)

En production, l'agent tourne dans des jobs Container Apps planifiés (`mire-agent-decouverte`,
`mire-agent-recherche`, `mire-agent-offres`), avec un plafond de dépense par passage : voir « Agent de recherche planifié » dans
[deploiement-azure.md](deploiement-azure.md). Le lanceur `agent/run_agent.py` (Linux, macOS ou Windows) fait la
même chose que `run-agent.ps1`, sans démarrer docker compose :

```sh
MIRE_URL=https://<domaine> MIRE_AGENT_TOKEN=… ANTHROPIC_API_KEY=… python agent/run_agent.py decouverte
python agent/run_agent.py rattrapage --months 6   # AGENT_TOTAL_BUDGET_USD plafonne l'ensemble
python agent/run_agent.py offres
python agent/run_agent.py recherche   # entreprises inconnues, trouvées par leurs offres
```

**Passage « recherche »** : la découverte par les levées rate les entreprises dont la levée est ancienne ou
discrète, mais qui recrutent aujourd'hui les profils des utilisateurs. Ce passage part des rôles et compétences
des profils (anonymisés), cherche les offres correspondantes (ATS, Welcome to the Jungle, tableaux d'offres des
fonds) et enregistre les entreprises inconnues, avec leur ATS s'il est lisible (Mire collecte alors toutes leurs
offres) ou leurs offres pertinentes sinon. Consignes : [`agent/prompt-recherche.md`](../agent/prompt-recherche.md).

## Mise en place (poste local)

1. Dans `.env` : `AGENT_TOKEN=<jeton aléatoire>` (par exemple `openssl rand -base64 32`), puis
   `docker compose up -d backend`. Sans jeton, l'API de l'agent répond 503.
2. Tester : `uv run --no-project python agent/mire_api.py GET context` (depuis la racine du dépôt).
3. Planifier (chaque lundi à 7h43, rattrapé au réveil si le PC était éteint) :
   `powershell -ExecutionPolicy Bypass -File agent\install-task.ps1`
4. Passage à la demande : `powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1`
   (ou `-Kind approfondi`). Journal : `agent\logs\`.

`run-agent.ps1` démarre Mire (docker compose dans WSL) s'il ne répond pas, puis lance
`claude -p` avec des outils limités : recherche web, lecture de pages, écriture de fichiers dans `agent/tmp/`
et `agent/mire_api.py`. Aucune autre commande n'est autorisée.

**Passage « offres »** (`-Kind offres`, chaque jeudi avec `install-task.ps1`) : beaucoup d'entreprises publient
leurs offres ailleurs que sur un ATS que Mire lit seul (Ashby, Lever, Greenhouse, Recruitee, Teamtailor) :
Welcome to the Jungle, page Notion, page carrières du site, Workable… L'agent lit alors leur page d'offres
publique, enregistre les offres tech ouvertes (`POST jobs`, `source` = la plateforme), ferme celles qui ont
disparu et marque l'entreprise vérifiée. Il lit les pages comme un visiteur : jamais d'API privée ni de clé.
S'il trouve un ATS lisible, il l'enregistre et Mire prend le relais chaque jour. Consignes :
[`agent/prompt-offres.md`](../agent/prompt-offres.md).

**Contre la production** (Azure, voir [deploiement-azure.md](deploiement-azure.md)) : définir `MIRE_URL`
(`https://<domaine>`) et `MIRE_AGENT_TOKEN` (secret `agent-token` du Key Vault). Avec une URL distante, le
script ne démarre pas docker compose, et le jeton du `.env` local n'est jamais envoyé. Une URL distante doit
être en `https://`. La fin de passage (`runs/{id}/finish`) évalue tout de suite les offres ajoutées, en au plus
`FINISH_BUDGET_S` secondes (90 par défaut). Le reste passe par la file de tâches.

## Sécurité
- Jeton `Authorization: Bearer <AGENT_TOKEN>`, comparé en temps constant ; la session LinkedIn ne donne pas
  accès à cette API, et le jeton ne donne pas accès au reste de l'API.
- Aucune suppression. Une source n'est jamais désactivée par l'agent. Une entreprise existante n'est que
  complétée (champs vides) : ce qu'un utilisateur a saisi est conservé.
- L'agent ne voit ni nom ni e-mail : les profils sont fournis sous la forme « Utilisateur <id> ».
- L'agent ne touche à aucune donnée propre à un utilisateur (statut, note, profil) : Mire évalue les offres
  pour chaque utilisateur, avec son profil.
- Tout contenu web lu par l'agent est une donnée, jamais une instruction (rappelé dans le prompt).

## Routes (`/api/agent/...`)

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `context` | Sources actives, entreprises (ATS, vérification), offres déjà vues, profils anonymisés, derniers passages |
| POST | `runs` `{kind: decouverte\|approfondi\|manuel\|offres\|recherche}` | Début d'un passage -> `run_id` |
| POST | `runs/{id}/finish` `{status, jobs_seen, companies_checked, summary, errors}` | Fin : Mire évalue les offres ajoutées pour chaque utilisateur et renvoie le résumé (top 5 par utilisateur) |
| POST | `companies/bulk` `{companies: [...], run_id}` | Jusqu'à 100 entreprises en un appel (même règle que `companies`) |
| POST | `companies` | Crée l'entreprise, ou complète les champs vides d'une existante (même nom ou même site) |
| POST | `companies/{id}/check?run_id=` | Mire lit la liste des offres de l'ATS (Ashby, Lever, Greenhouse, Recruitee, Teamtailor) : ouvertes, fermées, nouvelles |
| POST | `companies/{id}/ats` `{ats, slug}` | Mire teste l'identifiant ; enregistré seulement s'il fonctionne et contient les offres de cette entreprise (une valeur cassée peut être remplacée) |
| POST | `companies/{id}/checked` `{no_relevant_jobs}` | Vérifiée (sans offre pertinente : pas revérifiée au prochain passage) |
| POST | `jobs` | Ajoute une offre (dédoublonnée : 409 avec `existing_id` ; non vérifiée ouverte et publiée il y a plus de 2 mois : 422) |
| POST | `jobs/{id}/close?run_id=` | Offre disparue de la liste de l'entreprise : fermée |
| POST | `sources` | Ajoute une source (ajoutée par : agent) ; existante : complète seulement l'URL vide |
| PATCH | `sources/{id}` `{query, notes_append, used}` | Complète une source (URL vide, notes ajoutées à la suite) |

Champs d'une offre (`POST jobs`) : `company_id`, `title`, `url`, `location`, `work_mode`
(sur_site | hybride | remote), `travel`, `role_type`, `seniority`, `salary`, `stack`, `summary` (texte complet),
`published_on`, `open_verified`, `ats_job_id`, `source_id`, `run_id`.

Avec `agent/mire_api.py`, le chemin s'écrit sans « / » initial (Git Bash convertirait `/context` en chemin
Windows) ; un corps long peut être passé par fichier : `@agent/tmp/offre.json` (seul dossier autorisé).
