"""Analyses du marché (onglets « Marché » et « Toi face au marché » de la page Analytics, baromètre public).

Tout part de faits PRÉCALCULÉS : mv_market_jobs (une ligne par offre classée, vue matérialisée rafraîchie à la fin
du traitement quotidien) et job_skills (compétences des offres, classify.py). Aucun appel au LLM ici.

Périmètre par défaut : offres hors stage et alternance, publiées (publication, sinon ajout) dans la période,
ouvertes ou fermées (les fermées comptent pour les tendances). Ce sont des startups financées vues par Mire,
pas tout le marché : le périmètre réel est toujours renvoyé (« 842 offres de 213 startups… »).

Comptage « par entreprise » (défaut) : une entreprise compte une fois pour une catégorie, quel que soit son nombre
d'offres ; « par offre » : chaque offre compte. Chaque chiffre est renvoyé avec son effectif.
"""

import logging
import re
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from itertools import combinations

from sqlalchemy import delete, insert, select, text
from sqlalchemy.orm import Session

from . import taxonomy as tx
from .classify import SKILL_RULES, norm
from .models import Company, Job, JobAssessment, JobSkill, MarketJob, RoleFamily

log = logging.getLogger("mire.market")

# --- Seuils de lisibilité (un seul endroit) --------------------------------------------------------------

MIN_JOBS = 5  # une catégorie avec moins de 5 offres n'est pas affichée
LOW_DATA = 20  # un pourcentage calculé sur moins de 20 offres (ou entreprises) est marqué « peu de données »
TOP = 10  # éléments par liste à l'écran (« voir tout » au-delà)
MAX_TRENDS = 5  # compétences en hausse, et autant en baisse
MAX_STACKS = 5
STACK_MIN_SUPPORT = 5  # une paire / un trio doit apparaître dans au moins 5 offres
TREND_RECENT_DAYS = 30
TREND_BEFORE_DAYS = 90
POST_FUNDING_WINDOW_DAYS = 365
ME_MIN_JOBS = 3  # « Toi face au marché » : sur tes offres, effectifs plus petits
COVERAGE_THRESHOLD = 0.7
STRENGTH_MIN_SHARE = 0.15
GOOD_SCORE = 7

PERIODS = {"30j": 30, "90j": 90, "6m": 182}
PERIOD_LABELS = {"30j": "30 derniers jours", "90j": "90 derniers jours", "6m": "6 derniers mois"}
COUNT_BY = ("entreprise", "offre")
WORK_MODES = {"remote": "Remote", "hybride": "Hybride", "sur_site": "Sur site"}

# Fonds : noms libres découpés et normalisés avec une petite table de synonymes (pas de nouvelle catégorie à la volée :
# un nom inconnu est gardé tel quel, nettoyé)
INVESTOR_ALIASES = {
    "bpifrance": ["bpi", "bpi france", "bpifrance", "bpifrance large venture", "french tech seed"],
    "Partech": ["partech", "partech partners"],
    "Kima Ventures": ["kima", "kima ventures"],
    "Y Combinator": ["y combinator", "ycombinator", "yc"],
    "Elaia": ["elaia", "elaia partners"],
    "Eurazeo": ["eurazeo", "idinvest", "idinvest partners"],
    "Index Ventures": ["index", "index ventures"],
    "Point Nine": ["point nine", "point nine capital"],
    "Frst": ["frst", "frst capital"],
    "Isai": ["isai", "isai gestion"],
    "Alven": ["alven", "alven capital"],
    "Serena": ["serena", "serena capital"],
    "Daphni": ["daphni"],
    "Breega": ["breega", "breega capital"],
    "Newfund": ["newfund", "newfund capital"],
    "Seedcamp": ["seedcamp"],
    "Accel": ["accel", "accel partners"],
    "Balderton": ["balderton", "balderton capital"],
    "Sequoia": ["sequoia", "sequoia capital"],
    "a16z": ["a16z", "andreessen horowitz"],
    "Lightspeed": ["lightspeed", "lightspeed venture partners"],
    "Headline": ["headline", "e.ventures"],
    "Otium": ["otium", "otium capital"],
    "Side Capital": ["side capital"],
    "XAnge": ["xange"],
    "Cathay Innovation": ["cathay innovation", "cathay"],
    "Hi Inov": ["hi inov"],
}
_ALIAS = {norm(a): canon for canon, aliases in INVESTOR_ALIASES.items() for a in aliases}
_NOT_INVESTOR = {"business angels", "angels", "angel investors", "business angel", "and others", "et autres", "others"}


def investor_names(value: str | None) -> list[str]:
    out = []
    for part in re.split(r"[,;/&+\n]|\bet\b|\band\b|\(|\)", value or ""):
        key = norm(part).strip(" .-")
        if len(key) < 2 or key in _NOT_INVESTOR or key.startswith(("avec ", "with ", "aupres ")):
            continue
        out.append(_ALIAS.get(key, part.strip(" .-")))
    return list(dict.fromkeys(out))


