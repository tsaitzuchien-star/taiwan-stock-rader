from __future__ import annotations


def to_float(value) -> float | None:
    """'1,234.50' -> 1234.5；'--'、''、'X' 等無成交符號 -> None。"""
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if not s or s in {"--", "---", "----", "X", "除權息", "除權", "除息"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(value) -> int | None:
    f = to_float(value)
    return int(f) if f is not None else None


def is_common_code(code: str) -> bool:
    """沿用原程式的範圍：4 碼數字代號（普通股與 4 碼 ETF）。"""
    return len(code) == 4 and code.isdigit()


def find_col(fields: list[str], *candidates: str) -> int | None:
    norm = [str(f).replace(" ", "").replace("　", "") for f in fields]
    for cand in candidates:
        for i, f in enumerate(norm):
            if f == cand:
                return i
    for cand in candidates:
        for i, f in enumerate(norm):
            if f.startswith(cand):
                return i
    return None
