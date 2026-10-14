import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from assetbook.domain import (validate, add_months, status, totals, search, parse_quick_entry,
                              estimated_interest, maturity_schedule, insights, INVESTMENT_TYPES)
from assetbook.storage import Store, fingerprint, read_records
from assetbook.export import export_xlsx, export_svg

TEST_TMP = Path(__file__).resolve().parent / '.tmp'
TEST_TMP.mkdir(exist_ok=True)


def sample(**kwargs):
    return dict(bank="中国工商银行", deposit_type="定期3年", amount="50000.00", currency="人民币",
                deposit_date="2023-11-10", maturity_date="2026-11-10", interest_rate="2.1",
                term_months=36, renewal_option="自动续期", notes="", account_name="", account_number="") | kwargs


class DomainTests(unittest.TestCase):
    def test_calendar_months_handle_leap_year_and_month_end(self):
        self.assertEqual(add_months(date(2024, 2, 29), 12), date(2025, 2, 28))
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))

    def test_exact_decimal_total_is_separate_per_currency(self):
        data = [sample(amount="0.10"), sample(amount="0.20"), sample(amount="100", currency="美元")]
        self.assertEqual(totals(data, date(2026, 10, 10)), {"人民币": Decimal("0.30"), "美元": Decimal(100)})

    def test_reject_nonfinite_negative_precision_rate_and_date_order(self):
        for changes in [dict(amount="NaN"), dict(amount="Infinity"), dict(amount="-1"), dict(amount="1.001"),
                        dict(interest_rate="NaN"), dict(interest_rate="-1"), dict(interest_rate="101"),
                        dict(maturity_date="2023-01-01"), dict(deposit_date="2026-02-30"),
                        dict(deposit_date="2026-2-01"), dict(term_months=1201)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(sample(**changes))

    def test_compute_date_and_clear_current_account_maturity(self):
        result = validate(sample(maturity_date=""))
        self.assertEqual(result["maturity_date"], "2026-11-10")
        result = validate(sample(deposit_type="活期"))
        self.assertEqual(result["maturity_date"], "")
        self.assertEqual(result["term_months"], 0)

    def test_state_boundaries_do_not_mutate_record(self):
        asset = sample()
        before = json.dumps(asset)
        self.assertEqual(status(asset, date(2026, 10, 10)), "存续中")
        self.assertEqual(status(asset, date(2026, 10, 11)), "即将到期")
        self.assertEqual(status(asset, date(2026, 11, 10)), "已到期")
        self.assertEqual(json.dumps(asset), before)

    def test_closed_deleted_future_excluded_from_totals(self):
        assets = [sample(), sample(closed_at="x"), sample(deleted_at="x"), sample(deposit_date="2028-01-01")]
        self.assertEqual(totals(assets, date(2026, 10, 10)), {"人民币": Decimal(50000)})

    def test_interest_calendar_years_and_actual_days(self):
        self.assertEqual(estimated_interest(sample()), Decimal("3150.00"))
        self.assertEqual(estimated_interest(sample(deposit_date="2026-01-01", maturity_date="2026-01-11", term_months=0, amount="36500", interest_rate="1")), Decimal("10.00"))
        self.assertIsNone(estimated_interest(sample(interest_rate=None)))
        self.assertEqual(estimated_interest(sample(interest_rate="0")), Decimal("0.00"))

    def test_natural_search_combines_filters(self):
        on = date(2026, 10, 10)
        records = [sample(id="1", amount="60000", maturity_date="2026-10-20"), sample(id="2", amount="30000", maturity_date="2026-12-20")]
        self.assertEqual([a['id'] for a in search(records, "工行 未来30天到期 超过5万", on=on)], ['1'])
        self.assertEqual([a['id'] for a in search(records, "本月到期", on=on)], ['1'])
        self.assertEqual(search(records, "已到期", on=on), [])

    def test_quick_entry_extracts_values_and_never_guess_amount(self):
        result = parse_quick_entry("工行 5万元 三年 利率2.1% 2026-10-10 自动续期", date(2026, 10, 10))
        self.assertEqual(result['amount'], '50000')
        self.assertEqual(result['term_months'], 36)
        self.assertEqual(result['maturity_date'], '2029-10-10')
        self.assertEqual(result['bank'], '中国工商银行')
        self.assertEqual(result['interest_rate'], '2.1')
        self.assertNotIn('amount', parse_quick_entry("工行 三年 利率2.1%"))

    def test_maturity_schedule_excludes_expired_and_closed(self):
        data = [sample(amount='100', maturity_date='2026-10-20'), sample(amount='200', maturity_date='2026-09-01'), sample(closed_at='x')]
        schedule = maturity_schedule(data, '人民币', date(2026, 10, 10))
        self.assertEqual(len(schedule), 12)
        self.assertEqual(schedule[0], ('2026-10', Decimal(100)))

    def test_duplicate_insights(self):
        result = insights([sample(id='1'), sample(id='2')], on=date(2026, 10, 10))
        self.assertTrue(any('重复' in item['title'] for item in result))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.base = Path(self.temp.name)
        self.store = Store(self.base, migrate=False)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_crud_soft_delete_restore_and_audit(self):
        asset_id = self.store.save(sample())
        original = self.store.get(asset_id)
        self.store.save(sample(amount='12345.67'), asset_id, original['updated_at'])
        self.store.change_state(asset_id, '删除')
        self.assertEqual(status(self.store.get(asset_id)), '已删除')
        self.store.change_state(asset_id, '恢复')
        self.assertIsNone(self.store.get(asset_id)['deleted_at'])
        self.assertEqual([r['action'] for r in self.store.history(asset_id)], ['恢复', '删除', '编辑', '新增'])
        previous = json.loads(self.store.history(asset_id)[2]['before_json'])
        self.assertEqual(previous['amount'], '50000.00')

    def test_optimistic_concurrency(self):
        asset_id = self.store.save(sample())
        original = self.store.get(asset_id)
        self.store.save(sample(amount='100'), asset_id, original['updated_at'])
        with self.assertRaises(ValueError):
            self.store.save(sample(amount='200'), asset_id, original['updated_at'])
        self.assertEqual(self.store.get(asset_id)['amount'], '100.00')

    def test_renew_is_explicit_and_has_before_after(self):
        asset_id = self.store.save(sample(deposit_date='2022-01-01', maturity_date='2025-01-01'))
        with patch('assetbook.storage.today', return_value=date(2026, 10, 10)):
            self.store.renew(asset_id, '1.8', start='2026-10-10', months=36, amount='52000')
        current = self.store.get(asset_id)
        self.assertEqual(current['maturity_date'], '2029-10-10')
        self.assertEqual(current['auto_renew_count'], 1)
        self.assertEqual(current['amount'], '52000.00')
        self.assertEqual(json.loads(self.store.history(asset_id)[0]['before_json'])['maturity_date'], '2025-01-01')

    def test_reject_renew_before_maturity_and_invalid_result(self):
        asset_id = self.store.save(sample())
        with patch('assetbook.storage.today', return_value=date(2026, 10, 10)), self.assertRaises(ValueError):
            self.store.renew(asset_id, '2')
        asset_id = self.store.save(sample(deposit_date='2022-01-01', maturity_date='2025-01-01'))
        with patch('assetbook.storage.today', return_value=date(2026, 10, 10)), self.assertRaises(ValueError):
            self.store.renew(asset_id, '2', start='2025-01-01', months=1)
        self.assertEqual(self.store.get(asset_id)['auto_renew_count'], 0)

    def test_backup_restore_and_preserve_old_snapshots(self):
        folder = self.base / 'backups'
        folder.mkdir()
        old = folder / 'deposit_backup_existing.db'
        old.write_bytes(b'existing bytes')
        asset_id = self.store.save(sample())
        backup = self.store.backup()
        self.store.save(sample(amount='12.34'), asset_id)
        safety = self.store.restore(backup)
        self.assertEqual(self.store.get(asset_id)['amount'], '50000.00')
        self.assertEqual(read_records(safety)[0]['amount'], '12.34')
        self.assertEqual(old.read_bytes(), b'existing bytes')

    def test_invalid_restore_leaves_current_data(self):
        self.store.save(sample())
        invalid = self.base / 'invalid.db'
        with closing(sqlite3.connect(invalid)) as conn:
            conn.execute('CREATE TABLE junk(id INTEGER)')
        with self.assertRaises(ValueError):
            self.store.restore(invalid)
        self.assertEqual(len(self.store.all()), 1)

    def test_malformed_v4_restore_leaves_data(self):
        self.store.save(sample())
        malformed = self.base / 'malformed.db'
        with closing(sqlite3.connect(malformed)) as conn:
            conn.executescript('CREATE TABLE assets(id TEXT); CREATE TABLE history(id INTEGER); CREATE TABLE settings(key TEXT); CREATE TABLE migrations(source TEXT); PRAGMA user_version=4;')
        with self.assertRaises(sqlite3.Error):
            self.store.restore(malformed)
        self.assertEqual(len(self.store.all()), 1)

    def test_import_preview_errors_duplicates_and_atomic_write(self):
        self.store.save(sample())
        source = self.base / 'data.json'
        source.write_text(json.dumps([sample(), sample(amount='100'), sample(amount='NaN')]), encoding='utf-8')
        preview = self.store.preview_import(source)
        self.assertEqual([len(preview[k]) for k in ('valid', 'duplicates', 'errors')], [1, 1, 1])
        self.assertEqual(self.store.import_preview(preview), 1)
        self.assertEqual(len(self.store.all()), 2)
        broken = dict(preview, valid=[sample(amount='200'), sample(amount='NaN')])
        with self.assertRaises(ValueError):
            self.store.import_preview(broken)
        self.assertEqual(len(self.store.all()), 2)

    def test_csv_and_json_roundtrip_preserve_accounts_zero_rates(self):
        self.store.save(sample(account_number='001234', interest_rate='0', notes='真实备注'))
        for suffix, exporter in [('.csv', self.store.export_csv), ('.json', self.store.export_json)]:
            path = self.base / ('export' + suffix)
            exporter(path, self.store.all())
            record = self.store.preview_import(path)['duplicates'][0][1]
            self.assertEqual(record['account_number'], '001234')
            self.assertEqual(record['interest_rate'], '0')

    def test_xlsx_is_valid_zip_xml_and_preserves_account_as_text(self):
        self.store.save(sample(account_number='001234', notes='=1+1'))
        path = self.base / 'export.xlsx'
        export_xlsx(path, self.store.all())
        with ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            for file in archive.namelist():
                ET.fromstring(archive.read(file))
            xml = archive.read('xl/worksheets/sheet1.xml').decode()
            self.assertIn('001234', xml)
            self.assertNotIn('<f>', xml)
            self.assertIn('r="M2" t="inlineStr"', xml)

    def test_svg_exports_full_distribution(self):
        path = self.base / 'chart.svg'
        data = [(f'bank{i}', Decimal(i+1)) for i in range(10)]
        export_svg(path, data, '机构分布', '人民币')
        ET.parse(path)
        self.assertIn('bank9', path.read_text(encoding='utf-8'))

    def test_daily_backup_once(self):
        self.store.daily_backup()
        self.store.daily_backup()
        self.assertEqual(len(list((self.base/'backups').glob('*.db'))), 1)

    def test_reopening_current_schema_does_not_rewrite_database(self):
        self.store.save(sample())
        self.store.close()
        before = (self.base/'assets_v4.db').read_bytes()
        self.store = Store(self.base, migrate=False)
        self.store.all()
        self.store.close()
        self.assertEqual((self.base/'assets_v4.db').read_bytes(), before)
        self.store = Store(self.base, migrate=False)

    def test_sql_failure_rolls_back_entire_import_and_history(self):
        source = self.base/'batch.json'
        source.write_text(json.dumps([sample(amount='100'), sample(amount='200')]), encoding='utf-8')
        preview = self.store.preview_import(source)
        insert = self.store._insert
        calls = 0
        def failing_insert(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite3.OperationalError('simulated write failure')
            return insert(*args)
        with patch.object(self.store, '_insert', side_effect=failing_insert), self.assertRaises(sqlite3.OperationalError):
            self.store.import_preview(preview)
        self.assertEqual(self.store.all(), [])
        self.assertEqual(self.store.history(), [])

    def test_restore_rejects_invalid_settings_before_write(self):
        asset_id = self.store.save(sample())
        backup = self.store.backup()
        with closing(sqlite3.connect(backup)) as conn:
            conn.execute('INSERT INTO settings VALUES(?,?)', ('reminder_days', '999'))
            conn.commit()
        self.store.save(sample(amount='100'), asset_id)
        with self.assertRaises(ValueError):
            self.store.restore(backup)
        self.assertEqual(self.store.get(asset_id)['amount'], '100.00')

    def test_csv_deleted_records_do_not_become_active_after_import(self):
        asset_id = self.store.save(sample())
        self.store.change_state(asset_id, '删除')
        path = self.base/'deleted.csv'
        self.store.export_csv(path, self.store.all())
        preview = self.store.preview_import(path)
        self.assertIsNotNone(preview['duplicates'][0][1]['deleted_at'])


class InvestmentTests(unittest.TestCase):
    def test_all_investment_types_save_without_deposit_fields(self):
        for kind in INVESTMENT_TYPES + ['指数基金', '港股股票']:
            with self.subTest(kind=kind):
                result = validate(dict(bank='中信证券', deposit_type=kind, amount='12345.67', deposit_date='2026-01-01'))
                self.assertEqual(result['maturity_date'], '')
                self.assertEqual(result['term_months'], 0)
                self.assertIsNone(result['interest_rate'])
                self.assertEqual(result['renewal_option'], '不适用')
                self.assertEqual(status(result, date(2026, 10, 10)), '持有中')

    def test_stale_deposit_fields_are_cleared_before_validation(self):
        result = validate(sample(deposit_type='股票', maturity_date='invalid', interest_rate='NaN', term_months='invalid', renewal_option='stale'))
        self.assertEqual(result['maturity_date'], '')
        self.assertEqual(result['term_months'], 0)
        self.assertIsNone(result['interest_rate'])

    def test_investment_with_old_dates_is_not_due_or_interest_bearing(self):
        asset = sample(id='stock', deposit_type='股票', maturity_date='2026-10-20')
        on = date(2026, 10, 10)
        self.assertEqual(status(asset, on), '持有中')
        self.assertIsNone(estimated_interest(asset))
        self.assertEqual(sum(v for _, v in maturity_schedule([asset], '人民币', on)), 0)
        self.assertEqual(search([asset], '未来30天到期', on=on), [])
        self.assertEqual(search([asset], '本月到期', on=on), [])
        self.assertFalse(any('利率' in n['title'] or '到期' in n['title'] for n in insights([asset], on=on)))

    def test_mixed_asset_total_and_holding_filter(self):
        assets = [sample(id='deposit'), sample(id='stock', deposit_type='股票', amount='12345.67'),
                  sample(id='fund', deposit_type='基金', amount='100.12'), sample(id='closed', deposit_type='ETF', closed_at='x')]
        on = date(2026, 10, 10)
        self.assertEqual(totals(assets, on)['人民币'], Decimal('62445.79'))
        self.assertEqual([a['id'] for a in search(assets, state='持有中', on=on)], ['stock', 'fund'])

    def test_quick_investment_entry_preserves_name_in_notes(self):
        result = parse_quick_entry('中信证券 股票 市值3万元 2026-10-10 贵州茅台 600519')
        self.assertEqual(result['bank'], '中信证券')
        self.assertEqual(result['deposit_type'], '股票')
        self.assertEqual(result['amount'], '30000')
        self.assertEqual(result['maturity_date'], '')
        self.assertIn('600519', result['notes'])
        result = parse_quick_entry('招商银行 基金 2万元')
        self.assertEqual(result['deposit_type'], '基金')
        self.assertEqual(result['bank'], '招商银行')

    def test_distinct_holdings_not_reported_as_duplicate(self):
        assets = [sample(id='1', deposit_type='股票', notes='600519'), sample(id='2', deposit_type='股票', notes='000001')]
        self.assertFalse(any('重复' in n['title'] for n in insights(assets, on=date(2026, 10, 10))))

    def test_investment_storage_export_import_backup_and_renewal_rejection(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            store = Store(Path(temp), migrate=False)
            asset_id = store.save(sample(deposit_type='ETF', notes='510300'))
            for suffix, exporter in [('.csv', store.export_csv), ('.json', store.export_json)]:
                path = Path(temp)/('holdings'+suffix)
                exporter(path, store.all())
                result = store.preview_import(path)['duplicates'][0][1]
                self.assertEqual(result['deposit_type'], 'ETF')
                self.assertEqual(result['maturity_date'], '')
            backup = store.backup()
            store.save(sample(deposit_type='ETF', amount='12000', notes='510300'), asset_id)
            store.restore(backup)
            self.assertEqual(store.get(asset_id)['amount'], '50000.00')
            with self.assertRaisesRegex(ValueError, '投资持仓'):
                store.renew(asset_id, '1')
            store.close()


class LegacyTests(unittest.TestCase):
    def test_sqlite_json_migration_preserves_rows_raw_records_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            base = Path(temp)
            legacy = base/'deposits.db'
            with closing(sqlite3.connect(legacy)) as conn:
                conn.execute('CREATE TABLE deposits (id INTEGER,bank TEXT,deposit_type TEXT,amount REAL,currency TEXT,deposit_date TEXT,maturity_date TEXT,interest_rate REAL,renewal_option TEXT,notes TEXT)')
                conn.executemany('INSERT INTO deposits VALUES(?,?,?,?,?,?,?,?,?,?)', [(1, '工行', '定期3年', 100, '人民币', '2023-11-10', '2026-11-10', 2.1, '自动续期', '原始'), (2, '工行', '定期3年', 100, '人民币', '2023-11-10', '2026-11-10', 2.1, '自动续期', '原始')])
                conn.commit()
            json_path = base/'deposits.json'
            json_path.write_text(json.dumps([{'id':'D001','bank_name':'招行','amount':200,'deposit_type':'定期','term_months':12,'start_date':'2026-01-01','end_date':'2027-01-01','account_number':'00123','account_name':'账户'}]), encoding='utf-8')
            old_bytes = legacy.read_bytes(), json_path.read_bytes()
            store = Store(base)
            self.assertEqual(len(store.all()), 3)
            self.assertTrue(all(a['raw_json'] for a in store.all()))
            self.assertIn('D001', [a['source_id'] for a in store.all()])
            store.close()
            store = Store(base)
            self.assertEqual(len(store.all()), 3)
            self.assertEqual(old_bytes, (legacy.read_bytes(), json_path.read_bytes()))
            store.close()

    def test_legacy_restore_converts_schema_and_has_safety_snapshot(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            base = Path(temp)
            store = Store(base, migrate=False)
            store.save(sample())
            legacy = base/'legacy.db'
            with closing(sqlite3.connect(legacy)) as conn:
                conn.execute('CREATE TABLE deposits (id INTEGER,bank TEXT,deposit_type TEXT,amount REAL,currency TEXT,deposit_date TEXT,maturity_date TEXT,interest_rate REAL)')
                conn.execute('INSERT INTO deposits VALUES(1,?,?,?,?,?,?,?)', ('招行','定期1年', 123.45,'人民币','2026-01-01','2027-01-01',1.2))
                conn.commit()
            safety = store.restore(legacy)
            self.assertEqual(store.all()[0]['bank'], '招行')
            self.assertEqual(read_records(safety)[0]['amount'], '50000.00')
            store.close()


if __name__ == '__main__':
    unittest.main()
