"""Transactional storage, lossless legacy ingestion, snapshots and audit history."""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .domain import decimal, term_months, validate, parse_date, add_months, today, is_investment
from . import __version__

FIELDS = ("bank", "deposit_type", "amount", "currency", "deposit_date", "maturity_date",
          "interest_rate", "notes", "renewal_option", "term_months", "account_name", "account_number")
HEADERS = ("银行/机构", "资产类型", "金额", "币种", "存入/建仓日期", "到期日期", "利率", "备注", "续期选项", "期限月数", "账户名称", "账号")
ALIASES = dict(zip(HEADERS, FIELDS)) | {"银行/机构": "bank", "类型": "deposit_type", "币种": "currency",
    "银行": "bank", "存款类型": "deposit_type", "货币": "currency", "存款日期": "deposit_date", "建仓日期": "deposit_date",
    "年利率(%)": "interest_rate", "年利率": "interest_rate", "bank_name": "bank",
    "start_date": "deposit_date", "end_date": "maturity_date", "到期日": "maturity_date"}


def timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_records(path: Path) -> list[dict]:
    path = Path(path).resolve()
    suffix = path.suffix.lower()
    if suffix in (".db", ".sqlite", ".sqlite3"):
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("数据库完整性检查失败")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            table = "assets" if "assets" in tables else "deposits" if "deposits" in tables else None
            if table is None:
                raise ValueError("该数据库不包含资产或旧版存款表")
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
    if suffix == ".json":
        content = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(content, dict):
            content = content.get("assets", content.get("deposits"))
        if not isinstance(content, list) or not all(isinstance(a, dict) for a in content):
            raise ValueError("JSON 需要为记录数组；备份说明文件不是资产数据")
        return content
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                raise ValueError("CSV 缺少表头")
            return list(reader)
    raise ValueError("支持 CSV、JSON 和 SQLite 数据库")


def normalize(record: dict, strict=True) -> dict:
    mapped = {ALIASES.get(k.strip(), k.strip()): v for k, v in record.items()}
    mapped["currency"] = mapped.get("currency") or "人民币"
    mapped["renewal_option"] = mapped.get("renewal_option") or "不续期"
    mapped["term_months"] = mapped.get("term_months") or term_months(str(mapped.get("deposit_type", "")))
    if strict:
        result = validate(mapped)
    else:
        # Legacy fields are preserved even if they need correction in the new UI.
        result = {key: mapped.get(key, "") for key in FIELDS}
        result["amount"] = str(decimal(mapped.get("amount", 0), "历史金额"))
        rate = mapped.get("interest_rate")
        result["interest_rate"] = str(decimal(rate)) if rate not in (None, "") else None
        result["term_months"] = int(mapped.get("term_months") or 0)
        for key in FIELDS:
            if key not in ("interest_rate", "term_months"):
                result[key] = str(result[key] or "")
    result["source_id"] = str(mapped.get("source_id") or mapped.get("id") or mapped.get("ID") or "")
    result["raw_json"] = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    result["auto_renew_count"] = int(mapped.get("auto_renew_count") or mapped.get("自动续期次数") or 0)
    result["closed_at"] = mapped.get("closed_at") or (timestamp() if mapped.get("status", mapped.get("状态")) in ("已结清", "已取出", "已关闭") else None)
    result["deleted_at"] = mapped.get("deleted_at") or (timestamp() if mapped.get('status', mapped.get('状态')) == '已删除' else None)
    return result


def fingerprint(asset):
    fields = ("bank", "deposit_type", "amount", "currency", "deposit_date", "maturity_date", "interest_rate", "account_name", "account_number", "notes", "renewal_option")
    data = [str(asset.get(f) or "") for f in fields]
    data[2] = str(decimal(asset["amount"]).normalize())
    if asset.get("interest_rate") is not None:
        data[6] = str(decimal(asset["interest_rate"]).normalize())
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