def funding_round(stage: str | None) -> str | None:
    t = norm(stage)
    if not t:
        return None
    if re.search(r"\b(pre-?seed|seed|amorcage)\b", t):
        return "seed"
    if re.search(r"\b(serie|series)\s*a\b", t):
        return "serie_a"
    if re.search(r"\b(serie|series)\s*[b-h]\b|growth|late stage", t):
        return "serie_b_plus"
    return None


ROUND_LABELS = {"seed": "Seed", "serie_a": "Série A", "serie_b_plus": "Série B et plus"}


# --- Faits précalculés ----------------------------------------------------------------------------------------

_facts_version = 0


def refresh_facts(db: Session) -> int:
    """PostgreSQL : REFRESH MATERIALIZED VIEW CONCURRENTLY. SQLite (tests) : même contenu, calculé ici."""
    global _facts_version
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_market_jobs"))
        db.commit()
    else:
        domains = dict(db.execute(select(RoleFamily.id, RoleFamily.domain)).all())
        db.execute(delete(MarketJob))
        rows = []
        for job, company in db.execute(select(Job, Company).join(Company, Company.id == Job.company_id)
                                       .where(Job.classified_at.is_not(None))).all():
            created = job.created_at.date() if job.created_at else date.today()
            rows.append({
                "job_id": job.id, "company_id": job.company_id, "ref_date": job.published_on or created,
                "closed_on": job.closed_at.date() if job.closed_at else None, "role_family": job.role_family,
                "domain": domains.get(job.role_family), "seniority_level": job.seniority_level,
                "contract_type": job.contract_type, "salary_min_keur": job.salary_min_keur,
                "salary_max_keur": job.salary_max_keur, "salary_kind": job.salary_kind, "work_mode": job.work_mode,
                "market_regions": job.market_regions, "product_sector": job.product_sector,
                "funding_stage": company.funding_stage, "funding_announced_on": company.funding_announced_on,
                "investors": company.investors,
            })
        if rows:
            db.execute(insert(MarketJob), rows)
        db.commit()
    _facts_version += 1
    n = db.scalar(select(MarketJob.job_id).order_by(MarketJob.job_id.desc()).limit(1))
    log.info("Faits du marché rafraîchis", extra={"step": "market_refresh"})
    return n or 0


@dataclass
class Facts:
    rows: list[MarketJob]
    skills: dict[int, dict[str, bool]] = field(default_factory=dict)  # job_id -> {skill: exigée}


def load_facts(db: Session, include_excluded: bool = False) -> Facts:
    """Faits en lignes simples (attributs = colonnes de mv_market_jobs), sans objet ORM attaché à la session."""
    rows = list(db.execute(select(MarketJob.__table__)).all())
    if not include_excluded:
        rows = [r for r in rows if r.contract_type not in tx.EXCLUDED_CONTRACTS]
    skills: dict[int, dict[str, bool]] = defaultdict(dict)
    for job_id, skill_id, required in db.execute(select(JobSkill.job_id, JobSkill.skill_id, JobSkill.required)):
        skills[job_id][skill_id] = required
    return Facts(rows, dict(skills))


# --- Briques de calcul ------------------------------------------------------------------------------------------


def unit(r: MarketJob, count_by: str) -> int:
    return r.company_id if count_by == "entreprise" else r.job_id


def share_item(key: str, label: str, rows: list[MarketJob], base_units: int, count_by: str) -> dict:
    units = len({unit(r, count_by) for r in rows})
    return {"key": key, "label": label, "units": units, "jobs": len(rows),
            "share": round(units / base_units, 3) if base_units else 0.0, "base": base_units,
            "low_data": base_units < LOW_DATA}


def ranked(groups: dict[str, list[MarketJob]], labels: dict[str, str], base_units: int, count_by: str,
           limit: int | None = TOP) -> tuple[list[dict], int]:
    """Catégories au-dessus du seuil, triées ; renvoie aussi le nombre total d'éléments affichables."""
    items = [share_item(k, labels.get(k, k), rows, base_units, count_by)
             for k, rows in groups.items() if len(rows) >= MIN_JOBS]
    items.sort(key=lambda i: (-i["units"], -i["jobs"], i["label"]))
    return (items[:limit] if limit else items), len(items)


def quartiles(values: list[float]) -> tuple[float, float, float]:
    values = sorted(values)
    if len(values) == 1:
        return values[0], values[0], values[0]
    q = statistics.quantiles(values, n=4, method="inclusive")
    return q[0], statistics.median(values), q[2]


def scope(facts: Facts, *, period: str | None, domain: str | None, family: str | None,
          today: date | None = None) -> list[MarketJob]:
    today = today or date.today()
    start = today - timedelta(days=PERIODS[period]) if period else None
    return [r for r in facts.rows
            if (start is None or r.ref_date >= start) and r.ref_date <= today
            and (not domain or r.domain == domain) and (not family or r.role_family == family)]


