"""Pure business rules; no GUI, database or network dependencies."""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

BANKS = ["中国工商银行", "中国建设银行", "中国农业银行", "中国银行", "招商银行",
         "交通银行", "邮储银行", "浦发银行", "中信银行", "民生银行", "兴业银行", "支付宝", "微信支付"]
ALIASES = {"工行": "中国工商银行", "建行": "中国建设银行", "农行": "中国农业银行",
           "中行": "中国银行", "招行": "招商银行", "交行": "交通银行", "邮储": "邮储银行"}
INVESTMENT_TYPES = ["股票", "基金", "ETF", "其他投资"]
TYPES = ["活期", "定期1个月", "定期3个月", "定期6个月", "定期1年", "定期2年", "定期3年", "定期5年", *INVESTMENT_TYPES, "其他"]
CURRENCIES = ["人民币", "美元", "欧元", "日元", "港元", "英镑", "其他"]
RENEWALS = ["不续期", "自动续期", "到期转活期", "到期通知"]
SYMBOLS = {"人民币": "¥", "美元": "US$", "欧元": "€", "日元": "JP¥", "港元": "HK$", "英镑": "£"}
CENT = Decimal("0.01")


def is_investment(asset_or_type) -> bool:
    kind = asset_or_type.get('deposit_type', '') if isinstance(asset_or_type, dict) else str(asset_or_type)
    return any(word in kind.upper() for word in ('股票', '基金', 'ETF', '投资'))


def has_maturity(asset) -> bool:
    return not is_investment(asset) and asset.get('deposit_type') != '活期' and bool(asset.get('maturity_date'))


def today() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


def decimal(value, label="数值") -> Decimal:
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}必须是有效数字") from None
    if not result.is_finite():
        raise ValueError(f"{label}必须是有限数字")
    return result


def money(value, currency="人民币") -> str:
    return f"{SYMBOLS.get(currency, currency + ' ')}{decimal(value):,.2f}"


def parse_date(value, label="日期") -> date:
    value = str(value).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label}请使用 YYYY-MM-DD 格式")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label}不是有效日期") from None


def add_months(start: date, months: int) -> date:
    index = start.year * 12 + start.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def term_months(kind: str) -> int:
    found = re.search(r"(\d+)\s*(个月|月|年)", kind)
    return int(found[1]) * (12 if found[2] == "年" else 1) if found else 0


def validate(values: dict) -> dict:
    result = {key: str(values.get(key, "") or "").strip() for key in
              ("bank", "deposit_type", "currency", "deposit_date", "maturity_date", "notes",
               "renewal_option", "account_name", "account_number")}
    if not result["bank"]:
        raise ValueError("请填写银行或机构")
    if not result["deposit_type"]:
        raise ValueError("请填写资产类型")
    result["currency"] = result["currency"] or "人民币"
    investment = is_investment(result)
    if investment:
        # A type change or imported spreadsheet may contain stale deposit-only values.
        # Discard them before parsing: they have no meaning for an open-ended holding.
        result['maturity_date'] = ''
        result['renewal_option'] = '不适用'
    result["renewal_option"] = result["renewal_option"] or "不续期"
    if not investment and result["renewal_option"] not in RENEWALS:
        raise ValueError("请选择有效的续期方式")
    amount = decimal(values.get("amount", ""), "金额")
    if amount <= 0 or amount > Decimal("999999999999999"):
        raise ValueError("金额必须大于零且不超过 999,999,999,999,999")
    if amount != amount.quantize(CENT, rounding=ROUND_HALF_UP):
        raise ValueError("金额最多保留两位小数")
    result["amount"] = str(amount.quantize(CENT))
    rate = values.get("interest_rate", "")
    if investment or rate is None or str(rate).strip() == "":
        result["interest_rate"] = None
    else:
        rate = decimal(rate, "年利率")
        if not 0 <= rate <= 100:
            raise ValueError("年利率应在 0–100% 之间")
        result["interest_rate"] = str(rate)
    start = parse_date(result["deposit_date"], "建仓日期" if investment else "存入日期")
    months = 0 if investment else values.get("term_months", term_months(result["deposit_type"]))
    try:
        months = int(months or 0)
    except (ValueError, TypeError):
        raise ValueError("期限月数必须是整数") from None
    if not 0 <= months <= 1200:
        raise ValueError("期限应在 0–1200 个月之间")
    result["term_months"] = months
    if result["deposit_type"] == "活期":
        result["maturity_date"] = ""
        result["term_months"] = 0
    elif not result["maturity_date"] and months:
        try:
            result["maturity_date"] = add_months(start, months).isoformat()
        except (ValueError, OverflowError):
            raise ValueError("到期日期超出支持范围") from None
    if result["maturity_date"] and parse_date(result["maturity_date"], "到期日期") <= start:
        raise ValueError("到期日期必须晚于存入日期")
    if "定期" in result["deposit_type"] and not result["maturity_date"]:
        raise ValueError("定期资产需要期限或到期日期")
    if len(result["notes"]) > 10000:
        raise ValueError("备注不能超过 10000 字")
    return result


