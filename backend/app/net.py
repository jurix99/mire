import ssl

# Contexte TLS basé sur les certificats du système (le conteneur reçoit ceux de WSL,
# qui incluent la CA du proxy HTTPS de l'entreprise), au lieu du bundle certifi.
TLS_CONTEXT = ssl.create_default_context()