def base_units(rows: list[MarketJob], count_by: str) -> int:
    return len({unit(r, count_by) for r in rows})


def _pct(x: float) -> str:
    return f"{round(x * 100)} %"


def _where(domain: str | None, family: str | None) -> str:
    if family:
        return f" en {tx.FAMILY_LABEL.get(family, family).lower()}"
    if domain:
        return f" ({tx.DOMAINS.get(domain, domain).lower()})"
    return ""


def _careful(item: dict | None, sentence: str) -> str:
    """Formulation prudente quand l'effectif est faible."""
    return f"Sur peu de données : {sentence[0].lower()}{sentence[1:]}" if item and item.get("low_data") else sentence


# --- Indicateurs de l'onglet Marché ------------------------------------------------------------------------------


def top_skills(facts: Facts, rows, count_by, limit=TOP, domain=None, family=None) -> dict:
    base = base_units(rows, count_by)
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        for sid, required in facts.skills.get(r.job_id, {}).items():
            if required:
                groups[sid].append(r)
    top, total = ranked(groups, tx.SKILL_LABEL, base, count_by, limit)
    by_family: dict[str, list] = {}
    for fam in tx.SKILL_FAMILY_LABEL:
        fam_groups = {sid: g for sid, g in groups.items() if tx.SKILL_FAMILY.get(sid) == fam}
        items, _ = ranked(fam_groups, tx.SKILL_LABEL, base, count_by, limit)
        if items:
            by_family[fam] = items
    first = top[0] if top else None
    headline = (_careful(first, f"{first['label']} est la compétence la plus demandée{_where(domain, family)} "
                                f"({_pct(first['share'])} des {'entreprises' if count_by == 'entreprise' else 'offres'})")
                if first else "Pas encore assez d'offres pour dégager des compétences")
    return {"headline": headline, "top": top, "total": total, "base": base,
            "by_family": [{"family": f, "label": tx.SKILL_FAMILY_LABEL[f], "items": items} for f, items in by_family.items()]}


def skill_trends(facts: Facts, count_by, domain=None, family=None, today=None) -> dict:
    today = today or date.today()
    recent_start = today - timedelta(days=TREND_RECENT_DAYS)
    before_start = recent_start - timedelta(days=TREND_BEFORE_DAYS)
    rows = scope(facts, period=None, domain=domain, family=family, today=today)
    recent = [r for r in rows if r.ref_date > recent_start]
    before = [r for r in rows if before_start < r.ref_date <= recent_start]
    b_recent, b_before = base_units(recent, count_by), base_units(before, count_by)
    items = []
    if b_recent >= LOW_DATA and b_before >= LOW_DATA:
        def per_skill(rs):
            g = defaultdict(set)
            jobs = Counter()
            for r in rs:
                for sid, required in facts.skills.get(r.job_id, {}).items():
                    if required:
                        g[sid].add(unit(r, count_by))
                        jobs[sid] += 1
            return g, jobs
        g_recent, j_recent = per_skill(recent)
        g_before, j_before = per_skill(before)
        for sid in set(g_recent) | set(g_before):
            if j_recent[sid] + j_before[sid] < MIN_JOBS:
                continue
            s_recent, s_before = len(g_recent[sid]) / b_recent, len(g_before[sid]) / b_before
            items.append({"key": sid, "label": tx.SKILL_LABEL.get(sid, sid), "pp": round((s_recent - s_before) * 100, 1),
                          "share_recent": round(s_recent, 3), "share_before": round(s_before, 3),
                          "units_recent": len(g_recent[sid]), "units_before": len(g_before[sid])})
    up = sorted([i for i in items if i["pp"] > 0], key=lambda i: -i["pp"])[:MAX_TRENDS]
    down = sorted([i for i in items if i["pp"] < 0], key=lambda i: i["pp"])[:MAX_TRENDS]
    headline = (f"{up[0]['label']} gagne {up[0]['pp']:g} points sur les 30 derniers jours" if up
                else "Pas de tendance nette sur les 30 derniers jours" if items
                else "Pas encore assez d'offres pour mesurer des tendances")
    return {"headline": headline, "up": up, "down": down, "base_recent": b_recent, "base_before": b_before,
            "total": len(up) + len(down)}


def roles(rows, count_by, limit=TOP, domain=None, family=None) -> dict:
    base = base_units(rows, count_by)
    by_domain, _ = ranked(_group(rows, lambda r: r.domain), tx.DOMAINS | {tx.OTHER: "Autre"}, base, count_by, None)
    by_family, total = ranked(_group(rows, lambda r: r.role_family), tx.FAMILY_LABEL, base, count_by, limit)
    by_seniority, _ = ranked(_group(rows, lambda r: r.seniority_level or "inconnu"), tx.SENIORITY_LEVELS, base,
                             count_by, None)
    first = by_family[0] if by_family else None
    headline = (_careful(first, f"{first['label']} est la famille de postes la plus recherchée{_where(domain, None)}")
                if first else "Pas encore assez d'offres par famille de postes")
    return {"headline": headline, "domains": by_domain, "families": by_family, "seniority": by_seniority,
            "base": base, "total": total}


