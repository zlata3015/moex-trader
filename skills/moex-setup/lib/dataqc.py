"""Проверка качества дневных свечей: дефекты цены, дубли, пропуски, устаревание."""
from datetime import datetime

MAX_DAILY_JUMP = 0.35   # изменение закрытия за день, после которого нужна ручная проверка (сплит, ошибка цены)
MAX_GAP_DAYS = 12       # разрыв в календарных днях внутри ряда


def check_daily(bars, now, max_age_days=5):
    """{'errors': [...], 'warnings': [...]}. Ошибки блокируют сделку, предупреждения показываются."""
    errors, warnings = [], []
    if not bars:
        return {"errors": ["свечей нет"], "warnings": []}
    dates = [b["t"][:10] for b in bars]
    age = (now - datetime.strptime(dates[-1], "%Y-%m-%d")).days
    if age > max_age_days:
        errors.append("последняя свеча %s, %d дн. назад" % (dates[-1], age))
    if len(set(dates)) != len(dates):
        errors.append("повторяющиеся даты свечей")
    if dates != sorted(dates):
        errors.append("свечи идут не по порядку")
    bad = [b["t"][:10] for b in bars if min(b["o"], b["h"], b["l"], b["c"]) <= 0 or b["h"] < max(b["o"], b["c"], b["l"])
           or b["l"] > min(b["o"], b["c"], b["h"])]
    if bad:
        errors.append("неверные цены OHLC: %s" % ", ".join(bad[-3:]))
    for a, b in zip(bars, bars[1:]):
        if a["c"] > 0 and abs(b["c"] / a["c"] - 1) > MAX_DAILY_JUMP:
            warnings.append("скачок цены %s: проверьте сплит или ошибку данных" % b["t"][:10])
        gap = (datetime.strptime(b["t"][:10], "%Y-%m-%d") - datetime.strptime(a["t"][:10], "%Y-%m-%d")).days
        if gap > MAX_GAP_DAYS:
            warnings.append("разрыв в торгах %s – %s" % (a["t"][:10], b["t"][:10]))
    if bars[-1].get("v") == 0:
        warnings.append("нулевой объём в последней свече")
    return {"errors": errors, "warnings": warnings}
