class ConfigurationError(Exception):
    """
    Raised when a configuration error occurs.
    """

    pass


class ConfigurationRequestMalformed(ConfigurationError):
    """
    Raised when a configuration request is malformed
    """

    pass


class NoConfigurationFound(Exception):
    pass