def _group(rows, key) -> dict[str, list]:
    g = defaultdict(list)
    for r in rows:
        k = key(r)
        if k:
            g[k].append(r)
    return g


def stacks(facts: Facts, rows, count_by, family=None) -> dict:
    counts: Counter = Counter()
    units_by: dict = defaultdict(set)
    fam_jobs = Counter(r.role_family for r in rows)
    for r in rows:
        req = sorted(s for s, required in facts.skills.get(r.job_id, {}).items()
                     if required and tx.SKILL_FAMILY.get(s) not in ("langues", "type_client"))
        for size in (2, 3):
            for combo in combinations(req, size):
                key = (r.role_family, combo)
                counts[key] += 1
                units_by[key].add(unit(r, count_by))
    items = []
    for (fam, combo), n in counts.items():
        if n < STACK_MIN_SUPPORT or (family and fam != family):
            continue
        items.append({"family": fam, "family_label": tx.FAMILY_LABEL.get(fam, fam),
                      "skills": [tx.SKILL_LABEL.get(s, s) for s in combo], "jobs": n, "units": len(units_by[(fam, combo)]),
                      "share": round(n / fam_jobs[fam], 3) if fam_jobs[fam] else 0.0, "base": fam_jobs[fam],
                      "low_data": fam_jobs[fam] < LOW_DATA})
    # Les trios d'abord à support égal (plus informatifs), sans répéter une paire incluse dans un trio retenu
    items.sort(key=lambda i: (-i["jobs"], -len(i["skills"]), i["family_label"]))
    kept: list[dict] = []
    for i in items:
        if any(i["family"] == k["family"] and set(i["skills"]) <= set(k["skills"]) for k in kept):
            continue
        kept.append(i)
    first = kept[0] if kept else None
    headline = (f"{' + '.join(first['skills'])} : la combinaison la plus fréquente en {first['family_label'].lower()}"
                if first else "Pas encore de combinaison de compétences assez fréquente")
    return {"headline": headline, "items": kept[:MAX_STACKS], "total": len(kept)}


def salaries(rows) -> dict:
    groups = defaultdict(list)
    for r in rows:
        if r.salary_min_keur and r.salary_max_keur and r.salary_kind in ("fixe", "ote"):
            groups[(r.role_family, r.seniority_level or "inconnu", r.salary_kind)].append(
                (r.salary_min_keur + r.salary_max_keur) / 2)
    items = []
    for (fam, sen, kind), values in groups.items():
        if len(values) < MIN_JOBS:
            continue
        q1, med, q3 = quartiles(values)
        items.append({"family": fam, "family_label": tx.FAMILY_LABEL.get(fam, fam), "seniority": sen,
                      "seniority_label": tx.SENIORITY_LEVELS.get(sen, sen), "kind": kind,
                      "kind_label": tx.SALARY_KINDS[kind], "median": round(med), "q1": round(q1), "q3": round(q3),
                      "jobs": len(values), "low_data": len(values) < LOW_DATA})
    items.sort(key=lambda i: (-i["jobs"], i["family_label"]))
    with_salary = sum(len(v) for v in groups.values())
    first = items[0] if items else None
    headline = (_careful(first, f"Salaire médian affiché : {first['median']} k€ "
                                f"{'OTE ' if first['kind'] == 'ote' else ''}en {first['family_label'].lower()}"
                                f"{'' if first['seniority'] == 'inconnu' else ' ' + first['seniority_label'].lower()}")
                if first else "Trop peu d'offres affichent un salaire pour une médiane")
    return {"headline": headline, "items": items[:TOP], "total": len(items), "with_salary": with_salary,
            "base": len(rows)}


def work_modes(rows) -> dict:
    items = []
    for fam, rs in _group(rows, lambda r: r.role_family).items():
        known = [r for r in rs if r.work_mode in WORK_MODES]
        if len(known) < MIN_JOBS:
            continue
        c = Counter(r.work_mode for r in known)
        items.append({"family": fam, "label": tx.FAMILY_LABEL.get(fam, fam), "jobs": len(known),
                      "low_data": len(known) < LOW_DATA,
                      "modes": [{"key": m, "label": WORK_MODES[m], "share": round(c[m] / len(known), 3), "jobs": c[m]}
                                for m in WORK_MODES]})
    items.sort(key=lambda i: -i["jobs"])
    known_all = [r for r in rows if r.work_mode in WORK_MODES]
    headline = "Le mode de travail est rarement indiqué"
    if len(known_all) >= MIN_JOBS:
        mode, n = Counter(r.work_mode for r in known_all).most_common(1)[0]
        headline = (f"{_pct(n / len(known_all))} des offres qui le précisent sont en "
                    f"{WORK_MODES[mode].lower()}")
    return {"headline": headline, "items": items[:TOP], "total": len(items), "known": len(known_all)}


