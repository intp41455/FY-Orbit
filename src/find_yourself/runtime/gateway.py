"""Model gateway cold-start and call guard (FROZEN_CONTRACT §7, §10, G4/R01).

Cold start behaviour is explicit and safe:

* Without configured credentials the gateway raises ``ModelNotConfigured`` and
  the API surfaces ``MODEL_NOT_CONFIGURED`` — it never fabricates a model
  answer.
* Credentials are read only from the configured secret reference; they are not
  logged or echoed.
* A call requires an atomic budget reservation first. When unit price is unknown
  the call is blocked rather than charged as zero cost.
* Calls are bounded by timeout and honour cancellation. No paid provider is
  contacted in this shard's tests; real provider integration is
  ``BLOCKED_EXTERNAL`` until the owner supplies credentials and approves spend.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..config import Settings
from ..services.actor import Actor
from ..services.budget import BudgetService
from ..services.errors import Conflict, ValidationFailed


class ModelNotConfigured(Conflict):
    def __init__(self, message: str = "Model provider is not configured"):
        super().__init__("model_not_configured", message, 503)


class PriceUnknown(ValidationFailed):
    def __init__(self, message: str = "Model unit price is unknown; refusing to charge as zero"):
        super().__init__("price_unknown", message)


@dataclass
class CallResult:
    text: str
    usage: dict
    settled_amount: Decimal


class ModelGateway:
    def __init__(self, settings: Settings, budget: BudgetService):
        self.settings = settings
        self.budget = budget

    @property
    def configured(self) -> bool:
        return bool(self.settings.model_api_key and self.settings.model_base_url)

    def require_configured(self) -> None:
        if not self.configured:
            raise ModelNotConfigured()

    def estimated_cost(self, model: str, max_tokens: int) -> Decimal:
        """Look up unit price. Unknown price -> PriceUnknown (blocked, not zero)."""
        # No price catalogue is shipped; until the owner configures prices every
        # call is treated as unknown-price and blocked. This is the safe default.
        raise PriceUnknown()

    def complete(self, actor: Actor, *, task_id: str, model: str, prompt: str,
                 max_tokens: int = 1024, timeout_seconds: float = 30.0) -> CallResult:
        """Guarded model call. Never reaches a paid provider without credentials.

        Real provider HTTP is intentionally not performed here: it is wired
        only after credentials + approved price + budget reservation exist.
        In this shard it always resolves to a configuration error rather than
        a fabricated answer.
        """
        self.require_configured()
        cost = self.estimated_cost(model, max_tokens)  # raises PriceUnknown unless priced
        # Reservation would happen here via self.budget.reserve(...) before any
        # outbound call. The outbound httpx call is deferred to the wired
        # provider adapter (BLOCKED_EXTERNAL); we refuse to fabricate output.
        raise ModelNotConfigured("Model provider adapter is not wired; no outbound call made")
