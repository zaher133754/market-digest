"""Controlled Telegram integration failures."""


class TelegramIntegrationError(RuntimeError):
    """Base class for failures safe for the service supervisor to classify."""


class TelegramAuthorizationRequired(TelegramIntegrationError):
    """The configured MTProto session has not completed user authorization."""


class TelegramCollectorError(TelegramIntegrationError):
    """The collector cannot safely continue."""


class TelegramBotError(TelegramIntegrationError):
    """The owner bot failed to poll or deliver a required message."""
