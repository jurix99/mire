# Déploiement de Mire sur Azure (Container Apps)

Mire tourne en production sur Azure Container Apps (profil Consumption), avec une base PostgreSQL privée.
L'objectif est un coût proche de 0 €. Tout est décrit en Terraform (`infra/`). La CI GitHub Actions applique
l'infrastructure. Seul le bootstrap se lance en local, une fois (voir plus bas pourquoi).

## Vue d'ensemble

```
                     Internet (HTTPS)
                            │
              ┌─────────────▼──────────────┐  Environnement Container Apps (cae-mire-prod)
              │ mire-api   (0 à 2 répliques)│  profil Consumption, intégré à snet-aca
              │ API + front, met en file    │
              └─────────────┬──────────────┘
   cron 05:30 UTC           │              KEDA : tâches en file ?
 ┌──────────────┐           │           ┌──────────────┐
 │ mire-daily   │           │           │ mire-worker  │  python -m app.jobs worker
 │ collecte,    ├───────────┼───────────┤ (0 à 2 exéc.)│  vide la file puis s'arrête
 │ embeddings,  │           │           └──────────────┘
 │ évaluation   │   ┌───────▼────────┐   mire-migrate : alembic upgrade head (lancé par la CI)
 └──────────────┘   │ PostgreSQL 17  │   (snet-pg, zone DNS privée, pgvector)
                    │ B1ms, privé    │
                    └────────────────┘
 Secrets : Key Vault (références lues par l'identité id-mire-runtime) · Images : ghcr.io (PAT read:packages)
 Journaux : Log Analytics (plafonné) · Budget : 5 €/mois, alertes à 50 % et 100 %
```

| Élément | Rôle |
|---|---|
| `mire-api` | API FastAPI et front (servi par FastAPI, sans nginx). Aucun thread de fond (`RUN_BACKGROUND_THREADS=false`) : un traitement long devient une tâche dans la table `tasks`. |
| `mire-worker` | Job déclenché par KEDA (scaler `postgresql`) dès qu'une tâche est due. Il la traite avec `SELECT … FOR UPDATE SKIP LOCKED`. |
| `mire-daily` | Job planifié chaque jour : collecte sur les ATS, embeddings, présélection par similarité, évaluation Gemini (Pro d'abord), ping healthchecks.io. |
| `mire-migrate` | Job manuel : `alembic upgrade head`. La CI le lance avant chaque mise à jour de l'API. |

## Régions

- `rg-mire-prod` et toute l'application : **northeurope** (Irlande). En francecentral, la création du serveur
  PostgreSQL `B_Standard_B1ms` échoue pour cet abonnement (`SkuNotAvailable`, avec ou sans zone fixée), et
  westeurope est fermé aux nouveaux abonnements. Région réglée par `prod_location` dans `infra/bootstrap`.
- `rg-mire-tfstate` (state Terraform, identité de la CI) : francecentral.

Avant de changer de région, vérifier la disponibilité :
`az postgres flexible-server list-skus --location <région>` (champ `reason` vide, `Standard_B1ms` présent).
Pour changer : supprimer `rg-mire-prod` (`az group delete`), modifier `prod_location`, relancer le bootstrap,
reposer les secrets dans le nouveau coffre, puis relancer *Deploy*. Le suffixe des noms (coffre, serveur)
change avec la région, car un coffre supprimé reste réservé 7 jours.

## Prérequis

- Un abonnement Azure dont vous êtes propriétaire (`Owner`). Pour la gratuité de PostgreSQL 12 mois, il faut
  un compte gratuit Azure, ou un abonnement qui donne droit aux services gratuits 12 mois (voir « Coûts »).
- Azure CLI (`az`), Terraform ≥ 1.9 et Docker (pour la migration des données).
- Le dépôt `jurix99/mire` sur GitHub, avec les droits d'administration (variables, environnements).
- Une app LinkedIn (produit « Sign In with LinkedIn using OpenID Connect »), une clé Gemini et un compte
  healthchecks.io (facultatif).
- Un PAT GitHub « classic » avec le seul droit `read:packages`, pour que Container Apps tire les images privées
  de GHCR. Donnez-lui une date d'expiration longue et notez-la dans votre agenda.

## Premier déploiement

L'ordre compte : le coffre Key Vault est créé par Terraform, il doit exister avant qu'on y pose les secrets,
et les Container Apps ne démarrent pas sans leurs secrets. Le premier déploiement se fait donc en deux passes
de la CI, réglées par la variable GitHub `MIRE_APPS_ENABLED`.

### 1. Bootstrap (en local, une fois)

```sh
cd infra/bootstrap
az login
az account set --subscription <abonnement>
terraform init
terraform apply -var subscription_id=$(az account show --query id -o tsv)
terraform output
```

Gardez `infra/bootstrap/terraform.tfstate` en lieu sûr, hors du dépôt (il est dans `.gitignore`). Il ne
contient aucun secret.

**Pourquoi en local ?** Le bootstrap crée l'identité OIDC de la CI et lui donne ses droits. La CI ne peut pas
se créer elle-même. Pour qu'elle le puisse, il faudrait lui donner `Owner` sur l'abonnement, ce qui va contre le
principe du moindre privilège. Tout le reste (`infra/main`) est appliqué par GitHub Actions, y compris le tout
premier apply.

### 2. Variables GitHub

Dans *Settings → Environments*, créez l'environnement **`prod`**. Une règle de protection (relecture
obligatoire) est conseillée.

