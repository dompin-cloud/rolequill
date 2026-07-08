"""Section-aware resume profiling.

Beyond the fixed skill taxonomy, this pulls the candidate's *own* vocabulary from
the Core Skills, Education, and Professional Development / Certifications sections,
so the search can rank jobs by how well they align with that profile — even when
no job title is given.
"""
import re

# section name -> regex of header aliases
_SECTIONS = {
    "core_skills": r"(?:core (?:competenc\w+|skills)|technical skills|key skills|"
                   r"areas? of (?:expertise|focus)|skills?(?: ?& ?tools)?|"
                   r"competencies|proficiencies|tools? ?& ?technolog\w+)",
    "education": r"(?:education|academic background|academics)",
    "prof_dev": r"(?:professional development|certification\w*|licenses?|"
                r"courses?(?: ?& ?training)?|training|continuing education|"
                r"credentials|coursework)",
}

# headers that end a section we care about (so we stop collecting)
_ANY_HEADER = re.compile(
    r"^\s*(?:" + "|".join([
        v for v in _SECTIONS.values()
    ]) + r"|experience|employment|work history|professional experience|"
    r"projects?|summary|objective|profile|contact|references|awards|"
    r"publications|volunteer|interests)\s*:?\s*$", re.I)

_SECTION_RE = {name: re.compile(rf"^\s*{pat}\s*:?\s*$", re.I)
               for name, pat in _SECTIONS.items()}

_STOP = {"and", "or", "the", "with", "for", "of", "to", "in", "a", "an", "etc",
         "including", "various", "other", "skills", "tools", "knowledge",
         "experience", "proficient", "familiar", "strong", "excellent"}

_SPLIT = re.compile(r"[,•·▪◦‣•|/;:\t]+|\s{3,}|\s[-–—]\s")


_DATE_JUNK = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*\d|present|"
    r"^\d{4}$|^\W*$", re.I)


def _clean_term(term: str) -> str:
    term = term.strip().strip("•·-–—*").strip()
    term = term.strip(" .,;:()[]")
    term = re.sub(r"\s+", " ", term)
    return term


def _is_junk(term: str) -> bool:
    if not term or len(term) < 3:
        return True
    if not any(c.isalpha() for c in term):
        return True
    return bool(_DATE_JUNK.search(term))


def split_sections(text: str) -> dict:
    """Return {section_name: [lines]} for the sections we care about."""
    lines = text.splitlines()
    out = {k: [] for k in _SECTIONS}
    current = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        # is this line a header?
        matched_section = None
        for name, rx in _SECTION_RE.items():
            if rx.match(line):
                matched_section = name
                break
        if matched_section:
            current = matched_section
            continue
        # an unrelated header ends the current section
        if current and _ANY_HEADER.match(line):
            current = None
            continue
        if current:
            out[current].append(line)
    return out


def _terms_from_lines(lines, max_words=5):
    terms = set()
    for line in lines:
        for chunk in _SPLIT.split(line):
            t = _clean_term(chunk)
            if not t:
                continue
            words = t.split()
            if not (1 <= len(words) <= max_words):
                continue
            low = t.lower()
            if low in _STOP or _is_junk(low):
                continue
            # drop pure sentence fragments (likely prose, not a skill)
            if len(words) >= 4 and not any(c.isupper() for c in t) and "," not in line:
                # long lowercase phrase from prose — keep only if it looks techy
                if not re.search(r"\b(api|sql|cloud|data|system|software|"
                                 r"management|engineering|automation)\b", low):
                    continue
            terms.add(low)
    return terms


# Inline labels (robust to PDF reflow that breaks section boundaries).
_SKILL_LABEL = re.compile(
    r"\b(skills?|tools?|technolog\w+|competenc\w+|proficienc\w+|expertise|"
    r"stack|languages?|frameworks?|platforms?|design|prototyp\w+|iteration|"
    r"documentation|automation|engineering)\b", re.I)
_EDU_LABEL = re.compile(
    r"\b(education|degree|b\.?s\.?|b\.?a\.?|m\.?s\.?|m\.?b\.?a\.?|ph\.?d\.?|"
    r"associate|bachelor|master|diploma|ged|university|college|coursework)\b", re.I)