def status(asset: dict, on: date | None = None, reminder_days=30) -> str:
    on = on or today()
    if asset.get("deleted_at"):
        return "已删除"
    if asset.get("closed_at"):
        return "已结清"
    try:
        if parse_date(asset["deposit_date"]) > on:
            return "未开始"
        if is_investment(asset):
            return '持有中'
        if not asset.get("maturity_date"):
            return "活期" if asset["deposit_type"] == "活期" else "存续中"
        days = (parse_date(asset["maturity_date"]) - on).days
    except (ValueError, KeyError):
        return "待核对"
    if days <= 0:
        return "已到期"
    return "即将到期" if days <= reminder_days else "存续中"


def active(asset: dict, on=None) -> bool:
    return status(asset, on) not in ("已删除", "已结清", "未开始", "待核对")


def estimated_interest(asset: dict) -> Decimal | None:
    """Simple interest; exact calendar terms use months/12, other dates use ACT/365."""
    if is_investment(asset) or asset.get("interest_rate") is None or not has_maturity(asset):
        return None
    try:
        start, end = parse_date(asset["deposit_date"]), parse_date(asset["maturity_date"])
        months = int(asset.get("term_months", 0))
        years = Decimal(months) / 12 if months and add_months(start, months) == end else Decimal((end-start).days) / 365
        if years <= 0:
            return None
        return (decimal(asset["amount"]) * decimal(asset["interest_rate"]) / 100 * years).quantize(CENT, rounding=ROUND_HALF_UP)
    except (ValueError, TypeError, OverflowError):
        return None


def totals(assets: list[dict], on=None) -> dict[str, Decimal]:
    result = defaultdict(Decimal)
    for asset in assets:
        if active(asset, on):
            result[asset["currency"]] += decimal(asset["amount"])
    return dict(result)


def distribution(assets, currency, field="bank", on=None):
    grouped = defaultdict(Decimal)
    for asset in assets:
        if asset["currency"] == currency and active(asset, on):
            grouped[str(asset.get(field) or "其他")] += decimal(asset["amount"])
    return sorted(grouped.items(), key=lambda item: item[1], reverse=True)


def maturity_schedule(assets, currency, on=None, months=12):
    on = on or today()
    first = on.replace(day=1)
    buckets = {add_months(first, offset).strftime("%Y-%m"): Decimal(0) for offset in range(months)}
    for asset in assets:
        if active(asset, on) and asset["currency"] == currency and has_maturity(asset) and asset['maturity_date'] >= on.isoformat():
            key = asset["maturity_date"][:7]
            if key in buckets:
                buckets[key] += decimal(asset["amount"])
    return list(buckets.items())


def insights(assets: list[dict], reminder_days=30, on=None) -> list[dict]:
    on = on or today()
    result = []
    visible = [a for a in assets if not a.get("deleted_at") and not a.get("closed_at")]
    due = [a for a in visible if status(a, on, reminder_days) == "已到期"]
    soon = [a for a in visible if status(a, on, reminder_days) == "即将到期"]
    if due:
        result.append(dict(level="待处理", title=f"{len(due)} 笔资产已到期", detail="请核对银行的实际处理结果，再登记续期或结清。", ids=[a["id"] for a in due]))
    if soon:
        result.append(dict(level="提醒", title=f"未来 {reminder_days} 天有 {len(soon)} 笔资产到期", detail="到期日历列出了各笔本金与预计利息。", ids=[a["id"] for a in soon]))
    for currency, total in totals(visible, on).items():
        banks = distribution(visible, currency, on=on)
        if banks and len(banks) > 1 and banks[0][1] / total >= Decimal("0.8"):
            result.append(dict(level="观察", title=f"{currency}资产中 {banks[0][1] / total:.0%} 在{banks[0][0]}", detail="这是同币种机构分布的统计结果，可在资产分析中查看。", ids=[]))
    missing = [a for a in visible if has_maturity(a) and a.get("interest_rate") is None]
    if missing:
        result.append(dict(level="核对", title=f"{len(missing)} 笔定期记录缺少年利率", detail="补全利率后可估算到期利息。", ids=[a["id"] for a in missing]))
    seen, repeated = {}, []
    for a in visible:
        key = tuple(str(a.get(f, "")) for f in ("bank", "amount", "currency", "deposit_date", "maturity_date", "account_number", "deposit_type"))
        if is_investment(a):
            key += (str(a.get('notes') or ''),)
        if key in seen:
            repeated.extend([seen[key], a["id"]])
        seen[key] = a["id"]
    if repeated:
        result.append(dict(level="核对", title="发现可能重复的资产记录", detail="机构、金额、日期和账户相同；请确认是否为多笔独立存款。", ids=list(dict.fromkeys(repeated))))
    invalid = [a for a in visible if status(a, on) == "待核对"]
    if invalid:
        result.append(dict(level="核对", title=f"{len(invalid)} 笔历史记录日期需核对", detail="原始记录已保留，可在资产明细中修正。", ids=[a["id"] for a in invalid]))
    testing = [a for a in visible if '测试' in a.get('bank', '') or a.get('bank', '').lower() == 'test']
    if testing:
        result.append(dict(level="核对", title=f"{len(testing)} 笔记录的机构名称疑似测试数据", detail="这些记录已完整保留并计入统计，请核对后决定是否移入回收站。", ids=[a['id'] for a in testing]))
    if not result:
        result.append(dict(level="正常", title="目前没有需要处理的提醒", detail="新增记录后会自动检查日期、利率和重复数据。", ids=[]))
    return result


