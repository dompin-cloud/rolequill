"""Skill taxonomy used for resume parsing, ATS scoring, and gap analysis.

Each canonical skill maps to a list of alias substrings (lowercased). Matching is
word-boundary aware where the alias is alphanumeric to avoid false hits.
"""
import re

SKILLS = {
    # languages
    "Python": ["python"],
    "JavaScript": ["javascript", "js ", "node.js", "nodejs"],
    "TypeScript": ["typescript"],
    "Java": ["java"],
    "C#": ["c#", ".net", "dotnet", "asp.net"],
    "Go": ["golang", " go "],
    "Ruby": ["ruby", "rails"],
    "SQL": ["sql", "postgres", "postgresql", "mysql", "sql server", "t-sql"],
    "HTML/CSS": ["html", "css", "html5"],
    # data / ai
    "Machine Learning": ["machine learning", "ml ", "scikit", "pytorch", "tensorflow"],
    "LLM Integration": ["llm", "large language model", "openai api", "anthropic api",
                         "genai", "generative ai", "gpt-"],
    "Prompt Engineering": ["prompt engineering", "prompting"],
    "RAG / Vector DB": ["rag", "retrieval augmented", "vector database", "pinecone",
                        "weaviate", "embeddings"],
    "Agent Frameworks": ["langchain", "llamaindex", "crewai", "agentic", "ai agents"],
    "Data Engineering": ["data pipeline", "etl", "airflow", "dbt", "spark"],
    # automation / ipaas
    "Workato": ["workato"],
    "Zapier": ["zapier"],
    "Make.com": ["make.com", "integromat"],
    "n8n": ["n8n"],
    "iPaaS / Automation": ["ipaas", "workflow automation", "process automation",
                           "low-code", "no-code", "rpa", "uipath"],
    # integration / api
    "REST API / Webhooks": ["rest api", "restful", "webhook", "api integration",
                            "json", "graphql"],
    "Postman": ["postman"],
    # cloud / infra
    "AWS": ["aws", "amazon web services", "ec2", "s3", "lambda"],
    "Azure": ["azure", "azure monitor", "log analytics"],
    "GCP": ["gcp", "google cloud"],
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", "k8s"],
    "CI/CD": ["ci/cd", "github actions", "jenkins", "gitlab ci"],
    "Git": ["git", "github", "gitlab", "version control"],
    # support / saas tooling
    "Zendesk": ["zendesk"],
    "Jira / Confluence": ["jira", "confluence"],
    "Salesforce": ["salesforce"],
    "Incident / Escalation": ["incident", "escalation", "triage", "on-call", "t2", "tier 2"],
    "Customer-facing SaaS": ["customer-facing", "customer facing", "implementation",
                             "onboarding", "forward deployed", "solutions engineer"],
}

# Precompile a SINGLE combined regex (one named group per canonical skill) so a
# whole posting is scanned in one pass instead of ~40 separate searches. This is
# the hot path — called once per posting during scoring.
_GROUP_TO_CANON = {}
_parts = []
for _i, (_canon, _aliases) in enumerate(SKILLS.items()):
    _g = f"s{_i}"
    _GROUP_TO_CANON[_g] = _canon
    _alts = []
    for _a in _aliases:
        _a = _a.strip()
        if not _a:
            continue
        if re.fullmatch(r"[a-z0-9]+", _a):
            _alts.append(r"\b" + re.escape(_a) + r"\b")
        else:
            _alts.append(re.escape(_a))
    _parts.append(f"(?P<{_g}>" + "|".join(_alts) + ")")
_COMBINED = re.compile("|".join(_parts))


def extract_skills(text: str) -> set[str]:
    if not text:
        return set()
    found = set()
    for m in _COMBINED.finditer(text.lower()):
        found.add(_GROUP_TO_CANON[m.lastgroup])
    return found
