"""Application-specific exceptions with safe operator-facing messages."""


class MarketDigestError(Exception):
    """Base error for expected application failures."""


class ConfigurationError(MarketDigestError):
    """The application configuration violates a hard invariant."""


class LunaError(MarketDigestError):
    """Base error for the mandatory Luna analysis path."""


class LunaUnavailableError(LunaError):
    """The exact required model could not complete a request."""


class LunaContractError(LunaError):
    """The model response violated the strict application contract."""


class DigestNotReadyError(MarketDigestError):
    """A digest cannot be published because a required stage is incomplete."""
