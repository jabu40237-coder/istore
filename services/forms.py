"""Dynamic Service Form Engine.

Schema-driven order fields per KD1S service type.
Never invents properties — fields come only from the service type mapping.
Frontend renders fields from this schema; unknown types fall back safely.
"""
from models import Service

# Field spec: (param_name, field_type, required, i18n_label_key, placeholder_key)
_FIELD_LABELS = {
    "link": "f_link",
    "quantity": "f_quantity",
    "runs": "f_runs",
    "interval": "f_interval",
    "comments": "f_comments",
    "usernames": "f_usernames",
    "hashtags": "f_hashtags",
    "hashtag": "f_hashtag",
    "username": "f_username",
    "media": "f_media",
    "min": "f_min",
    "max": "f_max",
    "posts": "f_posts",
    "old_posts": "f_old_posts",
    "delay": "f_delay",
    "expiry": "f_expiry",
}

# service_type (lowercase) -> ordered list of (param, kind, required)
# kind: "link" | "text" | "number" | "textarea" | "username"
_SCHEMAS = {
    "subscriptions": [
        ("username", "username", True),
        ("min", "number", True),
        ("max", "number", True),
        ("posts", "number", True),
        ("old_posts", "number", False),
        ("delay", "number", False),
        ("expiry", "text", False),
    ],
    "custom comments": [
        ("link", "link", True),
        ("comments", "textarea", True),
    ],
    "custom comments package": [
        ("link", "link", True),
        ("comments", "textarea", True),
    ],
    "mentions user followers": [
        ("link", "link", True),
        ("quantity", "number", True),
        ("usernames", "textarea", True),
    ],
    "mentions custom list": [
        ("link", "link", True),
        ("quantity", "number", True),
        ("usernames", "textarea", True),
    ],
    "mentions with hashtags": [
        ("link", "link", True),
        ("quantity", "number", True),
        ("hashtags", "textarea", True),
    ],
    "mentions hashtag": [
        ("link", "link", True),
        ("quantity", "number", True),
        ("hashtag", "text", True),
    ],
    "mentions media likers": [
        ("link", "link", True),
        ("quantity", "number", True),
    ],
    "comment replies": [
        ("link", "link", True),
        ("quantity", "number", True),
        ("username", "username", False),
    ],
    "comment likes": [
        ("link", "link", True),
        ("quantity", "number", True),
    ],
    "package": [
        ("link", "link", True),
        ("quantity", "number", True),
    ],
}

_DEFAULT_SCHEMA = [
    ("link", "link", True),
    ("quantity", "number", True),
]


def get_form_schema(service: Service) -> list:
    """Return ordered field specs for a service. Never empty."""
    stype = (service.service_type or "default").strip().lower()
    raw = _SCHEMAS.get(stype, _DEFAULT_SCHEMA)
    return [
        {
            "param": p,
            "kind": k,
            "required": req,
            "label_key": _FIELD_LABELS.get(p, "f_" + p),
        }
        for (p, k, req) in raw
    ]


def extract_input_data(form: dict, service: Service) -> dict:
    """Pull only schema-defined extra params from a posted form (x_ prefix)."""
    schema = get_form_schema(service)
    allowed = {f["param"] for f in schema} - {"link", "quantity"}
    out = {}
    for k, v in form.items():
        if k.startswith("x_"):
            param = k[2:]
            if param in allowed and str(v).strip():
                out[param] = str(v).strip()
    return out


def describe_target(service: Service, link: str, input_data: dict) -> str:
    """Human-readable target for order display (never provider internals)."""
    stype = (service.service_type or "").lower()
    if "subscri" in stype:
        return "@" + (input_data.get("username") or link or "").lstrip("@")
    return link or ""
