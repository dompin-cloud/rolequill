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
    }
