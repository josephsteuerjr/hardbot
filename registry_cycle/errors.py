"""Доменные ошибки приложения."""


class RegistryCycleError(RuntimeError):
    """Базовая ожидаемая ошибка цикла."""


class ConfigurationError(RegistryCycleError):
    """Конфигурация неполна или противоречива."""


class WorkbookReadError(RegistryCycleError):
    """Книга не читается либо имеет неизвестную схему."""


class SafetyViolation(RegistryCycleError):
    """Операция остановлена защитным инвариантом."""


class PublishError(RegistryCycleError):
    """Не удалось опубликовать уже проверенный staging-набор."""
