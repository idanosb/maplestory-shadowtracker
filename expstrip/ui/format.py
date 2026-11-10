import math

EM_DASH = "—"


def _sig3(x: float) -> str:
    """Format x (>= 1) with up to 3 significant figures, e.g. 48.6, 129, 4.2, 1.23."""
    if x >= 100:
        decimals = 0
    elif x >= 10:
        decimals = 1
    else:
        decimals = 2
    text = f"{x:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_k(n: float | None, signed: bool = False) -> str:
    """Compact EXP number: integers below 1000, otherwise K/M with 3 significant figures."""
    if n is None:
        return EM_DASH

    magnitude = abs(n)
    if round(magnitude) < 1000:
        whole = round(n)
        if whole == 0:
            return "+0" if signed else "0"
        body = str(abs(whole))
        negative = whole < 0
    else:
        thousands = magnitude / 1_000
        if magnitude < 1_000_000 and float(_sig3(thousands)) < 1000:
            body = _sig3(thousands) + "K"
        else:
            body = _sig3(magnitude / 1_000_000) + "M"
        negative = n < 0

    if negative:
        return "-" + body
    if signed:
        return "+" + body
    return body


def format_duration(seconds: float | None) -> str:
    """Compact duration: '<1m', '42m', '4h 21m', capped at '99h+'."""
    if seconds is None:
        return EM_DASH

    total = max(0, math.floor(seconds))
    if total >= 99 * 3600:
        return "99h+"
    if total < 60:
        return "<1m"
    if total < 3600:
        return f"{total // 60}m"
    hours, rest = divmod(total, 3600)
    return f"{hours}h {rest // 60}m"
