"""Seed lists of company board tokens for each free public ATS.

These are the slugs used in the public API URLs (e.g. boards-api.greenhouse.io
/v1/boards/<token>/jobs). Unknown/expired tokens simply return zero jobs, so the
lists are safe to extend freely. Add your own targets here.
"""

GREENHOUSE = [
    "airbnb", "stripe", "dropbox", "robinhood", "gitlab", "coinbase", "databricks",
    "brex", "ramp", "plaid", "doordash", "instacart", "asana", "figma", "discord",
    "reddit", "cloudflare", "snyk", "datadog", "gusto", "samsara", "affirm", "sofi",
    "lyft", "pinterest", "twilio", "benchling", "scaleai", "flexport", "airtable",
    "webflow", "vercel", "retool", "amplitude", "mixpanel", "segment", "hashicorp",
    "elastic", "confluent", "mongodb", "cockroachlabs", "temporaltechnologies",
    "grafanalabs", "sentry", "postman", "miro", "calendly", "loom", "clickup",
    "monday", "zapier", "make", "workato", "celonis", "uipath", "automationanywhere",
    "alteryx", "dbtlabs", "fivetran", "hightouch",
]

LEVER = [
    # verified live at build time
    "ro", "spotify", "palantir",
    # likely-valid Lever clients (misses return zero jobs, harmless)
    "kayak", "nuro", "matterport", "veriff", "cresta", "harvey", "sardine",
    "synthesia", "gohighlevel", "clari", "gong", "abnormalsecurity", "verkada",
    "tecton", "adept", "together", "glean", "mercury", "pilot", "checkr",
    "attentive", "ramp-lever", "scale-ai", "anyscale", "openstore", "ironclad",
]

ASHBY = [
    "openai", "linear", "vanta", "posthog", "replicate", "modal", "mintlify",
    "census", "astronomer", "runway", "cohere", "elevenlabs", "ramp", "notion",
    "anthropic", "supabase", "render", "neon", "warp", "arize", "baseten", "cresend",
    "browserbase", "decagon", "sierra", "hex", "weaviate", "pinecone", "langchain",
    "llamaindex", "crewai", "n8n", "windmill", "trigger", "inngest", "defog",
    "fireworks-ai", "lambda", "crusoe", "lightning-ai", "predibase", "openpipe",
    "humanloop", "braintrust", "galileo", "patronus", "raycast", "linear-app",
]

REGISTRY = {
    "Greenhouse": GREENHOUSE,
    "Lever": LEVER,
    "Ashby": ASHBY,
}