def after_funding(rows) -> dict:
    by_company: dict[int, list[MarketJob]] = defaultdict(list)
    for r in rows:
        by_company[r.company_id].append(r)
    delays, first_families = [], Counter()
    for cid, rs in by_company.items():
        funded = rs[0].funding_announced_on
        if not funded:
            continue
        after = sorted((r for r in rs if funded <= r.ref_date <= funded + timedelta(days=POST_FUNDING_WINDOW_DAYS)),
                       key=lambda r: r.ref_date)
        if not after:
            continue
        delays.append((after[0].ref_date - funded).days)
        for r in after:
            if r.ref_date == after[0].ref_date and r.role_family:
                first_families[r.role_family] += 1
    by_round = []
    for rnd in ROUND_LABELS:
        rs = [r for r in rows if funding_round(r.funding_stage) == rnd]
        tech = len({r.company_id for r in rs if r.domain == "tech"})
        gtm = len({r.company_id for r in rs if r.domain in tx.GTM_DOMAINS})
        companies = len({r.company_id for r in rs})
        if len(rs) >= MIN_JOBS:
            by_round.append({"key": rnd, "label": ROUND_LABELS[rnd], "companies": companies, "jobs": len(rs),
                             "tech_jobs": sum(r.domain == "tech" for r in rs),
                             "gtm_jobs": sum(r.domain in tx.GTM_DOMAINS for r in rs),
                             "tech_share": round(sum(r.domain == "tech" for r in rs) / len(rs), 3),
                             "gtm_share": round(sum(r.domain in tx.GTM_DOMAINS for r in rs) / len(rs), 3),
                             "tech_companies": tech, "gtm_companies": gtm, "low_data": len(rs) < LOW_DATA})
    firsts = [{"key": f, "label": tx.FAMILY_LABEL.get(f, f), "companies": n} for f, n in first_families.most_common()
              if n >= MIN_JOBS][:TOP]
    median_delay = round(statistics.median(delays)) if len(delays) >= MIN_JOBS else None
    headline = (f"Première offre publiée {median_delay} jours après la levée, en médiane ({len(delays)} startups)"
                if median_delay is not None else "Pas encore assez de levées suivies d'offres")
    return {"headline": headline, "median_delay_days": median_delay, "companies": len(delays),
            "low_data": len(delays) < LOW_DATA, "by_round": by_round, "first_families": firsts,
            "total": len(by_round) + len(firsts)}


def lifetimes(rows) -> dict:
    groups = defaultdict(list)
    for r in rows:
        if r.closed_on and r.closed_on >= r.ref_date:
            groups[r.role_family].append((r.closed_on - r.ref_date).days)
    items = [{"key": f, "label": tx.FAMILY_LABEL.get(f, f), "median_days": round(statistics.median(v)), "jobs": len(v),
              "low_data": len(v) < LOW_DATA} for f, v in groups.items() if len(v) >= MIN_JOBS]
    items.sort(key=lambda i: -i["jobs"])
    all_days = [d for v in groups.values() for d in v]
    headline = (f"Une offre reste en ligne {round(statistics.median(all_days))} jours en médiane"
                if len(all_days) >= MIN_JOBS else "Pas encore assez d'offres fermées pour mesurer leur durée")
    return {"headline": headline, "items": items[:TOP], "total": len(items), "closed": len(all_days)}


def markets(rows, count_by) -> dict:
    gtm = [r for r in rows if r.domain in tx.GTM_DOMAINS]
    base = base_units(gtm, count_by)
    groups = defaultdict(list)
    for r in gtm:
        for region in r.market_regions or []:
            groups[region].append(r)
    items, total = ranked(groups, tx.MARKET_REGIONS, base, count_by)
    first = items[0] if items else None
    headline = (_careful(first, f"{first['label']} : le marché le plus visé par les postes commerciaux et marketing")
                if first else "Les marchés visés sont rarement précisés")
    return {"headline": headline, "items": items, "total": total, "base": base}


def investors(rows) -> dict:
    groups = defaultdict(list)
    for r in rows:
        if r.closed_on is None:
            for name in investor_names(r.investors):
                groups[name].append(r)
    items = [{"key": name, "label": name, "jobs": len(rs), "companies": len({r.company_id for r in rs}),
              "low_data": len(rs) < LOW_DATA} for name, rs in groups.items() if len(rs) >= MIN_JOBS]
    items.sort(key=lambda i: (-i["jobs"], i["label"]))
    first = items[0] if items else None
    headline = (f"{first['label']} : le portefeuille qui recrute le plus ({first['jobs']} offres ouvertes)"
                if first else "Pas encore assez d'offres ouvertes par fonds")
    return {"headline": headline, "items": items[:TOP], "total": len(items)}


