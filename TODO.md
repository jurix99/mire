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
