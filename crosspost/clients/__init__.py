"""Thin API clients. Each takes an injectable requests.Session / clock so tests never hit the network."""


class Deferred(Exception):
    """Not a failure: a platform limit is reached; try again on a later run without counting an attempt."""


class ApiError(RuntimeError):
    pass