INDICATORS = ("skills", "trends", "roles", "stacks", "salaries", "work_modes", "after_funding", "lifetimes",
              "markets", "investors")
INDICATOR_TITLES = {
    "skills": "Compétences les plus demandées", "trends": "Compétences qui montent ou baissent",
    "roles": "Postes les plus recherchés", "stacks": "Stacks types", "salaries": "Salaires affichés",
    "work_modes": "Mode de travail", "after_funding": "Après la levée", "lifetimes": "Durée de vie d'une offre",
    "markets": "Marchés visés (postes commerciaux et marketing)", "investors": "Fonds qui recrutent le plus",
}


def perimeter(rows: list[MarketJob], period: str, today: date) -> dict:
    start = today - timedelta(days=PERIODS[period])
    jobs, companies = len(rows), len({r.company_id for r in rows})
    return {"jobs": jobs, "companies": companies, "since": start.isoformat(), "period": period,
            "text": f"{jobs} offre{'s' if jobs > 1 else ''} de {companies} startup{'s' if companies > 1 else ''} "
                    f"financée{'s' if companies > 1 else ''}, publiée{'s' if jobs > 1 else ''} depuis le "
                    f"{start.strftime('%d/%m/%Y')}, vues par Mire (hors stages et alternances)"}


def market_overview(facts: Facts, *, period: str = "90j", domain: str | None = None, family: str | None = None,
                    count_by: str = "entreprise", limit: int = TOP, today: date | None = None) -> dict:
    """Tous les indicateurs de l'onglet Marché, avec leurs effectifs et le périmètre."""
    today = today or date.today()
    rows = scope(facts, period=period, domain=domain, family=family, today=today)
    broad = scope(facts, period=period, domain=domain, family=None, today=today)  # répartition par famille
    data = {
        "skills": top_skills(facts, rows, count_by, limit, domain, family),
        "trends": skill_trends(facts, count_by, domain, family, today),
        "roles": roles(broad, count_by, limit, domain, family),
        "stacks": stacks(facts, rows, count_by, family),
        "salaries": salaries(rows),
        "work_modes": work_modes(rows),
        # Après la levée : toutes les familles (comparer tech et commercial n'a pas de sens dans une seule famille)
        "after_funding": after_funding(scope(facts, period=period, domain=None, family=None, today=today)),
        "lifetimes": lifetimes(rows),
        "markets": markets(broad if not family else rows, count_by),
        "investors": investors(rows),
    }
    return {
        "perimeter": perimeter(rows, period, today),
        "filters": {"period": period, "domain": domain, "family": family, "count_by": count_by},
        "thresholds": {"min_jobs": MIN_JOBS, "low_data": LOW_DATA, "top": TOP},
        "indicators": {k: {"title": INDICATOR_TITLES[k], "locked": False, **v} for k, v in data.items()},
    }


def options() -> dict:
    """Valeurs des filtres (liste fermée)."""
    return {
        "domains": [{"key": k, "label": v} for k, v in tx.DOMAINS.items()],
        "families": [{"key": f[0], "label": f[1], "domain": f[2]} for f in tx.ROLE_FAMILIES],
        "periods": [{"key": k, "label": v} for k, v in PERIOD_LABELS.items()],
        "count_by": [{"key": "entreprise", "label": "Par entreprise"}, {"key": "offre", "label": "Par offre"}],
    }


# --- Cache (les faits ne changent qu'une fois par jour) -------------------------------------------------------

CACHE_TTL_S = 600
_cache: dict = {}


def cached(key: tuple, compute):
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and hit[0] == _facts_version and now - hit[1] < CACHE_TTL_S:
        return hit[2]
    value = compute()
    _cache[key] = (_facts_version, now, value)
    if len(_cache) > 500:
        _cache.clear()
    return value


# --- Toi face au marché --------------------------------------------------------------------------------------

_SKILL_EXACT = {norm(label): sid for sid, label in tx.SKILL_LABEL.items()} | {
    norm(syn): sid for sid, _, _, _, syns in tx.SKILLS for syn in syns}


def map_skill(label: str) -> str | None:
    """Libellé libre (évaluation, profil) -> compétence de la liste fermée, ou None."""
    key = norm(label).strip()
    if key in _SKILL_EXACT:
        return _SKILL_EXACT[key]
    return next((sid for sid, rx in SKILL_RULES if rx is not None and rx.search(key)), None)


