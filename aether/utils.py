def _to_float(val, default):
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default