_DEV_LABEL = re.compile(
    r"\b(certificat\w+|certified|license\w*|credential\w*|training|course\w*|"
    r"continuing education|bootcamp|nanodegree|specialization)\b", re.I)

_LABELLED = re.compile(r"^\s*(?P<label>[A-Za-z][\w &/()+-]{0,45}?):\s*(?P<body>.+)$")


def _labelled_lines(text: str):
    """Pull (bucket, content) from 'Label: a, b, c' lines anywhere in the resume."""
    core_c, edu_c, dev_c = [], [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LABELLED.match(line)
        if m:
            label, body = m.group("label"), m.group("body")
            if _EDU_LABEL.search(label):
                edu_c.append(body)
            elif _DEV_LABEL.search(label):
                dev_c.append(body)
            elif _SKILL_LABEL.search(label) or body.count(",") >= 2:
                core_c.append(body)
            continue
        # un-labelled education / certificate lines (PDF reflow drops the colon)
        if _DEV_LABEL.search(line) and len(line.split()) <= 14:
            dev_c.append(line)
        elif _EDU_LABEL.search(line) and len(line.split()) <= 14:
            edu_c.append(line)
    return core_c, edu_c, dev_c


# --- occupation detection (industry-agnostic) --------------------------------
# Broad set of role head-nouns spanning fields. Matching one as a whole word marks
# a likely job-title phrase; we capture up to two preceding modifier/capitalized
# words ("Registered Nurse", "Senior Staff Accountant") and rank by frequency, so
# the aggregator query targets the candidate's ACTUAL field — not the tech roster.
_ROLE_HEADS = {
    # tech / data
    "engineer", "developer", "programmer", "architect", "administrator", "analyst",
    "scientist", "technologist", "technician", "devops", "sysadmin",
    # healthcare
    "nurse", "physician", "doctor", "surgeon", "dentist", "hygienist", "pharmacist",
    "therapist", "paramedic", "phlebotomist", "radiographer", "sonographer",
    "practitioner", "psychologist", "dietitian", "optometrist", "veterinarian",
    "midwife", "aide", "caregiver", "medic", "epidemiologist",
    # business / office / finance
    "manager", "director", "supervisor", "coordinator", "specialist", "officer",
    "executive", "consultant", "accountant", "bookkeeper", "auditor", "controller",
    "recruiter", "representative", "clerk", "receptionist", "secretary", "planner",
    "buyer", "estimator", "strategist", "generalist", "partner", "advisor", "agent",
    "broker", "underwriter", "adjuster", "teller", "cashier", "banker", "actuary",
    "associate", "assistant",
    # sales / marketing
    "salesperson", "seller", "marketer", "merchandiser", "copywriter",
    # education
    "teacher", "professor", "instructor", "tutor", "educator", "principal",
    "lecturer", "librarian", "paraprofessional", "counselor",
    # legal
    "attorney", "lawyer", "paralegal", "counsel", "mediator",
    # trades / labor / logistics
    "electrician", "plumber", "carpenter", "welder", "machinist", "mechanic",
    "installer", "painter", "roofer", "mason", "fabricator", "operator", "laborer",
    "foreman", "superintendent", "driver", "dispatcher", "logistician", "picker",
    "packer", "custodian", "janitor", "groundskeeper", "landscaper", "farmer",
    "rancher", "miner", "surveyor", "inspector",
    # food / hospitality
    "chef", "cook", "baker", "server", "waiter", "waitress", "bartender", "barista",
    "host", "housekeeper", "concierge", "valet",
    # creative / media
    "designer", "artist", "illustrator", "animator", "photographer", "videographer",
    "editor", "writer", "journalist", "producer", "stylist", "curator",
    # personal care / service / public safety
    "barber", "cosmetologist", "esthetician", "trainer", "groomer", "firefighter",
    "guard", "paralegal",
}

# Modifiers that legitimately precede a role head ("Senior", "Registered", "Line"…).
# A whitelist (rather than "any capitalized word") is deliberate — it keeps company
# names and date fragments ("TechCo", "Present") out of the detected title phrase.
_ROLE_MODS = {
    # seniority / rank
    "senior", "junior", "lead", "principal", "staff", "chief", "head", "associate",
    "assistant", "master", "journeyman", "apprentice", "vice", "executive",
    "director", "deputy", "entry", "mid", "level",
    # licensure / clinical
    "registered", "licensed", "certified", "clinical", "surgical", "pediatric",
    "geriatric", "emergency", "intensive", "operating", "dental", "medical",
    "respiratory", "physical", "occupational", "speech", "behavioral", "mental",
    "home", "charge", "travel", "unit", "ward", "care", "patient",
    # business function
    "regional", "district", "general", "corporate", "field", "operations", "project",
    "product", "program", "account", "sales", "marketing", "financial", "finance",
    "administrative", "office", "technical", "customer", "client", "digital",
    "creative", "graphic", "content", "social", "media", "brand", "public", "event",
    "human", "talent", "quality", "safety", "environmental", "supply", "logistics",
    "inventory", "procurement", "payroll", "tax", "audit", "cost", "credit", "loan",
    "branch", "store", "retail", "warehouse", "delivery", "route",
    # engineering / trade specialties
    "mechanical", "electrical", "civil", "industrial", "software", "data", "systems",
    "network", "security", "support", "help", "service", "maintenance", "facilities",
    "equipment", "heavy", "line", "process", "production", "manufacturing",
    # food / hospitality
    "prep", "sous", "pastry", "grill", "line", "kitchen", "food", "banquet",
    # education
    "special", "elementary", "secondary", "substitute", "teaching", "school",
    # shift qualifiers
    "night", "day", "shift", "front", "back", "floor",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z.&/+-]*")


def detect_roles(text: str, top: int = 3):
    """Return the candidate's most prominent job-title phrases, most frequent first.

    Scans the whole resume for occupation head-nouns and grabs up to two preceding
    modifier/capitalized words to form the title phrase. Field-agnostic: works for
    'Registered Nurse', 'Staff Accountant', 'Line Cook', 'Software Engineer', etc.
    """
    words = _WORD_RE.findall(text or "")
    lower = [w.lower() for w in words]
    from collections import Counter
    phrases = Counter()
    for i, w in enumerate(lower):
        if w not in _ROLE_HEADS:
            continue
        parts = [words[i]]
        j, grabbed = i - 1, 0
        while j >= 0 and grabbed < 2:
            pw = lower[j]
            # only whitelisted modifiers — keeps company names / dates out of the title
            if pw in _ROLE_MODS and pw not in _ROLE_HEADS:
                parts.insert(0, words[j])
                j -= 1
                grabbed += 1
            else:
                break
        phrases[" ".join(parts).lower()] += 1
    # prefer multi-word phrases on ties (more specific than a bare head noun)
    ranked = sorted(phrases.items(), key=lambda kv: (kv[1], len(kv[0].split())),
                    reverse=True)
    return [p for p, _ in ranked[:top]]


def search_query(text: str, profile: dict | None = None) -> str:
    """Industry-agnostic aggregator query derived from the resume, role-first.

    Falls back to the strongest Core-Skills terms (never education/location noise)
    when no occupation is detected, and to '' when the resume yields nothing usable.
    """
    roles = detect_roles(text, top=1)
    if roles:
        return roles[0]
    core = [c for c in ((profile or {}).get("core_skills") or []) if len(c) >= 4]
    return " ".join(core[:3])


def build_profile(text: str) -> dict:
    """Return a profile dict with section terms and a combined term set.

    Combines header-bounded sections (works for clean DOCX) with label-based
    extraction (robust to PDF reflow)."""
    sections = split_sections(text)
    lab_core, lab_edu, lab_dev = _labelled_lines(text)

    core = _terms_from_lines(sections["core_skills"]) | _terms_from_lines(lab_core)
    edu = _terms_from_lines(sections["education"], 6) | _terms_from_lines(lab_edu, 6)
    dev = _terms_from_lines(sections["prof_dev"], 6) | _terms_from_lines(lab_dev, 6)

    combined = set(sorted(core | edu | dev, key=len, reverse=True)[:80])
    return {
        "core_skills": sorted(core)[:40],
        "education": sorted(edu)[:20],
        "prof_dev": sorted(dev)[:20],
        "terms": sorted(combined),
        "roles": detect_roles(text, top=3),   # detected occupation(s), most frequent first
    }
