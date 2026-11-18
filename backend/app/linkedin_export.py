"""Lecture de l'export de données LinkedIn (ZIP « Obtenir une copie de vos données »).

Le ZIP est lu entièrement en mémoire, jamais extrait sur disque. Protection zip-bomb :
nombre d'entrées limité, seuls les CSV utiles sont décompressés, taille décompressée plafonnée
(par fichier et au total, mesurée en lisant réellement les données, pas seulement l'en-tête du ZIP).

Les CSV LinkedIn changent de temps en temps (colonnes renommées, ajoutées, retirées, lignes de notes
au début) : les colonnes sont reconnues par alias, et une colonne absente est simplement ignorée.
"""

import csv
import io
import re
import unicodedata
import zipfile

from fastapi import HTTPException

from .profile import Education, Experience, Language, ProfileData, Skill

MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_ENTRIES = 5_000
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_ROWS = 500

WANTED = {"profile.csv", "positions.csv", "skills.csv", "education.csv", "languages.csv"}

# Colonnes reconnues : clé interne -> noms possibles (normalisés : minuscules, sans accents ni espaces)
ALIASES = {
    "first_name": ["firstname", "prenom"],
    "last_name": ["lastname", "nom", "nomdefamille"],
    "headline": ["headline", "titre", "titreduprofil"],
    "summary": ["summary", "resume", "about", "infos"],
    "location": ["geolocation", "location", "localisation", "lieu"],  # jamais "Address" (adresse postale)
    "company": ["companyname", "company", "entreprise", "nomdelentreprise", "organization"],
    "title": ["title", "titre", "poste", "intitule", "intituledeposte", "position"],
    "description": ["description"],
    "start": ["startedon", "startdate", "start", "datededebut", "debut"],
    "end": ["finishedon", "enddate", "end", "datedefin", "fin"],
    "name": ["name", "nom", "skill", "competence", "language", "langue"],
    "proficiency": ["proficiency", "niveau", "level"],
    "school": ["schoolname", "school", "ecole", "etablissement"],
    "degree": ["degreename", "degree", "diplome"],
    "field": ["fieldofstudy", "domaine", "domainedetudes"],
}

# Colonnes lues dans chaque fichier (un même alias, ex. « titre », n'a pas le même sens partout)
FILE_KEYS = {
    "profile.csv": ("first_name", "last_name", "headline", "summary", "location"),
    "positions.csv": ("company", "title", "description", "location", "start", "end"),
    "education.csv": ("school", "degree", "field", "start", "end"),
    "skills.csv": ("name",),
    "languages.csv": ("name", "proficiency"),
}

MONTHS = {
    "jan": 1, "janv": 1, "janvier": 1, "january": 1,
    "feb": 2, "fev": 2, "fevr": 2, "fevrier": 2, "february": 2,
    "mar": 3, "mars": 3, "march": 3,
    "apr": 4, "avr": 4, "avril": 4, "april": 4,
    "may": 5, "mai": 5,
    "jun": 6, "juin": 6, "june": 6,
    "jul": 7, "juil": 7, "juillet": 7, "july": 7,
    "aug": 8, "aou": 8, "aout": 8, "august": 8,
    "sep": 9, "sept": 9, "septembre": 9, "september": 9,
    "oct": 10, "octobre": 10, "october": 10,
    "nov": 11, "novembre": 11, "november": 11,
    "dec": 12, "decembre": 12, "december": 12,
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def normalize_date(value: str | None) -> str | None:
    """"Mar 2021" / "mars 2021" / "03/2021" / "2021-03" / "2021" -> "2021-03" ou "2021"."""
    if not value or not value.strip():
        return None
    v = value.strip()
    if m := re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-\d{1,2})?", v):
        return f"{m[1]}-{int(m[2]):02d}" if m[2] else m[1]
    if m := re.fullmatch(r"(\d{1,2})/(\d{4})", v):
        return f"{m[2]}-{int(m[1]):02d}"
    if m := re.fullmatch(r"([^\W\d]+)\.?\s+(\d{4})", v):
        month = MONTHS.get(_norm(m[1]))
        if month:
            return f"{m[2]}-{month:02d}"
    return v[:30]


def decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _read_csv(text: str, keys: tuple[str, ...]) -> list[dict[str, str]]:
    """Lit un CSV LinkedIn : ignore les éventuelles lignes de notes avant l'en-tête, colonnes par alias."""
    aliases = {key: ALIASES[key] for key in keys}
    lines = text.splitlines()
    known = {alias for names in aliases.values() for alias in names}
    start = 0
    for i, line in enumerate(lines[:20]):
        cells = next(csv.reader([line]), [])
        if any(_norm(c) in known for c in cells):
            start = i
            break
    reader = csv.reader(io.StringIO("\n".join(lines[start:])))
    header = next(reader, None)
    if not header:
        return []
    columns: dict[int, str] = {}
    for idx, raw in enumerate(header):
        n = _norm(raw)
        for key, names in aliases.items():
            if n in names and key not in columns.values():
                columns[idx] = key
                break
    rows = []
    for cells in reader:
        if len(rows) >= MAX_ROWS:
            break
        row = {key: cells[idx].strip() for idx, key in columns.items() if idx < len(cells) and cells[idx].strip()}
        if row:
            rows.append(row)
    return rows


def read_zip(data: bytes) -> dict[str, str]:
    """Contenu texte des CSV utiles, par nom de fichier en minuscules (ex. "skills.csv")."""
    if len(data) > MAX_ZIP_BYTES:
        raise HTTPException(413, "Archive trop lourde (20 Mo max)")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(422, "Fichier ZIP invalide") from None
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise HTTPException(422, "Archive refusée : trop de fichiers")
        files: dict[str, str] = {}
        total = 0
        for info in infos:
            name = info.filename.replace("\\", "/").rsplit("/", 1)[-1].lower()
            if info.is_dir() or name not in WANTED or name in files:
                continue
            if info.flag_bits & 0x1:
                raise HTTPException(422, "Archive chiffrée non prise en charge")
            if info.file_size > MAX_FILE_BYTES:
                raise HTTPException(422, f"Fichier {name} trop volumineux")
            with zf.open(info) as fh:
                content = fh.read(MAX_FILE_BYTES + 1)  # taille réelle, même si l'en-tête ment
            if len(content) > MAX_FILE_BYTES:
                raise HTTPException(422, f"Fichier {name} trop volumineux")
            total += len(content)
            if total > MAX_TOTAL_BYTES:
                raise HTTPException(422, "Archive refusée : contenu décompressé trop volumineux")
            files[name] = decode(content)
    return files


def parse_export(data: bytes) -> tuple[ProfileData, list[str]]:
    """ZIP -> (profil extrait, fichiers reconnus)."""
    files = read_zip(data)
    if not files:
        raise HTTPException(
            422,
            "Aucun fichier de profil reconnu dans l'archive (Profile.csv, Positions.csv, Skills.csv, "
            "Education.csv, Languages.csv). Vérifiez qu'il s'agit bien de l'export de données LinkedIn.",
        )
    return parse_files(files), sorted(files)


def _valid(model, **values):
    """Construit un élément en ignorant ceux qui ne passent pas la validation (ligne incomplète)."""
    try:
        return model.model_validate({k: v for k, v in values.items() if v not in (None, "")})
    except ValueError:
        return None


def parse_files(files: dict[str, str]) -> ProfileData:
    def read_csv(name: str) -> list[dict[str, str]]:
        return _read_csv(files[name], FILE_KEYS[name])

    data: dict = {}

    if "profile.csv" in files and (rows := read_csv("profile.csv")):
        row = rows[0]
        data["headline"] = row.get("headline", "")[:300] or None
        data["summary"] = row.get("summary", "")[:5000] or None
        data["location"] = row.get("location", "")[:200] or None

    if "positions.csv" in files:
        data["experiences"] = [e for row in read_csv("positions.csv")[:60] if (e := _valid(
            Experience,
            company=row.get("company", "")[:200],
            title=row.get("title", "")[:200],
            start=normalize_date(row.get("start")),
            end=normalize_date(row.get("end")),
            location=row.get("location", "")[:200],
            description=row.get("description", "")[:5000],
        )) and (e.company or e.title)]

    if "education.csv" in files:
        data["education"] = [e for row in read_csv("education.csv")[:30] if (e := _valid(
            Education,
            school=row.get("school", "")[:200],
            degree=row.get("degree", "")[:200],
            field=row.get("field", "")[:200],
            start=normalize_date(row.get("start")),
            end=normalize_date(row.get("end")),
        )) and (e.school or e.degree)]

    if "skills.csv" in files:
        data["skills"] = [s for row in read_csv("skills.csv")[:200]
                          if (s := _valid(Skill, name=row.get("name", "")[:120]))]

    if "languages.csv" in files:
        data["languages"] = [lang for row in read_csv("languages.csv")[:30]
                             if (lang := _valid(Language, name=row.get("name", "")[:120],
                                                level=row.get("proficiency", "")[:60]))]

    return ProfileData.model_validate({k: v for k, v in data.items() if v is not None})