Dans *Settings → Secrets and variables → Actions → Variables* (onglet « Repository variables »), ajoutez :

| Variable | Valeur |
|---|---|
| `AZURE_CLIENT_ID` | sortie du bootstrap |
| `AZURE_TENANT_ID` | sortie du bootstrap |
| `AZURE_SUBSCRIPTION_ID` | sortie du bootstrap |
| `TFSTATE_STORAGE_ACCOUNT` | sortie du bootstrap |
| `MIRE_APPS_ENABLED` | `false` pour l'instant |
| `MIRE_CUSTOM_DOMAIN` | vide pour l'instant (étape 6) |
| `BILLING_ENABLED` | `true` si l'offre Pro / Stripe est active (sinon ne pas créer) |
| `STRIPE_PRICE_MONTHLY`, `STRIPE_PRICE_PASS_3M` | identifiants `price_…` (si facturation) |

Ce sont des identifiants, pas des secrets : l'authentification passe par OIDC, sans secret client.

Les adresses e-mail vont dans des **secrets** du dépôt (*Secrets* → *Repository secrets*), pas dans des
variables. Le dépôt est public, et GitHub affiche dans les journaux des Actions l'`env` de chaque étape :
seuls les secrets y sont masqués.

| Secret | Valeur |
|---|---|
| `BUDGET_ALERT_EMAIL` | adresse des alertes de budget |
| `PRO_USER_EMAILS`, `ADMIN_EMAILS` | adresses séparées par des virgules (facultatif) |

### 3. Premier apply : l'infrastructure, sans les applications

Poussez sur `main`, ou lancez *Actions → Deploy → Run workflow*. La CI construit les images, puis
`terraform apply` crée le VNet, PostgreSQL, Key Vault, Log Analytics, l'environnement Container Apps et le
budget. Avec `MIRE_APPS_ENABLED=false`, elle ne crée ni l'API ni les jobs. Le nom du coffre apparaît en sortie
de l'étape `apply` (`key_vault_name`). Vous pouvez aussi le lire avec :

```sh
az keyvault list -g rg-mire-prod --query "[].name" -o tsv
```

### 4. Secrets dans Key Vault (à la main)

Le bootstrap vous a donné le rôle `Key Vault Secrets Officer` sur `rg-mire-prod`. Comptez quelques minutes de
propagation après le bootstrap. Les valeurs ne passent jamais par Terraform, le state ni le dépôt.

```sh
KV=<key_vault_name>
az keyvault secret set --vault-name $KV -n session-secret          --value "$(openssl rand -base64 32)"
az keyvault secret set --vault-name $KV -n agent-token             --value "$(openssl rand -base64 32)"
az keyvault secret set --vault-name $KV -n linkedin-client-id      --value "<client id LinkedIn>"
az keyvault secret set --vault-name $KV -n linkedin-client-secret  --value "<client secret LinkedIn>"
az keyvault secret set --vault-name $KV -n gemini-api-key          --value "<clé Gemini>"
az keyvault secret set --vault-name $KV -n ghcr-pat                --value "<PAT read:packages>"
az keyvault secret set --vault-name $KV -n healthcheck-daily-url   --value "https://hc-ping.com/<uuid>"
# Seulement si BILLING_ENABLED=true :
az keyvault secret set --vault-name $KV -n stripe-secret-key       --value "sk_live_…"
az keyvault secret set --vault-name $KV -n stripe-webhook-secret   --value "whsec_…"
```

Ne réutilisez pas le `session-secret` ni l'`agent-token` du `.env` local. Pour éviter que la valeur reste dans
l'historique du shell, utilisez plutôt `--file <fichier>` et supprimez le fichier ensuite.

