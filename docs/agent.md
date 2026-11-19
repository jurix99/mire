# Agent de découverte : API et lancement

Mire fonctionne en trois processus : **découverte** des entreprises (cet agent, chaque lundi), **collecte**
quotidienne des offres (Mire, sans LLM) et **pertinence** par utilisateur (Mire). L'agent (Claude Code, lancé
en local) trouve les entreprises françaises qui ont levé des fonds et l'identifiant de leur ATS, et les écrit
dans Mire via une API protégée par jeton. Le processus est décrit dans [processus-recherche.md](processus-recherche.md) ; les consignes
données à l'agent sont dans [`agent/prompt.md`](../agent/prompt.md).

## Mise en place

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
| POST | `runs` `{kind: decouverte\|approfondi\|manuel}` | Début d'un passage -> `run_id` |
| POST | `runs/{id}/finish` `{status, jobs_seen, companies_checked, summary, errors}` | Fin : Mire évalue les offres ajoutées pour chaque utilisateur et renvoie le résumé (top 5 par utilisateur) |
| POST | `companies` | Crée l'entreprise, ou complète les champs vides d'une existante (même nom ou même site) |
| POST | `companies/{id}/check?run_id=` | Mire lit la liste des offres de l'ATS (Ashby, Lever, Greenhouse, Recruitee) : ouvertes, fermées, nouvelles |
| POST | `companies/{id}/ats` `{ats, slug}` | Mire teste l'identifiant ; enregistré seulement s'il fonctionne et contient les offres de cette entreprise (une valeur cassée peut être remplacée) |
| POST | `companies/{id}/checked` `{no_relevant_jobs}` | Vérifiée (sans offre pertinente : pas revérifiée au prochain passage) |
| POST | `jobs` | Ajoute une offre (dédoublonnée : 409 avec `existing_id` ; publiée il y a plus de 2 mois : 422) |
| POST | `jobs/{id}/close?run_id=` | Offre disparue de la liste de l'entreprise : fermée |
| POST | `sources` | Ajoute une source (ajoutée par : agent) ; existante : complète seulement l'URL vide |
| PATCH | `sources/{id}` `{query, notes_append, used}` | Complète une source (URL vide, notes ajoutées à la suite) |

Champs d'une offre (`POST jobs`) : `company_id`, `title`, `url`, `location`, `work_mode`
(sur_site | hybride | remote), `travel`, `role_type`, `seniority`, `salary`, `stack`, `summary` (texte complet),
`published_on`, `open_verified`, `ats_job_id`, `source_id`, `run_id`.

Avec `agent/mire_api.py`, le chemin s'écrit sans « / » initial (Git Bash convertirait `/context` en chemin
Windows) ; un corps long peut être passé par fichier : `@agent/tmp/offre.json` (seul dossier autorisé).
