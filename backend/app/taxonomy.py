"""Liste fermée des catégories de Mire : familles de postes, compétences, secteurs. SEULE source de vérité.

Règle : peu de catégories, toujours les mêmes. On ne crée JAMAIS une catégorie à partir du texte d'une offre :
une valeur hors liste devient « autre » et est comptée (voir classify.py et /api/admin/taxonomy-health) pour
décider, à la main, d'ajouter une entrée ici.

Modifier la liste : éditer ce fichier, augmenter TAXONOMY_VERSION, déployer. La migration et la commande
`python -m app.jobs taxonomy` chargent la liste en base (sync) ; les offres classées avec une version antérieure
sont reclassées par le traitement quotidien (ou tout de suite : `python -m app.jobs classify --all`).

Synonymes : comparés au texte normalisé (minuscules, sans accents), en mots entiers. Une compétence de la
famille « langues » n'est comptée que si elle est EXIGÉE (synonymes = formulations d'exigence, jamais le simple
nom de la langue : une offre rédigée en anglais n'exige pas l'anglais).
"""

TAXONOMY_VERSION = 1

# --- Postes ---------------------------------------------------------------------------------------------

DOMAINS: dict[str, str] = {
    "tech": "Tech",
    "produit_design": "Produit et design",
    "gtm_ventes": "Ventes",
    "gtm_marketing": "Marketing",
    "operations": "Opérations",
    "generaliste": "Généraliste",
}
GTM_DOMAINS = ("gtm_ventes", "gtm_marketing")

