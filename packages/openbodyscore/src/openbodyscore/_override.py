"""Call-time parameter overrides shared by the parameter modules (params, healthspan_params, hrv, stress)."""
from types import ModuleType, SimpleNamespace


def override(module: ModuleType, changes: dict) -> SimpleNamespace:
    """A copy of the module's UPPER_CASE parameters with `changes` applied; unknown names raise (catches typos)."""
    names = {k: v for k, v in vars(module).items() if k.isupper()}
    unknown = sorted(set(changes) - set(names))
    if unknown:
        raise KeyError(f"unknown parameter(s) for {module.__name__}: {', '.join(unknown)}")
    return SimpleNamespace(**{**names, **changes})