class Store:
    def __init__(self, base: Path, migrate=True):
        self.base = Path(base).resolve()
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "assets_v4.db"
        self.conn = sqlite3.connect(self.path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=15000")
        self._schema()
        self.migration_messages = []
        if migrate:
            self._migrate()

    def _schema(self):
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > 4:
            raise ValueError("数据库由更新版本创建，请使用对应版本打开")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY, bank TEXT NOT NULL, deposit_type TEXT NOT NULL,
                amount TEXT NOT NULL, currency TEXT NOT NULL, deposit_date TEXT NOT NULL,
                maturity_date TEXT, interest_rate TEXT, notes TEXT, renewal_option TEXT,
                term_months INTEGER NOT NULL DEFAULT 0, account_name TEXT, account_number TEXT,
                auto_renew_count INTEGER NOT NULL DEFAULT 0, source TEXT, source_id TEXT,
                raw_json TEXT, closed_at TEXT, deleted_at TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY, asset_id TEXT, action TEXT NOT NULL,
                before_json TEXT, after_json TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS migrations (source TEXT PRIMARY KEY, digest TEXT, records INTEGER, created_at TEXT);
            CREATE INDEX IF NOT EXISTS assets_maturity ON assets(maturity_date);
        """)
        if version != 4:
            with self.conn:
                self.conn.execute('PRAGMA user_version=4')

    def _migrate(self):
        for name in ("deposits.db", "deposits.json"):
            source = self.base / name
            if not source.exists() or self.conn.execute("SELECT 1 FROM migrations WHERE source=?", (name,)).fetchone():
                continue
            try:
                records = read_records(source)
                data = [normalize(record, strict=False) for record in records]
                with self.conn:
                    # Every legacy row is retained, including identical but independently stored deposits.
                    for asset in data:
                        self._insert(asset, f"旧版 {name}")
                    self.conn.execute("INSERT INTO migrations VALUES(?,?,?,?)", (name, hashlib.sha256(source.read_bytes()).hexdigest(), len(data), timestamp()))
                self.migration_messages.append(f"{name}：已导入 {len(data)} 笔")
            except (ValueError, sqlite3.Error, OSError, TypeError) as error:
                self.migration_messages.append(f"{name}：导入未完成，原文件保留。{error}")
        if self.migration_messages:
            self.backup("migration")

    def close(self):
        self.conn.close()

    def all(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM assets ORDER BY deposit_date DESC, created_at DESC, id")]

    def get(self, asset_id):
        row = self.conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            raise ValueError("该记录已不存在，请刷新")
        return dict(row)

    def _history(self, asset_id, action, before=None, after=None):
        self.conn.execute("INSERT INTO history(asset_id,action,before_json,after_json,created_at) VALUES(?,?,?,?,?)",
                          (asset_id, action, json.dumps(before, ensure_ascii=False) if before else None,
                           json.dumps(after, ensure_ascii=False) if after else None, timestamp()))

    def _insert(self, values, source="手动录入"):
        now = timestamp()
        asset = dict(values)
        asset.update(id="A" + uuid.uuid4().hex[:12].upper(), source=source, created_at=now, updated_at=now)
        allowed = set(FIELDS) | {"id", "source", "source_id", "raw_json", "auto_renew_count", "closed_at", "deleted_at", "created_at", "updated_at"}
        asset = {key: value for key, value in asset.items() if key in allowed}
        columns = list(asset)
        self.conn.execute(f"INSERT INTO assets ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(asset.values()))
        self._history(asset["id"], "导入" if source != "手动录入" else "新增", after=asset)
        return asset["id"]

    def save(self, values, asset_id=None, expected_updated_at=None):
        data = validate(values)
        with self.conn:
            if asset_id is None:
                return self._insert(data)
            before = self.get(asset_id)
            if expected_updated_at and before["updated_at"] != expected_updated_at:
                raise ValueError("记录已在其他窗口更新，请关闭表单后重新打开")
            if before.get("deleted_at") or before.get("closed_at"):
                raise ValueError("请先恢复或重新打开这笔记录后再编辑")
            data["updated_at"] = datetime.now().astimezone().isoformat(timespec="microseconds")
            columns = list(data)
            self.conn.execute(f"UPDATE assets SET {','.join(key+'=?' for key in columns)} WHERE id=?", tuple(data.values()) + (asset_id,))
            self._history(asset_id, "编辑", before, self.get(asset_id))
        return asset_id

    def change_state(self, asset_id, action):
        fields = {"删除": ("deleted_at", timestamp()), "恢复": ("deleted_at", None),
                  "结清": ("closed_at", timestamp()), "重新打开": ("closed_at", None)}
        if action not in fields:
            raise ValueError("未知操作")
        key, value = fields[action]
        with self.conn:
            before = self.get(asset_id)
            self.conn.execute(f"UPDATE assets SET {key}=?,updated_at=? WHERE id=?", (value, timestamp(), asset_id))
            self._history(asset_id, action, before, self.get(asset_id))

    def renew(self, asset_id, rate, start=None, months=None, amount=None):
        before = self.get(asset_id)
        if is_investment(before):
            raise ValueError('投资持仓没有到期日，不能登记续期')
        if before.get("closed_at") or before.get("deleted_at"):
            raise ValueError("已结清或已删除的记录不能续期")
        if not before.get("maturity_date") or parse_date(before["maturity_date"]) > today():
            raise ValueError("仅可对已到期记录登记续期")
        months = int(months or before["term_months"])
        if months <= 0:
            raise ValueError("请填写新的期限月数")
        start = start or before["maturity_date"]
        data = dict(before, deposit_date=start, maturity_date=add_months(parse_date(start), months).isoformat(),
                    term_months=months, deposit_type=f"定期{months}个月", interest_rate=rate,
                    amount=amount if amount is not None else before["amount"])
        data = validate(data)
        if parse_date(data["maturity_date"]) <= today():
            raise ValueError("新到期日仍已过去，请填写银行确认的本期存入日期及期限")
        with self.conn:
            data.update(auto_renew_count=before["auto_renew_count"] + 1, updated_at=timestamp())
            self.conn.execute(f"UPDATE assets SET {','.join(key+'=?' for key in data)} WHERE id=?", tuple(data.values()) + (asset_id,))
            self._history(asset_id, "续期", before, self.get(asset_id))

    def setting(self, key, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.conn:
            self.conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

    def history(self, asset_id=None):
        query = "SELECT * FROM history"
        params = ()
        if asset_id:
            query += " WHERE asset_id=?"
            params = (asset_id,)
        return [dict(row) for row in self.conn.execute(query + " ORDER BY id DESC LIMIT 1000", params)]

    def preview_import(self, path):
        records = read_records(Path(path))
        seen = {fingerprint(a) for a in self.all()}
        result = {"valid": [], "duplicates": [], "errors": [], "source": str(Path(path).resolve())}
        for i, record in enumerate(records, 1):
            try:
                asset = normalize(record)
                digest = fingerprint(asset)
                if digest in seen:
                    result["duplicates"].append((i, asset))
                else:
                    result["valid"].append(asset)
                    seen.add(digest)
            except (ValueError, TypeError, OverflowError) as error:
                result["errors"].append(f"第 {i} 行：{error}")
        return result

    def import_preview(self, preview, include_duplicates=False):
        items = preview["valid"] + ([asset for _, asset in preview["duplicates"]] if include_duplicates else [])
        # Revalidate everything before touching the database; invalid rows abort the transaction.
        for item in items:
            validate(item)
        if not items:
            return 0
        self.backup("before_import")
        with self.conn:
            for item in items:
                self._insert(item, "导入 " + Path(preview["source"]).name)
        return len(items)

    def backup(self, reason="manual") -> Path:
        folder = self.base / "backups"
        folder.mkdir(exist_ok=True)
        path = folder / f"assetbook_{datetime.now():%Y%m%d_%H%M%S_%f}_{reason}.db"
        with closing(sqlite3.connect(path)) as target:
            self.conn.backup(target)
        meta = dict(backup_time=timestamp(), version=__version__, record_count=len(self.all()), reason=reason, database=path.name)
        path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def daily_backup(self):
        key = today().isoformat()
        if self.setting("daily_backup_date") != key:
            self.backup("daily")
            self.set_setting("daily_backup_date", key)

    def restore(self, path):
        path = Path(path).resolve()
        if path == self.path:
            raise ValueError("不能用当前数据库恢复自身")
        # Stage and validate before creating a recovery snapshot or changing active data.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
            source.row_factory = sqlite3.Row
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("备份完整性检查失败")
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "assets" in tables:
                version = source.execute("PRAGMA user_version").fetchone()[0]
                if version != 4 or not {"history", "settings", "migrations"} <= tables:
                    raise ValueError("备份版本或结构不兼容")
                staged = sqlite3.connect(":memory:")
                try:
                    source.backup(staged)
                    # Validate all required columns and history schema, including empty backups.
                    staged.execute("SELECT " + ",".join(FIELDS) + ",id,source,source_id,raw_json,auto_renew_count,closed_at,deleted_at,created_at,updated_at FROM assets LIMIT 1")
                    staged.execute("SELECT id,asset_id,action,before_json,after_json,created_at FROM history LIMIT 1")
                    staged.execute("SELECT key,value FROM settings LIMIT 1")
                    staged.execute("SELECT source,digest,records,created_at FROM migrations LIMIT 1")
                    saved_settings = {r[0]: json.loads(r[1]) for r in staged.execute('SELECT key,value FROM settings')}
                    if not 1 <= int(saved_settings.get('reminder_days', 30)) <= 365:
                        raise ValueError('备份中的提醒设置无效')
                    for row in staged.execute("SELECT amount,interest_rate FROM assets"):
                        decimal(row[0])
                        if row[1] is not None:
                            decimal(row[1])
                    safety = self.backup("before_restore")
                    rollback = sqlite3.connect(':memory:')
                    try:
                        self.conn.backup(rollback)
                        try:
                            staged.backup(self.conn)
                        except sqlite3.Error:
                            rollback.backup(self.conn)
                            raise
                    finally:
                        rollback.close()
                finally:
                    staged.close()
            elif "deposits" in tables:
                assets = [normalize(dict(row), strict=False) for row in source.execute("SELECT * FROM deposits")]
                safety = self.backup("before_restore")
                with self.conn:
                    self.conn.execute("DELETE FROM assets")
                    for a in assets:
                        self._insert(a, "恢复旧版 " + path.name)
            else:
                raise ValueError("该文件不是资产账本备份")
        with self.conn:
            self._history(None, "恢复备份", after={"file": path.name, "safety_backup": safety.name})
        return safety

    def export_csv(self, path, assets):
        with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("ID",) + HEADERS + ("状态", "自动续期次数", "创建时间", "更新时间", "数据来源", "原编号"))
            from .domain import status
            for a in assets:
                values = (a["id"],) + tuple(a.get(k, "") for k in FIELDS) + (status(a), a["auto_renew_count"], a["created_at"], a["updated_at"], a["source"], a.get("source_id", ""))
                # Protect spreadsheet readers from formula execution; normal records are unchanged.
                writer.writerow(["'"+v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in values])

    def export_json(self, path, assets):
        Path(path).write_text(json.dumps({"version": 4, "exported_at": timestamp(), "assets": assets}, ensure_ascii=False, indent=2), encoding="utf-8")
