"""Verified company board tokens for each free public ATS, tagged by industry.

Each entry is (token, industry). Tokens were validated live against the public ATS
APIs at build time; unknown/expired tokens simply return zero jobs, so the lists are
safe to extend. Industry tags drive FIELD-AWARE selection (`select_tokens`): a search
scans companies matching the candidate's detected field first, then fills the rest, so
a nurse's or accountant's search isn't spent entirely on tech boards.

NOTE: Greenhouse/Lever/Ashby skew toward tech/startups, so non-tech coverage here is
adjacent-industry (fintech, healthtech, consumer/DTC, media, edtech, logistics-tech),
NOT e.g. hospital nursing or K-12 teaching — those employers use other ATSes and are
surfaced via the Google Jobs / JSearch aggregators instead.
"""

# industry buckets: tech finance health consumer media education logistics
COMPANIES = {
    'Greenhouse': [
    ('brooklinen', 'consumer'),
    ('classpass', 'consumer'),
    ('everlane', 'consumer'),
    ('faire', 'consumer'),
    ('glossier', 'consumer'),
    ('gostudent', 'consumer'),
    ('gymshark', 'consumer'),
    ('hellofresh', 'consumer'),
    ('instacart', 'consumer'),
    ('lyft', 'consumer'),
    ('olipop', 'consumer'),
    ('onrunning', 'consumer'),
    ('oura', 'consumer'),
    ('peloton', 'consumer'),
    ('pinterest', 'consumer'),
    ('reformation', 'consumer'),
    ('ritual', 'consumer'),
    ('seatgeek', 'consumer'),
    ('sweetgreen', 'consumer'),
    ('coursera', 'education'),
    ('duolingo', 'education'),
    ('edmentum', 'education'),
    ('guild', 'education'),
    ('masterclass', 'education'),
    ('nerdy', 'education'),
    ('newsela', 'education'),
    ('outschool', 'education'),
    ('udacity', 'education'),
    ('udemy', 'education'),
    ('adyen', 'finance'),
    ('affirm', 'finance'),
    ('alloy', 'finance'),
    ('betterment', 'finance'),
    ('block', 'finance'),
    ('brex', 'finance'),
    ('carta', 'finance'),
    ('checkr', 'finance'),
    ('chime', 'finance'),
    ('coalition', 'finance'),
    ('coinbase', 'finance'),
    ('current', 'finance'),
    ('fireblocks', 'finance'),
    ('gemini', 'finance'),
    ('gusto', 'finance'),
    ('marqeta', 'finance'),
    ('mercury', 'finance'),
    ('monzo', 'finance'),
    ('robinhood', 'finance'),
    ('sofi', 'finance'),
    ('calm', 'health'),
    ('cerebral', 'health'),
    ('cloverhealth', 'health'),
    ('henrymeds', 'health'),
    ('komodohealth', 'health'),
    ('onemedical', 'health'),
    ('oscar', 'health'),
    ('zocdoc', 'health'),
    ('everlaw', 'logistics'),
    ('fetch', 'logistics'),
    ('flexport', 'logistics'),
    ('greenhouse', 'logistics'),
    ('instawork', 'logistics'),
    ('kodiak', 'logistics'),
    ('lattice', 'logistics'),
    ('nuro', 'logistics'),
    ('project44', 'logistics'),
    ('samsara', 'logistics'),
    ('axios', 'media'),
    ('descript', 'media'),
    ('discord', 'media'),
    ('epicgames', 'media'),
    ('medium', 'media'),
    ('riotgames', 'media'),
    ('roblox', 'media'),
    ('rockstargames', 'media'),
    ('scopely', 'media'),
    ('twitch', 'media'),
    ('airbnb', 'tech'),
    ('airtable', 'tech'),
    ('amplitude', 'tech'),
    ('anthropic', 'tech'),
    ('asana', 'tech'),
    ('calendly', 'tech'),
    ('celonis', 'tech'),
    ('cloudflare', 'tech'),
    ('cockroachlabs', 'tech'),
    ('databricks', 'tech'),
    ('datadog', 'tech'),
    ('dropbox', 'tech'),
    ('elastic', 'tech'),
    ('figma', 'tech'),
    ('fivetran', 'tech'),
    ('galileo', 'tech'),
    ('gitlab', 'tech'),
    ('grafanalabs', 'tech'),
    ('hightouch', 'tech'),
    ('make', 'tech'),
    ('mixpanel', 'tech'),
    ('mongodb', 'tech'),
    ('okta', 'tech'),
    ('postman', 'tech'),
    ('reddit', 'tech'),
    ('stripe', 'tech'),
    ('temporaltechnologies', 'tech'),
    ('twilio', 'tech'),
    ('vannevarlabs', 'tech'),
    ('vercel', 'tech'),
    ('webflow', 'tech'),
    ('workato', 'tech'),
    ],
    'Lever': [
    ('gopuff', 'consumer'),
    ('whoop', 'consumer'),
    ('brilliant', 'education'),
    ('coderpad', 'education'),
    ('anchorage', 'finance'),
    ('wealthfront', 'finance'),
    ('aledade', 'health'),
    ('ro', 'health'),
    ('gohighlevel', 'logistics'),
    ('waabi', 'logistics'),
    ('spotify', 'media'),
    ('theathletic', 'media'),
    ('neon', 'tech'),
    ('palantir', 'tech'),
    ],
    'Ashby': [
    ('away', 'consumer'),
    ('hopper', 'consumer'),
    ('kayak', 'consumer'),
    ('poshmark', 'consumer'),
    ('rothys', 'consumer'),
    ('strava', 'consumer'),
    ('handshake', 'education'),
    ('multiverse', 'education'),
    ('column', 'finance'),
    ('dave', 'finance'),
    ('ledger', 'finance'),
    ('lemonade', 'finance'),
    ('moderntreasury', 'finance'),
    ('nubank', 'finance'),
    ('paxos', 'finance'),
    ('plaid', 'finance'),
    ('ramp', 'finance'),
    ('benchling', 'health'),
    ('capsule', 'health'),
    ('cedar', 'health'),
    ('commure', 'health'),
    ('headway', 'health'),
    ('maven', 'health'),
    ('talkiatry', 'health'),
    ('ashby', 'logistics'),
    ('shiftkey', 'logistics'),
    ('dailypay', 'media'),
    ('opusclip', 'media'),
    ('patreon', 'media'),
    ('substack', 'media'),
    ('supercell', 'media'),
    ('abridge', 'tech'),
    ('anyscale', 'tech'),
    ('astronomer', 'tech'),
    ('baseten', 'tech'),
    ('braintrust', 'tech'),
    ('browserbase', 'tech'),
    ('clickup', 'tech'),
    ('cohere', 'tech'),
    ('confluent', 'tech'),
    ('crusoe', 'tech'),
    ('cursor', 'tech'),
    ('decagon', 'tech'),
    ('elevenlabs', 'tech'),
    ('harvey', 'tech'),
    ('inngest', 'tech'),
    ('lambda', 'tech'),
    ('langchain', 'tech'),
    ('linear', 'tech'),
    ('llamaindex', 'tech'),
    ('mintlify', 'tech'),
    ('miro', 'tech'),
    ('modal', 'tech'),
    ('n8n', 'tech'),
    ('notion', 'tech'),
    ('openai', 'tech'),
    ('perplexity', 'tech'),
    ('pinecone', 'tech'),
    ('posthog', 'tech'),
    ('render', 'tech'),
    ('replit', 'tech'),
    ('runway', 'tech'),
    ('sentry', 'tech'),
    ('sierra', 'tech'),
    ('snowflake', 'tech'),
    ('supabase', 'tech'),
    ('uipath', 'tech'),
    ('warp', 'tech'),
    ('weaviate', 'tech'),
    ('windmill', 'tech'),
    ('zapier', 'tech'),
    ],
}