Sans healthchecks.io, mettez `--value off` : toute valeur qui n'est pas une URL désactive le ping. Le secret
doit quand même exister, car le job `mire-daily` y fait référence.

Terraform pose lui-même `database-url` et `keda-postgres-connection`. Ils sont dérivés du mot de passe
PostgreSQL qu'il a généré.

### 5. Deuxième passe : les applications

Passez la variable `MIRE_APPS_ENABLED` à `true`, puis relancez *Deploy* (*Run workflow*). La CI crée
`mire-migrate`, applique les migrations (base vide → schéma complet, extension `vector` comprise), crée
`mire-api`, `mire-daily` et `mire-worker`, puis teste `/healthz` et `/readyz`. L'URL de l'API apparaît en sortie
(`public_url`, `api_fqdn`).

### 6. Domaine personnalisé et certificat managé (gratuit)

1. Chez votre registraire DNS, créez :
   - `CNAME  <domaine>        → <api_fqdn>` (ex. `mire-api.<…>.northeurope.azurecontainerapps.io`) ;
   - `TXT    asuid.<domaine>  → <custom_domain_verification_id>` (sortie Terraform).

   Pour un domaine racine (apex), utilisez un enregistrement `A` vers l'IP statique de l'environnement :
   `az containerapp env show -n cae-mire-prod -g rg-mire-prod --query properties.staticIp`.
2. Une fois le DNS propagé, renseignez la variable GitHub `MIRE_CUSTOM_DOMAIN=<domaine>` et relancez *Deploy*.
   Terraform ajoute le nom d'hôte à `mire-api`, et `PUBLIC_URL` devient `https://<domaine>`.
3. Créez et liez le certificat managé, une seule fois. azurerm ne gère pas les certificats managés, d'où cette
   commande :
   ```sh
   az containerapp hostname bind -g rg-mire-prod -n mire-api --hostname <domaine> \
     --environment cae-mire-prod --validation-method CNAME
   ```
   Azure renouvelle ensuite le certificat tout seul. Terraform ignore la liaison (`ignore_changes`), donc il ne
   la défait pas.

### 7. LinkedIn

Dans l'app LinkedIn (onglet *Auth*), ajoutez l'URL de redirection **`https://<domaine>/auth/callback`**. Sans
domaine personnalisé, c'est `https://<api_fqdn>/auth/callback`. Gardez celle du local
(`http://localhost:8080/auth/callback`).

Pour Stripe : créez le webhook `https://<domaine>/api/billing/webhook` en mode live.

## Mises à jour

À chaque push sur `main`, *Deploy* fait dans l'ordre :

