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
