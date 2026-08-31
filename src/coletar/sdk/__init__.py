"""The coletar SDK — a thin async client over the canonical graph (ROADMAP M7).

Thin on purpose. Everything it can do, the REST API can do, and every call goes
through the same authentication, tenant resolution, scope checks and rate limiting as
any other connector. An SDK that reached past the API would be a second
implementation of the rules, and the second one is always the one that drifts.

**It exposes no hard-delete, and that is a property rather than a promise.** There is
no `delete()` here because there is no endpoint under it: constraint 6 says the graph
never hard-deletes, so `retire()` excludes an object from retrieval and from compile
while leaving it readable. A convenience method that removed a row would turn a
guarantee into a convention, and conventions get worked around.

**It sends no telemetry.** Not "redacted by default" — none. The client contacts the
base URL it was given and nothing else, which is a claim a test can check and a
privacy policy cannot.
"""

from coletar.sdk.client import Coletar, ColetarError, NotFound, RateLimited, Unauthorized

__all__ = ["Coletar", "ColetarError", "NotFound", "RateLimited", "Unauthorized"]