# (id, libellé, domaine, mots-clés d'intitulé). L'ORDRE compte : la première famille dont un mot-clé apparaît
# dans l'intitulé l'emporte (« Data Product Manager » -> product_manager avant data).
ROLE_FAMILIES: list[tuple[str, str, str, list[str]]] = [
    ("founders_associate_chief_of_staff", "Founder's associate / Chief of staff", "generaliste",
     ["founder's associate", "founders associate", "founder associate", "chief of staff", "bras droit"]),
    ("product_manager", "Product manager", "produit_design",
     ["product manager", "product owner", "head of product", "product lead", "chef de produit", "cpo",
      "group product manager", "vp product"]),
    ("product_designer", "Product designer", "produit_design",
     ["product designer", "ux designer", "ui designer", "ux/ui", "ui/ux", "designer produit", "design lead",
      "head of design", "ux researcher", "user researcher"]),
    ("ml_ia", "ML / IA", "tech",
     ["machine learning", "ml engineer", "mlops", "ai engineer", "ingenieur ia", "ingenieur en intelligence artificielle",
      "research scientist", "research engineer", "applied scientist", "llm", "deep learning", "computer vision", "nlp",
      "ai researcher", "genai"]),
    ("data", "Data", "tech",
     ["data engineer", "data analyst", "analytics engineer", "data scientist", "business intelligence", "bi engineer",
      "bi analyst", "ingenieur data", "data architect", "head of data", "analyste de donnees"]),
    ("platform_devops_sre", "Platform / DevOps / SRE", "tech",
     ["devops", "sre", "site reliability", "platform engineer", "infrastructure engineer", "cloud engineer",
      "ingenieur infrastructure", "ingenieur systeme", "systems engineer", "security engineer", "devsecops",
      "ingenieur cloud", "administrateur systeme"]),
    ("front_mobile", "Front-end / mobile", "tech",
     ["frontend", "front-end", "front end", "ios", "android", "mobile engineer", "mobile developer", "react native",
      "flutter", "developpeur mobile", "developpeur front"]),
    ("fullstack", "Fullstack", "tech", ["fullstack", "full stack", "full-stack"]),
    ("backend", "Backend", "tech",
     ["backend", "back-end", "back end", "api engineer", "python developer", "developpeur python", "java developer",
      "golang developer", "developpeur backend", "software engineer", "software engineering", "ingenieur logiciel",
      "developpeur logiciel", "member of technical staff", "software developer", "software system engineer",
      "cto", "engineering manager", "tech lead", "head of engineering", "vp engineering"]),
    ("customer_success_account_management", "Customer success / account management", "gtm_ventes",
     ["customer success", "account manager", "csm", "key account", "customer support", "support engineer",
      "onboarding manager", "implementation manager", "customer experience", "charge de clientele",
      "relation client", "solutions engineer", "care expert", "experience client", "customer care"]),
    ("partenariats", "Partenariats", "gtm_ventes",
     ["partnership", "partnerships", "partner manager", "alliances", "channel manager", "partenariats"]),
    ("sales", "Sales (SDR / BDR / AE)", "gtm_ventes",
     ["sdr", "bdr", "account executive", "business developer", "business development", "sales", "commercial",
      "inside sales", "chargé d'affaires", "charge d'affaires", "ingenieur commercial", "head of sales",
      "presales", "pre-sales", "sales engineer", "solutions consultant"]),
    ("growth_acquisition", "Growth / acquisition", "gtm_marketing",
     ["growth", "acquisition", "performance marketing", "traffic manager", "seo", "sea", "paid media", "demand gen",
      "demand generation"]),
    ("marketing_produit_contenu", "Marketing produit / contenu", "gtm_marketing",
     ["product marketing", "pmm", "content", "copywriter", "marketing manager", "lifecycle", "crm marketing",
      "marketing", "redacteur"]),
    ("brand_communication", "Brand / communication", "gtm_marketing",
     ["brand", "communication", "relations presse", "public relations", "community manager", "social media",
      "evenementiel", "events manager"]),
    ("finance", "Finance", "operations",
     ["finance", "financial", "fp&a", "comptable", "comptabilite", "accountant", "accounting", "controller",
      "controleur de gestion", "cfo", "tresorerie", "treasury"]),
    ("rh_recrutement", "RH / recrutement", "operations",
     ["recruiter", "recruiting", "talent acquisition", "talent partner", "people", "ressources humaines", "hr",
      "rh", "charge de recrutement", "recruteur", "payroll"]),
    ("juridique", "Juridique", "operations", ["legal", "juriste", "counsel", "compliance", "dpo", "avocat", "juridique",
                                              "paralegal"]),
    ("ops_business_ops", "Ops / business ops", "operations",
     ["operations", "ops", "business operations", "revops", "revenue operations", "sales ops", "bizops",
      "office manager", "supply chain", "logistique", "operations manager", "strategy"]),
    ("founding_toutes_fonctions", "Founding (toutes fonctions)", "generaliste",
     ["founding", "membre fondateur", "first employee", "generalist"]),
]
OTHER_FAMILY = ("autre", "Autre", "generaliste")

SENIORITY_LEVELS: dict[str, str] = {
    "junior": "Junior", "confirme": "Confirmé", "senior": "Senior", "lead": "Lead / management", "inconnu": "Non précisé",
}
CONTRACT_TYPES: dict[str, str] = {
    "cdi": "CDI", "cdd": "CDD", "stage": "Stage", "alternance": "Alternance", "freelance": "Freelance", "inconnu": "Non précisé",
}
EXCLUDED_CONTRACTS = ("stage", "alternance")  # exclus des statistiques par défaut
SALARY_KINDS = {"fixe": "Fixe", "ote": "OTE (fixe + variable)", "inconnu": "Non précisé"}
MARKET_REGIONS: dict[str, str] = {
    "france": "France", "dach": "DACH", "uk": "Royaume-Uni", "benelux": "Benelux", "espagne_italie": "Espagne / Italie",
    "us": "États-Unis", "europe": "Europe", "monde": "Monde",
}

# --- Compétences -----------------------------------------------------------------------------------------