def user_family(db: Session, user, data) -> str | None:
    from .classify import FAMILY_RULES, _first

    text = norm(" ".join(data.preferences.roles + ([data.headline] if data.headline else [])))
    if fam := _first(FAMILY_RULES, text):
        return fam
    good = db.scalars(select(Job.role_family).join(JobAssessment, JobAssessment.job_id == Job.id).where(
        JobAssessment.user_id == user.id, JobAssessment.score >= GOOD_SCORE, Job.role_family.is_not(None))).all()
    return Counter(good).most_common(1)[0][0] if good else None


def user_seniority(years: float | None) -> str | None:
    if years is None:
        return None
    return "junior" if years < 2 else "confirme" if years < 5 else "senior" if years < 9 else "lead"


def me_overview(db: Session, user, facts: Facts, today: date | None = None) -> dict:
    from .profile import get_profile, profile_data

    today = today or date.today()
    data = profile_data(get_profile(db, user))
    family = user_family(db, user, data)
    assessments = db.scalars(select(JobAssessment).where(JobAssessment.user_id == user.id)).all()
    have = {sid for a in assessments for s in (a.skills_have or []) if (sid := map_skill(s))}
    have |= {sid for s in data.skills if s.kind == "fort" and (sid := map_skill(s.name))}

    # Compétences manquantes dans les offres bien notées pour toi
    good = [a for a in assessments if (a.manual_score if a.manual_score is not None else a.score) is not None
            and float(a.manual_score if a.manual_score is not None else a.score) >= GOOD_SCORE]
    missing = Counter()
    for a in good:
        for sid in {map_skill(s) for s in (a.skills_missing or [])} - {None}:
            missing[sid] += 1
    missing_items = [{"key": sid, "label": tx.SKILL_LABEL[sid], "jobs": n, "share": round(n / len(good), 3),
                      "base": len(good), "low_data": len(good) < LOW_DATA}
                     for sid, n in missing.most_common() if n >= ME_MIN_JOBS][:TOP]

    # Offres de ta famille (90 jours) : couverture et points forts
    fam_rows = scope(facts, period="90j", domain=None, family=family, today=today) if family else []
    with_req = [(r, {s for s, req in facts.skills.get(r.job_id, {}).items() if req}) for r in fam_rows]
    with_req = [(r, req) for r, req in with_req if req]
    covered = sum(1 for _, req in with_req if len(req & have) / len(req) >= COVERAGE_THRESHOLD)
    demand = Counter(s for _, req in with_req for s in req)
    strengths = [{"key": sid, "label": tx.SKILL_LABEL[sid], "jobs": demand[sid],
                  "share": round(demand[sid] / len(with_req), 3), "base": len(with_req),
                  "low_data": len(with_req) < LOW_DATA}
                 for sid in sorted(have & set(demand), key=lambda s: -demand[s])
                 if with_req and demand[sid] / len(with_req) > STRENGTH_MIN_SHARE][:TOP]

    # Salaire : fourchettes affichées de ta famille et de ta séniorité, face à tes attentes
    level = user_seniority(data.years_experience)
    sal_rows = [r for r in fam_rows if r.salary_min_keur and r.salary_kind == "fixe"
                and (level is None or r.seniority_level == level)]
    salary = None
    if len(sal_rows) >= ME_MIN_JOBS:
        q1, med, q3 = quartiles([(r.salary_min_keur + r.salary_max_keur) / 2 for r in sal_rows])
        expected = data.preferences.min_salary_keur
        salary = {"median": round(med), "q1": round(q1), "q3": round(q3), "jobs": len(sal_rows),
                  "seniority": level, "seniority_label": tx.SENIORITY_LEVELS.get(level or "inconnu"),
                  "expected": expected, "low_data": len(sal_rows) < LOW_DATA,
                  "share_meeting_expectation": (round(sum(r.salary_max_keur >= expected for r in sal_rows)
                                                      / len(sal_rows), 3) if expected else None)}
    fam_label = tx.FAMILY_LABEL.get(family, "") if family else None
    return {
        "family": family, "family_label": fam_label,
        "missing": {"title": "Compétences qui te manquent le plus souvent",
                    "headline": (f"{missing_items[0]['label']} manque dans {_pct(missing_items[0]['share'])} de tes "
                                 f"offres notées {GOOD_SCORE} et plus" if missing_items
                                 else "Pas encore assez d'offres bien notées pour toi"),
                    "items": missing_items, "base": len(good)},
        "coverage": {"title": "Ta couverture des offres de ta famille",
                     "headline": (f"Tu as au moins {round(COVERAGE_THRESHOLD * 100)} % des compétences exigées dans "
                                  f"{_pct(covered / len(with_req))} des offres {fam_label.lower()}"
                                  if with_req else "Indique les rôles visés dans ton profil pour te comparer à ta famille"),
                     "covered": covered, "base": len(with_req),
                     "share": round(covered / len(with_req), 3) if with_req else None,
                     "low_data": len(with_req) < LOW_DATA},
        "strengths": {"title": "Tes points forts recherchés",
                      "headline": (f"{strengths[0]['label']}, que tu as, est demandé dans {_pct(strengths[0]['share'])} "
                                   f"des offres {fam_label.lower()}" if strengths
                                   else "Pas encore de point fort qui ressorte"),
                      "items": strengths},
        "salary": {"title": "Salaires de ta famille face à tes attentes",
                   "headline": (f"Médiane affichée : {salary['median']} k€ (fixe) pour ta famille"
                                if salary else "Trop peu de salaires affichés dans ta famille"),
                   "data": salary},
    }


