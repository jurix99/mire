# TODO

## Pages légales (frontend/mentions-legales.html, cgv.html, confidentialite.html)

Les emplacements sont marqués `[À compléter]` (surlignés en jaune sur les pages) :

- [ ] Identité de l'éditeur : nom ou raison sociale, forme juridique / statut, SIREN-SIRET (RCS ou RNE),
      adresse, numéro de TVA intracommunautaire le cas échéant.
- [ ] Directeur de la publication.
- [ ] Adresse e-mail de contact, dont celle des demandes relatives aux données personnelles (RGPD).
- [ ] Médiateur de la consommation (adhésion obligatoire pour vendre à des consommateurs) : nom, site, adresse.

## Conformité

- [ ] Gemini : vérifier que la clé d'API est sur un projet **facturé** (offre payante). Sur l'offre gratuite,
      Google peut réutiliser les données envoyées (CV, profils), ce qui contredit la politique de confidentialité.
- [ ] Confirmation de commande sur support durable rappelant la renonciation au droit de rétractation (CGV art. 6) :
      ajouter la mention aux reçus / factures Stripe (Dashboard > Paramètres > Reçus clients / Factures), ou envoyer
      un e-mail de confirmation depuis Mire.
- [ ] Webhook Stripe de production (`…/api/billing/webhook`) et secret `stripe-webhook-secret` correspondant dans
      Key Vault.
- [ ] URL de redirection LinkedIn de production (`…/auth/callback`).
- [ ] Remplacer le secret `ghcr-pat` par un PAT limité à `read:packages`.

## Bêta privée (10 à 20 profils variés, pas seulement platform en startup IA)

- [ ] Secrets GitHub `BETA_INVITE_CODE` (et/ou `BETA_EMAILS`), puis *Deploy* ; envoyer le lien
      `https://<domaine>/?invitation=<code>` aux testeurs.
- [ ] Demander à chaque testeur de remplir « Ma recherche » (Profil) et de juger ses offres
      (J'aime / Pas intéressé) ; suivre « Pertinence par profil » sur la page Sources (administrateurs).

## Classement (backend/app/ranking.py)

- [ ] Mesurer la pertinence du nouveau classement sur des jugements réels (précision@10) avant d'ajuster ses poids.
- [ ] Premier déploiement : les embeddings de toutes les offres sont recalculés par le job `mire-daily` (la
      migration les efface) ; vérifier qu'il tient dans son délai (1 h).
- [ ] Code serveur sans page depuis le retrait d'Analytics et du tableau de bord (`/api/analytics*`,
      `/api/dashboard`) : à supprimer ou à réutiliser.

## Récapitulatif par e-mail (backend/app/digest.py, désactivé tant que SMTP_HOST est vide)

- [x] Fournisseur : Cloudflare Email Service, domaine `notifications.kingjulian.fr` actif (SPF, DKIM, DMARC posés
      par Cloudflare ; DNS de kingjulian.fr chez Cloudflare depuis le 27/11/2026, enregistrements en « DNS only »).
- [ ] Offre Workers Free : envoi seulement aux destinataires vérifiés (Email Routing > Destination addresses, 200 au
      plus). Passer à Workers Paid (3 000 e-mails/mois inclus) avant d'ouvrir à des utilisateurs non vérifiés.
- [ ] Production : variables GitHub `MIRE_DIGEST_ENABLED=true`, `SMTP_HOST=smtp.mx.cloudflare.net`, `SMTP_PORT=465`,
      `SMTP_USER=api_token`, `SMTP_FROM=Mire <recapitulatif@notifications.kingjulian.fr>`, secret Key Vault
      `smtp-password` (jeton d'API « mire-smtp-recapitulatif »), puis *Deploy* (docs/deploiement-azure.md).
- [ ] Bêta : suivre dans les journaux de `mire-daily` (`step = digest`) les envois, et les retours venus des
      e-mails (clics J'aime / Pas intéressé) ; ajuster `MIN_SCORE` et la fréquence par défaut si besoin.
- [ ] En local avec nginx (port 8080), la redirection vers la connexion perd les paramètres d'un lien
      (`/postes?offre=…`) : seul le front servi par FastAPI (production) les garde.

## Essai Pro (TRIAL_DAYS, désactivé par défaut)

- [ ] Choisir la durée (7 ou 14 jours), l'ajouter aux CGV (essai gratuit, sans carte, retour automatique au Free),
      puis poser `TRIAL_DAYS` (variable Terraform / Container Apps de l'API et des jobs).

## Exploitation

- [ ] Lancer une première fois le workflow *Restore test* à la main et vérifier qu'il passe (restauration,
      vérification, suppression du serveur temporaire) : il n'a encore jamais tourné contre Azure.

## Agent de recherche côté serveur (docs/deploiement-azure.md, « Agent de recherche planifié »)

- [ ] Clé de l'API Anthropic dédiée, avec une limite de dépense mensuelle dans la console Anthropic.
- [ ] Secret `anthropic-api-key` dans Key Vault, puis variables GitHub `MIRE_AGENT_ENABLED=true` (et
      `MIRE_AGENT_MODEL` si besoin), puis *Deploy*.
- [ ] Premier passage à la main de chaque job (`az containerapp job start -n mire-agent-decouverte`, puis
      `mire-agent-recherche` et `mire-agent-offres`), lecture des journaux (coût, tours, entreprises et offres
      ajoutées), puis désactivation des tâches planifiées Windows.