# (id, libellé, groupe)
SKILL_FAMILIES: list[tuple[str, str, str]] = [
    ("langages", "Langages", "tech"),
    ("cloud_infra", "Cloud et infra", "tech"),
    ("ia_llm", "IA et LLM", "tech"),
    ("data", "Data", "tech"),
    ("front_mobile", "Front et mobile", "tech"),
    ("securite", "Sécurité", "tech"),
    ("pratiques", "Pratiques d'ingénierie", "tech"),
    ("gtm_outils", "Outils GTM", "gtm"),
    ("gtm_savoir_faire", "Savoir-faire GTM", "gtm"),
    ("produit_design", "Produit et design", "produit"),
    ("operations", "Opérations", "operations"),
    ("langues", "Langues exigées", "commun"),
    ("type_client", "Type de clients", "commun"),
]

T, P, V, M, O, G = "tech", "produit_design", "gtm_ventes", "gtm_marketing", "operations", "generaliste"
ALL = (T, P, V, M, O, G)

# (id, libellé, famille, domaines où elle compte, synonymes)
SKILLS: list[tuple[str, str, str, tuple[str, ...], list[str]]] = [
    # langages (11)
    ("python", "Python", "langages", (T,), ["python", "django", "fastapi", "flask"]),
    ("typescript_javascript", "TypeScript / JavaScript", "langages", (T,),
     ["typescript", "javascript", "node.js", "nodejs", "node js", "ts/js", "es6"]),
    ("go", "Go", "langages", (T,), ["golang", "go developer", "go engineer"]),
    ("java_kotlin", "Java / Kotlin", "langages", (T,), ["java", "kotlin", "spring boot", "spring", "jvm"]),
    ("rust", "Rust", "langages", (T,), ["rust"]),
    ("c_cpp", "C / C++", "langages", (T,), ["c++", "cpp", "embedded c", "c/c++"]),
    ("csharp_dotnet", "C# / .NET", "langages", (T,), ["c#", ".net", "dotnet", "asp.net"]),
    ("php", "PHP", "langages", (T,), ["php", "symfony", "laravel"]),
    ("ruby", "Ruby", "langages", (T,), ["ruby", "rails", "ruby on rails"]),
    ("scala", "Scala", "langages", (T,), ["scala"]),
    ("elixir", "Elixir", "langages", (T,), ["elixir", "phoenix framework"]),
    # cloud_infra (14)
    ("aws", "AWS", "cloud_infra", (T,), ["aws", "amazon web services", "ec2", "s3", "lambda", "ecs", "eks"]),
    ("azure", "Azure", "cloud_infra", (T,), ["azure", "microsoft azure"]),
    ("gcp", "Google Cloud", "cloud_infra", (T,), ["gcp", "google cloud", "google cloud platform", "gke", "cloud run"]),
    ("kubernetes", "Kubernetes", "cloud_infra", (T,), ["kubernetes", "k8s", "helm", "openshift"]),
    ("terraform", "Terraform / IaC", "cloud_infra", (T,),
     ["terraform", "infrastructure as code", "iac", "pulumi", "cloudformation", "opentofu"]),
    ("docker", "Docker", "cloud_infra", (T,), ["docker", "containers", "conteneurs", "containerisation"]),
    ("ci_cd", "CI/CD", "cloud_infra", (T,),
     ["ci/cd", "ci cd", "continuous integration", "integration continue", "github actions", "gitlab ci", "jenkins",
      "argocd", "argo cd"]),
    ("observabilite", "Observabilité", "cloud_infra", (T,),
     ["observability", "observabilite", "monitoring", "prometheus", "grafana", "datadog", "opentelemetry", "sentry"]),
    ("linux", "Linux", "cloud_infra", (T,), ["linux", "unix", "bash", "shell scripting"]),
    ("postgresql", "PostgreSQL", "cloud_infra", (T,), ["postgresql", "postgres", "pgvector"]),
    ("bases_nosql", "Bases NoSQL", "cloud_infra", (T,), ["mongodb", "nosql", "dynamodb", "cassandra", "elasticsearch"]),
    ("redis", "Redis", "cloud_infra", (T,), ["redis"]),
    ("kafka", "Kafka / streaming", "cloud_infra", (T,), ["kafka", "event streaming", "pubsub", "pub/sub", "rabbitmq", "nats"]),
    ("networking", "Réseau", "cloud_infra", (T,), ["networking", "reseau", "tcp/ip", "vpn", "dns"]),
    # ia_llm (11)
    ("llm", "LLM", "ia_llm", (T, P), ["llm", "llms", "large language model", "gpt", "openai", "anthropic", "gemini",
                                         "mistral", "generative ai", "genai", "ia generative"]),
    ("rag", "RAG", "ia_llm", (T,), ["rag", "retrieval augmented generation", "retrieval-augmented", "vector database",
                                     "base vectorielle", "embeddings"]),
    ("agents_ia", "Agents IA", "ia_llm", (T, P), ["ai agents", "agents ia", "agentic", "langchain", "langgraph",
                                                    "llamaindex", "function calling", "tool use", "mcp"]),
    ("fine_tuning", "Fine-tuning", "ia_llm", (T,), ["fine-tuning", "fine tuning", "finetuning", "rlhf", "lora", "post-training"]),
    ("eval_modeles", "Évaluation de modèles", "ia_llm", (T,), ["model evaluation", "evals", "evaluation de modeles",
                                                                "llm evaluation", "benchmarking"]),
    ("mlops", "MLOps", "ia_llm", (T,), ["mlops", "llm ops", "llmops", "mlflow", "kubeflow", "model serving",
                                         "model deployment", "vllm"]),
    ("deep_learning", "Deep learning", "ia_llm", (T,), ["pytorch", "tensorflow", "deep learning", "jax", "keras",
                                                          "neural networks", "reseaux de neurones"]),
    ("vision", "Vision par ordinateur", "ia_llm", (T,), ["computer vision", "vision par ordinateur", "opencv",
                                                          "image processing", "traitement d'image"]),
    ("nlp", "NLP", "ia_llm", (T,), ["nlp", "natural language processing", "traitement du langage", "speech recognition"]),
    ("ml_classique", "Machine learning classique", "ia_llm", (T,), ["machine learning", "scikit-learn", "sklearn",
                                                                      "xgboost", "statistical modeling", "apprentissage automatique"]),
    ("robotique", "Robotique", "ia_llm", (T,), ["robotics", "robotique", "ros", "ros2", "motion planning"]),
    # data (10)
    ("sql", "SQL", "data", (T, P, M, O), ["sql"]),
    ("dbt", "dbt", "data", (T,), ["dbt"]),
    ("spark", "Spark", "data", (T,), ["spark", "pyspark", "databricks"]),
    ("airflow", "Airflow / orchestration", "data", (T,), ["airflow", "dagster", "prefect", "orchestration de donnees"]),
    ("entrepot_donnees", "BigQuery / Snowflake", "data", (T,), ["bigquery", "snowflake", "redshift", "data warehouse",
                                                                "entrepot de donnees", "clickhouse"]),
    ("pandas", "Pandas / Polars", "data", (T,), ["pandas", "polars", "numpy"]),
    ("bi_dataviz", "BI / dataviz", "data", (T, O, M), ["looker", "metabase", "tableau", "power bi", "powerbi",
                                                        "data visualization", "dataviz", "superset"]),
    ("etl", "ETL / pipelines", "data", (T,), ["etl", "elt", "data pipelines", "pipelines de donnees", "fivetran", "airbyte"]),
    ("modelisation_donnees", "Modélisation de données", "data", (T,), ["data modeling", "modelisation de donnees",
                                                                         "dimensional modeling"]),
    ("statistiques", "Statistiques", "data", (T, P, M), ["statistics", "statistiques", "a/b testing", "ab testing",
                                                          "experimentation", "econometrics"]),
    # front_mobile (7)
    ("react", "React", "front_mobile", (T,), ["react", "reactjs", "react.js", "redux"]),
    ("nextjs", "Next.js", "front_mobile", (T,), ["next.js", "nextjs"]),
    ("vue", "Vue", "front_mobile", (T,), ["vue.js", "vuejs", "vue 3", "nuxt"]),
    ("ios_swift", "iOS / Swift", "front_mobile", (T,), ["swift", "swiftui", "ios", "objective-c"]),
    ("android_kotlin", "Android", "front_mobile", (T,), ["android", "jetpack compose"]),
    ("react_native_flutter", "React Native / Flutter", "front_mobile", (T,), ["react native", "flutter", "dart", "expo"]),
    ("css_html", "HTML / CSS", "front_mobile", (T,), ["css", "html", "tailwind", "sass"]),
    # securite (3)
    ("securite_applicative", "Sécurité applicative", "securite", (T,), ["application security", "appsec", "owasp",
                                                                          "securite applicative", "pentest", "secure coding"]),
    ("securite_cloud", "Sécurité cloud et conformité", "securite", (T, O), ["soc 2", "soc2", "iso 27001", "hds",
                                                                              "cloud security", "devsecops", "secnumcloud"]),
    ("identite_acces", "Identité et accès", "securite", (T,), ["oauth", "openid connect", "iam", "sso", "saml", "zero trust"]),
    # pratiques (7)
    ("tests", "Tests automatisés", "pratiques", (T,), ["unit tests", "tests unitaires", "tdd", "test automation",
                                                        "pytest", "jest", "cypress", "playwright", "tests automatises"]),
    ("architecture", "Architecture logicielle", "pratiques", (T,), ["software architecture", "architecture logicielle",
                                                                      "system design", "domain-driven design", "ddd",
                                                                      "clean architecture", "hexagonal"]),
    ("systemes_distribues", "Systèmes distribués", "pratiques", (T,), ["distributed systems", "systemes distribues",
                                                                         "scalability", "high availability", "microservices"]),
    ("api", "Conception d'API", "pratiques", (T,), ["rest api", "api rest", "graphql", "grpc", "openapi", "api design"]),
    ("git", "Git", "pratiques", (T,), ["git", "github", "gitlab"]),
    ("performance", "Performance", "pratiques", (T,), ["performance optimization", "optimisation des performances",
                                                        "profiling", "low latency", "faible latence"]),
    ("agile", "Méthodes agiles", "pratiques", (T, P), ["agile", "scrum", "kanban", "methodes agiles"]),
    # gtm_outils (11)
    ("hubspot", "HubSpot", "gtm_outils", (V, M), ["hubspot"]),
    ("salesforce", "Salesforce", "gtm_outils", (V, M, O), ["salesforce", "sfdc"]),
    ("outils_outbound", "Outils d'outbound", "gtm_outils", (V, M), ["lemlist", "la growth machine", "outreach",
                                                                     "salesloft", "apollo", "waalaxy", "lusha", "kaspr"]),
    ("google_ads", "Google Ads", "gtm_outils", (M,), ["google ads", "adwords"]),
    ("meta_ads", "Meta Ads", "gtm_outils", (M,), ["meta ads", "facebook ads", "instagram ads"]),
    ("linkedin_ads", "LinkedIn Ads", "gtm_outils", (M, V), ["linkedin ads", "linkedin sales navigator", "sales navigator"]),
    ("webflow", "Webflow / CMS", "gtm_outils", (M,), ["webflow", "wordpress", "framer", "cms"]),
    ("google_analytics", "Google Analytics / GA4", "gtm_outils", (M, P), ["google analytics", "ga4", "google tag manager",
                                                                           "gtm tag"]),
    ("emailing", "Outils d'emailing", "gtm_outils", (M,), ["brevo", "sendinblue", "mailchimp", "customer.io", "klaviyo",
                                                           "braze", "emailing"]),
    ("crm", "CRM (général)", "gtm_outils", (V, M), ["crm", "pipedrive", "attio", "zoho"]),
    ("notion_airtable", "No-code (Notion, Airtable, Zapier)", "gtm_outils", (M, O, G), ["airtable", "zapier", "make.com",
                                                                                         "n8n", "no-code", "nocode"]),
    # gtm_savoir_faire (12)
    ("prospection_outbound", "Prospection outbound", "gtm_savoir_faire", (V,), ["outbound", "prospection", "cold calling",
                                                                                 "cold emailing", "prospecting",
                                                                                 "lead generation", "generation de leads"]),
    ("seo", "SEO", "gtm_savoir_faire", (M,), ["seo", "referencement naturel", "search engine optimization"]),
    ("sea", "SEA / acquisition payante", "gtm_savoir_faire", (M,), ["paid search", "search ads", "sem", "paid acquisition",
                                                                     "acquisition payante", "campagnes sea", "paid social"]),
    ("content", "Content marketing", "gtm_savoir_faire", (M,), ["content marketing", "marketing de contenu",
                                                                 "redaction web", "copywriting", "storytelling"]),
    ("plg", "Product-led growth", "gtm_savoir_faire", (M, P), ["product-led", "product led", "plg", "self-serve",
                                                                "freemium"]),
    ("abm", "ABM", "gtm_savoir_faire", (M, V), ["account based marketing", "account-based marketing", "abm"]),
    ("closing", "Closing / négociation", "gtm_savoir_faire", (V,), ["closing", "negotiation", "negociation",
                                                                     "full-cycle sales", "full cycle sales", "deal closing"]),
    ("onboarding_client", "Onboarding client", "gtm_savoir_faire", (V,), ["customer onboarding", "onboarding client",
                                                                           "implementation", "deploiement client"]),
    ("gestion_comptes", "Gestion de comptes / upsell", "gtm_savoir_faire", (V,), ["account management", "upsell",
                                                                                   "cross-sell", "renewals", "churn",
                                                                                   "retention", "expansion revenue"]),
    ("lifecycle_crm", "Lifecycle / CRM marketing", "gtm_savoir_faire", (M,), ["lifecycle marketing", "crm marketing",
                                                                               "marketing automation", "nurturing"]),
    ("evenementiel", "Événementiel", "gtm_savoir_faire", (M,), ["events", "evenementiel", "salons", "trade shows"]),
    ("relations_presse", "Relations presse", "gtm_savoir_faire", (M,), ["relations presse", "public relations",
                                                                         "media relations"]),
    # produit_design (8)
    ("figma", "Figma", "produit_design", (P,), ["figma", "sketch", "prototyping", "prototypage"]),
    ("discovery", "Discovery produit", "produit_design", (P,), ["product discovery", "discovery", "jobs to be done",
                                                               "jtbd", "problem framing"]),
    ("recherche_utilisateur", "Recherche utilisateur", "produit_design", (P,), ["user research", "recherche utilisateur",
                                                                                "user interviews", "entretiens utilisateurs",
                                                                                "usability testing", "tests utilisateurs"]),
    ("analytics_produit", "Analytics produit", "produit_design", (P, M), ["amplitude", "mixpanel", "posthog", "heap",
                                                                          "product analytics", "analytics produit"]),
    ("design_system", "Design system", "produit_design", (P, T), ["design system", "systeme de design", "storybook"]),
    ("roadmap", "Roadmap / priorisation", "produit_design", (P,), ["roadmap", "prioritization", "priorisation", "okr",
                                                                   "okrs"]),
    ("ux_ui", "UX / UI", "produit_design", (P,), ["ux", "ui design", "interaction design", "user experience",
                                                  "experience utilisateur"]),
    ("specs", "Spécifications", "produit_design", (P,), ["specifications", "user stories", "prd", "functional specs",
                                                         "specifications fonctionnelles"]),
    # operations (9)
    ("modelisation_financiere", "Modélisation financière", "operations", (O,), ["financial modeling",
                                                                                "modelisation financiere",
                                                                                "financial model", "business plan"]),
    ("controle_gestion", "Contrôle de gestion", "operations", (O,), ["controle de gestion", "fp&a", "budgeting",
                                                                     "forecasting", "reporting financier"]),
    ("comptabilite", "Comptabilité", "operations", (O,), ["comptabilite", "accounting", "ifrs", "french gaap",
                                                          "cloture", "closing comptable"]),
    ("paie", "Paie", "operations", (O,), ["paie", "payroll"]),
    ("droit_contrats", "Droit des contrats", "operations", (O,), ["droit des contrats", "contract law",
                                                                  "redaction de contrats", "contract drafting"]),
    ("rgpd", "RGPD / conformité", "operations", (O, T), ["rgpd", "gdpr", "data protection", "protection des donnees"]),
    ("recrutement", "Recrutement", "operations", (O,), ["sourcing", "recrutement", "recruiting", "talent acquisition",
                                                        "ats recrutement"]),
    ("excel", "Excel / Google Sheets", "operations", (O, V, M, G), ["excel", "google sheets", "spreadsheets", "vba"]),
    ("gestion_projet", "Gestion de projet", "operations", (O, P, G), ["project management", "gestion de projet",
                                                                      "pmo", "program management"]),
    # langues (6) : uniquement des formulations d'EXIGENCE
    ("anglais", "Anglais exigé", "langues", ALL, ["anglais courant", "anglais professionnel", "anglais bilingue",
                                                  "bilingue anglais", "fluent english", "fluent in english",
                                                  "english fluency", "full professional proficiency in english",
                                                  "maitrise de l'anglais", "anglais : courant"]),
    ("francais", "Français exigé", "langues", ALL, ["fluent french", "fluent in french", "french speaker",
                                                    "french fluency", "native french", "francais courant"]),
    ("allemand", "Allemand exigé", "langues", ALL, ["allemand courant", "fluent german", "fluent in german",
                                                    "german speaker", "germanophone", "native german"]),
    ("espagnol", "Espagnol exigé", "langues", ALL, ["espagnol courant", "fluent spanish", "fluent in spanish",
                                                    "spanish speaker", "hispanophone"]),
    ("italien", "Italien exigé", "langues", ALL, ["italien courant", "fluent italian", "fluent in italian",
                                                  "italian speaker"]),
    ("neerlandais", "Néerlandais exigé", "langues", ALL, ["neerlandais courant", "fluent dutch", "fluent in dutch",
                                                          "dutch speaker", "neerlandophone"]),
    # type_client (5)
    ("b2b", "B2B", "type_client", ALL, ["b2b"]),
    ("b2c", "B2C", "type_client", ALL, ["b2c"]),
    ("smb", "PME / SMB", "type_client", ALL, ["smb", "pme", "tpe/pme", "small businesses"]),
    ("mid_market", "Mid-market / ETI", "type_client", ALL, ["mid-market", "mid market", "eti"]),
    ("grands_comptes", "Grands comptes", "type_client", ALL, ["grands comptes", "key accounts", "enterprise accounts",
                                                              "enterprise clients", "large accounts", "cac 40",
                                                              "enterprise sales"]),
]