# --- Santé de la liste fermée (admin) ------------------------------------------------------------------------


def taxonomy_health(db: Session) -> dict:
    from sqlalchemy import func

    from .classify import count_pending
    from .models import Skill, TaxonomyMiss

    classified = db.scalar(select(func.count(Job.id)).where(Job.classified_at.is_not(None))) or 0
    autre = db.scalar(select(func.count(Job.id)).where(Job.classified_at.is_not(None),
                                                      Job.role_family == tx.OTHER)) or 0
    seen = set(db.scalars(select(JobSkill.skill_id).distinct()))
    misses = db.execute(select(TaxonomyMiss.kind, TaxonomyMiss.label, TaxonomyMiss.count, TaxonomyMiss.last_seen_at)
                        .order_by(TaxonomyMiss.count.desc()).limit(40)).all()
    return {
        "taxonomy_version": tx.TAXONOMY_VERSION, "classified": classified, "pending": count_pending(db),
        "autre": autre, "autre_share": round(autre / classified, 3) if classified else 0.0,
        "misses": [{"kind": k, "label": label, "count": n, "last_seen_at": at} for k, label, n, at in misses],
        "never_seen_skills": [{"key": sid, "label": label, "family": fam} for sid, label, fam in db.execute(
            select(Skill.id, Skill.label, Skill.family_id).order_by(Skill.position)) if sid not in seen],
    }


def now_utc() -> datetime:
    return datetime.now(UTC)


# --- Masquage Free / Pro (PLAN_LIMITS : market_*) -------------------------------------------------------------
# Un utilisateur Free ne reçoit JAMAIS la vraie donnée d'un indicateur réservé : seulement son titre (générique),
# le nombre d'éléments cachés et un aperçu en texte factice de longueur FIXE (flouté par le front).

_FAKE_ROWS = [("Lorem ipsum", "42 %"), ("Dolor sit amet", "37 %"), ("Consectetur", "31 %"), ("Adipiscing elit", "24 %"),
              ("Sed do eiusmod", "18 %")]


def _locked_preview(n: int) -> list[dict]:
    return [{"label": {"locked": True, "placeholder": label}, "value": {"locked": True, "placeholder": value}}
            for label, value in _FAKE_ROWS[:max(1, min(n, len(_FAKE_ROWS)))]]


def _locked_indicator(key: str, value: dict) -> dict:
    hidden = value.get("total")
    if hidden is None:
        hidden = len(value.get("items") or [])
    return {"title": INDICATOR_TITLES.get(key, key), "locked": True, "hidden": hidden,
            "headline": INDICATOR_TITLES.get(key, key) + " : réservé au Pro", "preview": _locked_preview(hidden)}


def mask_market(out: dict, limits: dict) -> dict:
    """Applique les limites de l'offre (Free) à la réponse de market_overview (copie)."""
    if limits.get("market_open_indicators") is None:
        return out
    top = limits["market_top"]
    open_keys = set(limits["market_open_indicators"])
    masked = {}
    for key, value in out["indicators"].items():
        if key not in open_keys:
            masked[key] = _locked_indicator(key, value)
            continue
        value = dict(value)
        if key == "skills":
            value["top"] = value["top"][:top]
            value["by_family"] = [{**f, "items": f["items"][:top]} for f in value["by_family"]]
        if key == "roles":
            value["families"] = value["families"][:top]
        value["hidden"] = max(0, (value.get("total") or 0) - top)
        masked[key] = value
    return {**out, "indicators": masked}


def locked_me() -> dict:
    """Onglet « Toi face au marché » pour un utilisateur Free : aperçu générique, aucune donnée réelle."""
    section = {"locked": True, "preview": _locked_preview(5)}
    return {
        "locked": True, "family": None, "family_label": None,
        "missing": {"title": "Compétences qui te manquent le plus souvent",
                    "headline": "Les compétences qui reviennent dans tes meilleures offres et que tu n'as pas encore",
                    **section},
        "coverage": {"title": "Ta couverture des offres de ta famille",
                     "headline": "La part des offres de ta famille dont tu coches l'essentiel", **section},
        "strengths": {"title": "Tes points forts recherchés",
                      "headline": "Ce que tu as et que le marché demande", **section},
        "salary": {"title": "Salaires de ta famille face à tes attentes",
                   "headline": "Où se situent les salaires affichés par rapport à tes attentes", **section},
    }