def search(assets, query="", currency="全部币种", state="全部状态", bank="全部机构", reminder_days=30, on=None):
    on = on or today()
    text = query.strip()
    days_match = re.search(r"(?:未来|近)(\d+)天(?:内)?(?:到期)?", text)
    minimum = re.search(r"(?:大于|超过|>)\s*(\d+(?:\.\d+)?)(万|元)?", text)
    remainder = text
    if days_match:
        remainder = remainder.replace(days_match[0], "")
    if minimum:
        remainder = remainder.replace(minimum[0], "")
    due_only = "已到期" in remainder
    remainder = remainder.replace("已到期", "")
    this_month = "本月到期" in remainder
    remainder = remainder.replace("本月到期", "")
    for alias, canonical in ALIASES.items():
        remainder = remainder.replace(alias, canonical)
    tokens = remainder.lower().split()
    filtered = []
    for a in assets:
        s = status(a, on, reminder_days)
        if state == "已删除":
            if s != "已删除":
                continue
        elif a.get("deleted_at"):
            continue
        if state not in ("全部状态", "已删除") and s != state:
            continue
        if currency != "全部币种" and a["currency"] != currency:
            continue
        if bank != "全部机构" and a["bank"] != bank:
            continue
        corpus = " ".join(str(a.get(k, "") or "") for k in
                          ("id", "bank", "deposit_type", "notes", "account_name", "account_number", "currency", "source_id")).lower()
        if not all(token in corpus for token in tokens):
            continue
        if minimum and decimal(a["amount"]) <= Decimal(minimum[1]) * (10000 if minimum[2] == "万" else 1):
            continue
        if due_only and s != "已到期":
            continue
        if this_month and (not active(a, on) or not has_maturity(a) or not a['maturity_date'].startswith(on.strftime("%Y-%m"))):
            continue
        if days_match:
            if not has_maturity(a):
                continue
            try:
                delta = (parse_date(a.get("maturity_date", "")) - on).days
            except ValueError:
                continue
            if not active(a, on) or not 0 <= delta <= int(days_match[1]):
                continue
        filtered.append(a)
    return filtered


def parse_quick_entry(text: str, on=None) -> dict:
    """Conservative Chinese rule-based extraction; the GUI always previews before save."""
    on = on or today()
    result = {"currency": "人民币", "deposit_date": on.isoformat(), "renewal_option": "不续期"}
    for bank in sorted(BANKS + list(ALIASES), key=len, reverse=True):
        if bank in text:
            result["bank"] = ALIASES.get(bank, bank)
            break
    if not result.get('bank'):
        broker = re.search(r'([\u4e00-\u9fffA-Za-z]{2,16}(?:证券|券商|交易平台))', text)
        if broker:
            result['bank'] = broker[1]
    for kind in ('ETF', '股票', '基金', '其他投资'):
        if kind in text.upper():
            result['deposit_type'] = kind
            break
    for currency in CURRENCIES[:-1]:
        if currency in text:
            result["currency"] = currency
    amount = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(万元|万|元)(?![\d])", text)
    if amount:
        result["amount"] = str(decimal(amount[1]) * (10000 if "万" in amount[2] else 1))
    if is_investment(result):
        dates = re.findall(r'\d{4}-\d{2}-\d{2}', text)
        if dates:
            result['deposit_date'] = dates[0]
        result.update(term_months=0, maturity_date='', interest_rate=None, renewal_option='不适用', notes=text.strip())
        return result
    rate = re.search(r"(?:年利率|利率)?\s*(\d+(?:\.\d+)?)\s*[%％]", text)
    if rate:
        result["interest_rate"] = rate[1]
    chinese_numbers = {"一": 1, "二": 2, "两": 2, "三": 3, "五": 5, "六": 6}
    term = re.search(r"([一二两三五六]|\d+)\s*(个月|月|年)(?![\d-])", re.sub(r"\d{4}-\d{2}-\d{2}", "", text))
    if term:
        count = chinese_numbers.get(term[1], int(term[1]) if term[1].isdigit() else 0)
        result["term_months"] = count * (12 if term[2] == "年" else 1)
        result["deposit_type"] = f"定期{count}{'年' if term[2] == '年' else '个月'}"
    elif "活期" in text:
        result["deposit_type"] = "活期"
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    if dates:
        result["deposit_date"] = dates[0]
    if len(dates) > 1:
        result["maturity_date"] = dates[1]
    for renewal in RENEWALS:
        if renewal in text:
            result["renewal_option"] = renewal
    if result.get("term_months") and not result.get("maturity_date"):
        result["maturity_date"] = add_months(parse_date(result["deposit_date"]), result["term_months"]).isoformat()
    return result