# map a resume role head-noun -> industry bucket (field-aware company selection)
ROLE_INDUSTRY = {
    # health
    **{h: "health" for h in ("nurse", "physician", "doctor", "surgeon", "dentist",
       "hygienist", "pharmacist", "therapist", "paramedic", "phlebotomist",
       "radiographer", "sonographer", "practitioner", "psychologist", "dietitian",
       "optometrist", "veterinarian", "midwife", "aide", "caregiver", "medic",
       "epidemiologist")},
    # finance
    **{h: "finance" for h in ("accountant", "bookkeeper", "auditor", "controller",
       "actuary", "underwriter", "adjuster", "teller", "banker", "broker")},
    # education
    **{h: "education" for h in ("teacher", "professor", "instructor", "tutor",
       "educator", "principal", "lecturer", "librarian", "paraprofessional")},
    # media / creative
    **{h: "media" for h in ("designer", "artist", "illustrator", "animator",
       "photographer", "videographer", "editor", "writer", "journalist", "producer",
       "stylist", "curator")},
    # consumer / food / hospitality
    **{h: "consumer" for h in ("chef", "cook", "baker", "server", "waiter",
       "waitress", "bartender", "barista", "host", "housekeeper", "concierge",
       "valet", "merchandiser")},
    # tech
    **{h: "tech" for h in ("engineer", "developer", "programmer", "architect",
       "scientist", "technologist", "devops", "sysadmin")},
    # logistics / field ops / trades (thin ATS coverage; aggregators fill the rest)
    **{h: "logistics" for h in ("driver", "dispatcher", "logistician", "picker",
       "packer", "custodian", "landscaper", "foreman", "superintendent", "operator",
       "installer", "electrician", "plumber", "carpenter", "welder", "machinist",
       "mechanic")},
}


def industries_for(roles):
    """Set of industry buckets implied by the resume's detected role phrases."""
    import re
    out = set()
    for r in (roles or []):
        for w in re.findall(r"[a-z]+", (r or "").lower()):
            ind = ROLE_INDUSTRY.get(w)
            if ind:
                out.add(ind)
    return out


def select_tokens(provider, industries, limit):
    """Field-aware token selection: companies matching the candidate's industries
    first (so the limited per-search budget lands on relevant boards), then fill the
    remainder with the rest. When no field is known, interleave across industries for
    a broad, balanced scan instead of clustering on one bucket."""
    rows = COMPANIES.get(provider, [])
    if industries:
        prio = [t for t, ind in rows if ind in industries]
        rest = [t for t, ind in rows if ind not in industries]
        return (prio + rest)[:limit]
    # no field signal — round-robin across industry buckets so a generic search still
    # touches tech, finance, health, consumer, media, education and logistics boards
    from itertools import chain, zip_longest
    buckets = {}
    for t, ind in rows:
        buckets.setdefault(ind, []).append(t)
    interleaved = [t for t in chain.from_iterable(zip_longest(*buckets.values())) if t]
    return interleaved[:limit]


# back-compat: flat {provider: [tokens]} for any caller that just wants every token
REGISTRY = {p: [t for t, _ in rows] for p, rows in COMPANIES.items()}