1. images `mire-api` et `mire-worker` taguées avec le SHA court du commit ;
2. `terraform apply -target=azurerm_container_app_job.migrate` (nouvelle image du job de migration) ;
3. `az containerapp job start -n mire-migrate`, et attente du succès (sinon le déploiement s'arrête) ;
4. `terraform apply` complet (API, jobs) ;
5. test de fumée.

Il n'y a jamais deux déploiements en même temps (`concurrency`). L'image est changée par Terraform, pas par
`az containerapp update` : le state reste la seule référence. Sur une pull request, la CI poste le
`terraform plan` en commentaire.

## Migrer les données locales vers Azure

La base Azure n'est pas joignable depuis Internet. La méthode la plus simple n'ajoute aucune ressource Azure :
on emballe le dump dans une image privée sur GHCR, et un job ponctuel, dans le VNet, le restaure avec la chaîne
de connexion déjà présente dans Key Vault. À faire juste après la deuxième passe (étape 5), avant d'utiliser la
prod, et en dehors de l'heure du job quotidien.

```sh
# 1. Dump de la base locale (format custom), depuis le dossier du dépôt
mkdir -p restore
docker compose exec -T db pg_dump -U jobradar -d jobradar -Fc --no-owner --no-acl > restore/mire.dump

# 2. Image temporaire (PRIVÉE : elle contient vos données)
cat > restore/Dockerfile <<'EOF'
FROM postgres:17
COPY mire.dump /mire.dump
# Schéma vidé puis restauré ; mire-migrate amène ensuite le schéma à la dernière version
CMD ["sh", "-c", "psql \"$DB\" -v ON_ERROR_STOP=1 -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;' && pg_restore --no-owner --no-acl -d \"$DB\" /mire.dump"]
EOF
echo <PAT write:packages> | docker login ghcr.io -u jurix99 --password-stdin
docker build --platform linux/amd64 -t ghcr.io/jurix99/mire-restore:once restore
docker push ghcr.io/jurix99/mire-restore:once

# 3. Job ponctuel dans l'environnement (secret lu dans Key Vault par l'identité runtime)
KV=<key_vault_name>
ID=$(az identity show -g rg-mire-prod -n id-mire-runtime --query id -o tsv)
az containerapp job create -g rg-mire-prod -n mire-restore --environment cae-mire-prod \
  --trigger-type Manual --replica-timeout 1800 --replica-retry-limit 0 --cpu 0.5 --memory 1Gi \
  --workload-profile-name Consumption \
  --image ghcr.io/jurix99/mire-restore:once \
  --registry-server ghcr.io --registry-username jurix99 --registry-password <PAT read:packages> \
  --mi-user-assigned "$ID" \
  --secrets "db=keyvaultref:https://$KV.vault.azure.net/secrets/keda-postgres-connection,identityref:$ID" \
  --env-vars "DB=secretref:db"
az containerapp job start -g rg-mire-prod -n mire-restore
az containerapp job execution list -g rg-mire-prod -n mire-restore -o table   # attendre « Succeeded »

# 4. Schéma à jour (la base restaurée est à la révision Alembic du local), puis ménage
az containerapp job start -g rg-mire-prod -n mire-migrate
az containerapp job delete -g rg-mire-prod -n mire-restore --yes
rm -rf restore
```

Supprimez ensuite le paquet `mire-restore` sur GitHub (*Packages → mire-restore → Package settings → Delete*).
Les embeddings sont recalculés au premier passage du worker (tâche `embed_jobs`) ou du job quotidien.

## Exploitation

### Lancer un traitement à la main

```sh
az containerapp job start -g rg-mire-prod -n mire-daily     # enchaînement quotidien complet
az containerapp job start -g rg-mire-prod -n mire-worker    # vider la file sans attendre KEDA
az containerapp job start -g rg-mire-prod -n mire-migrate   # migrations
```

Depuis l'interface, « Collecter maintenant », un profil modifié ou un J'aime mettent une tâche en file. KEDA
démarre `mire-worker` dans les 30 secondes.

### Lire les journaux

Les journaux sont en JSON, un objet par ligne, sans secret ni contenu de profil.

```sh
# Exécutions d'un job et leur statut
az containerapp job execution list -g rg-mire-prod -n mire-daily -o table
# Journaux d'une exécution (nom donné par la commande précédente)
az containerapp job logs show -g rg-mire-prod -n mire-daily --execution <execution> --container daily
# Journaux de l'API en direct
az containerapp logs show -g rg-mire-prod -n mire-api --follow
```

Dans Log Analytics (*log-mire-prod → Logs*) :

```kusto
ContainerAppConsoleLogs_CL
| where ContainerJobName_s == "mire-daily"      // ou ContainerAppName_s == "mire-api"
| extend j = parse_json(Log_s)
| project TimeGenerated, level = tostring(j.level), logger = tostring(j.logger), message = tostring(j.message)
| order by TimeGenerated desc
```

La file elle-même se consulte en SQL (table `tasks` : `status`, `attempts`, `error`, `run_after`). Une tâche en
échec est réessayée deux fois (après 1 min, puis 4 min), puis marquée `failed`.

### Agent de découverte local contre la prod

```powershell
$env:MIRE_URL = "https://<domaine>"
$env:MIRE_AGENT_TOKEN = (az keyvault secret show --vault-name <kv> -n agent-token --query value -o tsv)
powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1
```

Avec une URL distante, le script ne lance pas docker compose. Il attend une vingtaine de secondes si l'API
démarre à froid. Le jeton du `.env` local n'est jamais envoyé à un autre serveur que localhost, et une URL
distante doit être en `https://`. Pour la tâche planifiée Windows, définissez ces deux variables d'environnement
pour l'utilisateur.

## Coûts estimés (northeurope, hors TVA, ordres de grandeur)

| Poste | Estimation | Commentaire |
|---|---|---|
| Container Apps (API, jobs) | **0 €** | Quota gratuit mensuel par abonnement : 180 000 vCPU-s, 360 000 Gio-s, 2 M de requêtes. L'API passe à zéro. Le job quotidien (1 vCPU / 2 Gio, ~10 min) consomme environ 18 000 vCPU-s par mois. Une heure d'API par jour en consomme environ 54 000. |
| PostgreSQL B1ms + 32 Go | **0 € pendant 12 mois** | 750 h de B1ms, 32 Go de stockage et 32 Go de sauvegarde gratuits par mois, **seulement** avec un compte gratuit Azure (ou une offre éligible). Ensuite, environ 13 à 15 €/mois de calcul et 4 €/mois de stockage. |
| Zone DNS privée | ~0,50 €/mois | Prix fixe par zone ; les requêtes sont négligeables. |
| Key Vault | quelques centimes | 0,03 € pour 10 000 opérations ; les Container Apps relisent les secrets environ toutes les 30 min. |
| Log Analytics | **0 €** | Plafond de 0,1 Go/jour (≤ 3 Go/mois), sous les 5 Go gratuits mensuels. Au-delà du plafond, les journaux du jour sont perdus. |
| Stockage du state | quelques centimes | Quelques Ko en LRS, versions comprises. |
| VNet, IP de sortie | 0 € | Pas de NAT Gateway. Les 100 premiers Go sortants par mois sont gratuits. |
| GHCR (images) | 0 € à ce jour | GitHub ne facture pas encore le stockage ni le transfert du Container registry, avec un mois de préavis promis. À surveiller : chaque exécution d'un job tire l'image worker (~400 Mo compressés). |

Total : moins de 1 € par mois pendant la première année, puis 17 à 20 € par mois si l'on garde le B1MS. Le
budget de 5 € envoie une alerte à 50 % et à 100 %.

## Fin des 12 mois gratuits de PostgreSQL

**Option A : garder le B1MS payant** (environ 17 à 20 €/mois). Rien à changer dans l'infra. Il faut seulement
relever `budget_amount` (variable Terraform, 25 par exemple), sinon l'alerte de budget part chaque mois.

**Option B : passer sur Neon (offre gratuite)**. Neon prend en charge pgvector, et la base actuelle pèse
environ 12 Mo, sous la limite gratuite. Changements :

1. Créer le projet Neon et activer l'extension (`CREATE EXTENSION vector`, ou laisser la migration le faire).
2. Migrer les données : `pg_dump` via un job ponctuel (comme ci-dessus, sens inverse) vers Neon, qui est
   joignable depuis Internet.
3. Dans `infra/main` :
   - supprimer `postgres.tf`, le sous-réseau `snet-pg`, la zone DNS privée et son lien (`network.tf`). Retirer
     d'abord `prevent_destroy`, et ne détruire le serveur qu'une fois la migration vérifiée ;
   - remplacer les deux `azurerm_key_vault_secret` (`database-url`, `keda-postgres-connection`) par des secrets
     posés à la main, avec les URL Neon (`postgresql+psycopg://…?sslmode=require` et `postgresql://…?sslmode=require`).
4. **Attention à KEDA** : le scaler interroge la base toutes les 30 secondes, ce qui empêcherait le calcul Neon
   de se mettre en veille et dépasserait les heures gratuites. Remplacer la règle KEDA de `mire-worker` par un
   déclenchement cron (par exemple `*/15 * * * *`, `schedule_trigger_config`), ou relever fortement
   `polling_interval_in_seconds`.

Le VNet et l'environnement intégré peuvent rester en place : ils ne coûtent rien.

## Points d'attention

- **Décalage horaire du cron** : `mire-daily` tourne à `30 5 * * *` **UTC** (variable `daily_cron`), soit
  06:30 à Paris en hiver et 07:30 en été. Pour garder 06:30 toute l'année, passer à `30 4 * * *` fin mars et
  revenir fin octobre.
- **PAT GHCR** : s'il expire, les nouvelles révisions et les jobs ne démarrent plus (échec du tirage d'image).
  Mettez à jour le secret `ghcr-pat`. La nouvelle valeur est prise en compte en moins de 30 min, ou tout de
  suite au prochain déploiement.
- **Mot de passe PostgreSQL** : c'est le seul secret présent dans le state Terraform, qui est privé et chiffré.
  Pour le changer : `terraform apply -replace=random_password.pg_admin` (via la CI), puis relancer les révisions.
- **Plan de PR** : il utilise la même identité que le déploiement, qui a des droits d'écriture. Les PR venant
  d'un fork n'ont pas de jeton OIDC. Gardez les droits d'écriture du dépôt pour vous seul.
- **Logos** : ils sont stockés en base (`companies.logo_data`). En local : 195 logos, 364 Ko en tout (1,9 Ko en
  moyenne, 15 Ko au plus), et la base entière fait 12 Mo. Même avec 100 000 entreprises, on resterait vers
  1,5 Go, loin des 32 Go.
