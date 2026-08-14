"""Rate limits for the endpoints where unlimited attempts are the actual risk.

Scoped classes rather than a global default: throttling every read would degrade the
dashboard without protecting anything, whereas these four endpoints each have a specific
abuse case.
"""

from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    """Credential brute force. Keyed by IP, since the caller is unauthenticated by
    definition."""

    scope = "login"


class ESignatureRateThrottle(UserRateThrottle):
    """Electronic-signature password guessing.

    Keyed by user: the attacker here already holds a valid token (a hijacked session) and
    is guessing the password needed to step up to signing authority. Rate-limiting slows
    that materially, but it is weaker than failure-only counting with account lockout --
    see docs/SECURITY.md for the residual risk.
    """

    scope = "esignature"


class AIGenerationRateThrottle(UserRateThrottle):
    """Quiz generation fans out to one LLM call per SOP chunk and blocks a web worker for
    up to 120s, so it is both the most expensive endpoint and the easiest to weaponise."""

    scope = "ai_generate"


class SopChatRateThrottle(UserRateThrottle):
    """One LLM call per question. Open to every authenticated role, so it has the widest
    caller population of any AI endpoint."""

    scope = "sop_chat"