# --- Secteur du produit (de l'ENTREPRISE, pas une compétence du candidat) ----------------------------------

SECTORS: list[tuple[str, str, list[str]]] = [
    ("saas", "SaaS B2B", ["saas", "logiciel", "software"]),
    ("ia", "IA", ["ia", "ai", "intelligence artificielle", "llm", "machine learning"]),
    ("fintech", "Fintech", ["fintech", "paiement", "payment", "banque", "neobanque", "assurance", "insurtech", "credit"]),
    ("sante", "Santé", ["sante", "health", "healthtech", "medtech", "biotech", "medical", "pharma"]),
    ("deeptech", "Deeptech", ["deeptech", "quantique", "quantum", "semi-conducteur", "semiconductor", "photonique",
                              "materiaux"]),
    ("industrie", "Industrie", ["industrie", "industrial", "manufacturing", "usine", "robotique", "robotics"]),
    ("climat_energie", "Climat et énergie", ["climat", "climate", "energie", "energy", "greentech", "cleantech",
                                             "carbone", "decarbonation", "hydrogene", "solaire", "batterie"]),
    ("cybersecurite", "Cybersécurité", ["cybersecurite", "cybersecurity", "securite"]),
    ("rh_tech", "RH tech", ["rh", "hr tech", "recrutement", "paie", "people"]),
    ("commerce", "Commerce et marketplaces", ["e-commerce", "ecommerce", "marketplace", "retail", "commerce"]),
    ("mobilite_logistique", "Mobilité et logistique", ["mobilite", "mobility", "logistique", "logistics", "transport",
                                                       "supply chain"]),
    ("immobilier", "Immobilier et construction", ["immobilier", "proptech", "real estate", "construction", "btp"]),
    ("education", "Éducation", ["education", "edtech", "formation"]),
    ("legaltech", "Legaltech", ["legaltech", "juridique", "legal"]),
    ("defense_spatial", "Défense et spatial", ["defense", "spatial", "space", "aerospace", "aeronautique", "drone"]),
    ("agri_food", "Agri et food", ["agritech", "foodtech", "agriculture", "alimentation", "food"]),
]
OTHER = "autre"


def role_family_ids() -> list[str]:
    return [f[0] for f in ROLE_FAMILIES] + [OTHER]


def skill_ids() -> list[str]:
    return [s[0] for s in SKILLS]


def sector_ids() -> list[str]:
    return [s[0] for s in SECTORS] + [OTHER]


FAMILY_DOMAIN: dict[str, str] = {f[0]: f[2] for f in ROLE_FAMILIES} | {OTHER: OTHER_FAMILY[2]}
FAMILY_LABEL: dict[str, str] = {f[0]: f[1] for f in ROLE_FAMILIES} | {OTHER: OTHER_FAMILY[1]}
SKILL_LABEL: dict[str, str] = {s[0]: s[1] for s in SKILLS}
SKILL_FAMILY: dict[str, str] = {s[0]: s[2] for s in SKILLS}
SKILL_FAMILY_LABEL: dict[str, str] = {f[0]: f[1] for f in SKILL_FAMILIES}
SECTOR_LABEL: dict[str, str] = {s[0]: s[1] for s in SECTORS} | {OTHER: "Autre"}


# --- Chargement en base -----------------------------------------------------------------------------------


def rows() -> dict[str, list[dict]]:
    """Lignes des tables de référence (role_families, skill_families, skills), dans l'ordre de ce fichier."""
    families = [{"id": fid, "label": label, "domain": domain, "position": i, "title_keywords": keywords}
                for i, (fid, label, domain, keywords) in enumerate(ROLE_FAMILIES)]
    families.append({"id": OTHER, "label": OTHER_FAMILY[1], "domain": OTHER_FAMILY[2], "position": len(families),
                     "title_keywords": []})
    return {
        "role_families": families,
        "skill_families": [{"id": fid, "label": label, "group": group, "position": i}
                           for i, (fid, label, group) in enumerate(SKILL_FAMILIES)],
        "skills": [{"id": sid, "label": label, "family_id": fam, "domains": list(domains), "synonyms": synonyms,
                    "position": i} for i, (sid, label, fam, domains, synonyms) in enumerate(SKILLS)],
    }


def sync(connection) -> dict[str, int]:
    """Charge la liste fermée dans les tables de référence (insertion ou mise à jour, suppression des entrées
    retirées du fichier). `connection` : Session ou Connection SQLAlchemy (migration). Idempotent."""
    from sqlalchemy import delete, insert, select, update

    from .models import RoleFamily, Skill, SkillFamily

    counts = {}
    data = rows()
    for model, key in ((SkillFamily, "skill_families"), (RoleFamily, "role_families"), (Skill, "skills")):
        table = model.__table__
        wanted = {r["id"]: r for r in data[key]}
        existing = set(connection.execute(select(table.c.id)).scalars())
        for rid, row in wanted.items():
            if rid in existing:
                connection.execute(update(table).where(table.c.id == rid).values(**row))
            else:
                connection.execute(insert(table).values(**row))
        removed = existing - set(wanted)
        if removed and model is Skill:
            from .models import JobSkill

            connection.execute(delete(JobSkill.__table__).where(JobSkill.__table__.c.skill_id.in_(removed)))
        if removed and model is not RoleFamily:  # une famille de poste retirée : les offres seront reclassées
            connection.execute(delete(table).where(table.c.id.in_(removed)))
        counts[key] = len(wanted)
    return counts
