"""Test the actual form variable events, without focus changes or user data writes."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from assetbook.domain import today, add_months
from assetbook.storage import Store
from assetbook.ui import AssetDialog

TEST_TMP = Path(__file__).resolve().parent / '.tmp'
TEST_TMP.mkdir(exist_ok=True)


class AutoMaturityUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except tk.TclError as error:
            raise unittest.SkipTest(f'Tk display unavailable: {error}')

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.store = Store(Path(self.temp.name), migrate=False)
        self.errors = []
        self.root.report_callback_exception = lambda kind, error, trace: self.errors.append(str(error))
        self.app = SimpleNamespace(root=self.root, store=self.store, assets=[], currency=tk.StringVar(master=self.root, value='人民币'), refresh=Mock(), toast=Mock())
        self.dialog = AssetDialog(self.app)

    def tearDown(self):
        for window in self.root.winfo_children():
            if isinstance(window, tk.Toplevel):
                window.destroy()
        self.root.update()
        self.store.close()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def type_in(self, key, value):
        entry = self.dialog.field_inputs[key]
        entry.delete(0, 'end')
        entry.insert(0, value)

    def test_new_form_has_today_and_computed_default_maturity(self):
        self.assertEqual(self.dialog.vars['deposit_date'].get(), today().isoformat())
        self.assertEqual(self.dialog.vars['maturity_date'].get(), add_months(today(), 36).isoformat())
        self.assertEqual(self.dialog.field_inputs['maturity_date'].get(), add_months(today(), 36).isoformat())
        self.assertEqual(str(self.dialog.field_inputs['maturity_date'].cget('state')), 'readonly')
        self.assertEqual(self.dialog.field_inputs['maturity_date'].cget('style'), 'Calculated.TEntry')

    def test_changing_date_and_type_updates_without_focus_event(self):
        self.type_in('deposit_date', '2026-10-10')
        self.dialog.field_inputs['deposit_type'].set('定期1年')
        self.assertEqual(self.dialog.vars['term_months'].get(), '12')
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2027-10-10')
        self.assertEqual(self.dialog.field_inputs['maturity_date'].get(), '2027-10-10')
        self.type_in('deposit_date', '2026-11-15')
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2027-11-15')
        self.assertEqual(self.dialog.field_inputs['maturity_date'].get(), '2027-11-15')

    def test_partial_or_invalid_inputs_clear_stale_maturity(self):
        self.type_in('deposit_date', '2026-10-')
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '')
        self.assertEqual(self.dialog.field_inputs['maturity_date'].get(), '')
        self.dialog.vars['deposit_date'].set('2026-02-30')
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '')
        self.dialog.vars['deposit_date'].set('2026-02-28')
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2029-02-28')
        self.dialog.vars['term_months'].set('invalid')
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '')

    def test_custom_months_updates_type_and_date(self):
        self.type_in('deposit_date', '2026-01-31')
        self.type_in('term_months', '1')
        self.assertEqual(self.dialog.vars['deposit_type'].get(), '定期1个月')
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2026-02-28')
        self.assertEqual(self.dialog.field_inputs['maturity_date'].get(), '2026-02-28')
        self.type_in('term_months', '6')
        self.assertEqual(self.dialog.vars['deposit_type'].get(), '定期6个月')
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2026-07-31')
        self.assertEqual(self.dialog.field_inputs['maturity_date'].get(), '2026-07-31')

    def test_leap_year_and_month_end(self):
        self.dialog.vars['deposit_type'].set('定期1年')
        self.dialog.vars['deposit_date'].set('2024-02-29')
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2025-02-28')
        self.dialog.vars['deposit_type'].set('定期1个月')
        self.dialog.vars['deposit_date'].set('2024-01-31')
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2024-02-29')

    def test_manual_mode_keeps_special_date_until_auto_enabled(self):
        self.dialog.auto_maturity.set(False)
        self.type_in('maturity_date', '2030-12-31')
        self.dialog.vars['deposit_date'].set('2026-11-15')
        self.dialog.vars['deposit_type'].set('定期1年')
        self.dialog.vars['amount'].set('50000')
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2030-12-31')
        self.assertEqual(self.dialog.field_inputs['maturity_date'].get(), '2030-12-31')
        self.assertEqual(str(self.dialog.field_inputs['maturity_date'].cget('state')), 'normal')
        self.dialog.auto_maturity.set(True)
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2027-11-15')
        self.assertEqual(self.dialog.field_inputs['maturity_date'].get(), '2027-11-15')

    def test_edit_custom_maturity_keeps_saved_date_and_manual_mode(self):
        asset_id = self.store.save(dict(bank='工行', deposit_type='定期1年', amount='10000', deposit_date='2026-01-01', maturity_date='2027-01-05', term_months=12))
        self.dialog.destroy()
        self.dialog = AssetDialog(self.app, self.store.get(asset_id))
        self.assertFalse(self.dialog.auto_maturity.get())
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2027-01-05')
        self.dialog.vars['amount'].set('20000')
        self.dialog.save()
        self.assertEqual(self.store.get(asset_id)['maturity_date'], '2027-01-05')

    def test_quick_entry_preserves_explicit_custom_maturity(self):
        self.dialog.quick.set('工行 5万元 一年 2026-01-01 2027-01-05')
        self.dialog.parse()
        self.assertFalse(self.dialog.auto_maturity.get())
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2027-01-05')
        self.dialog.quick.set('工行 5万元 一年 2026-01-01')
        self.dialog.parse()
        self.assertTrue(self.dialog.auto_maturity.get())
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2027-01-01')

    def test_cash_and_investments_have_no_maturity_when_dates_change(self):
        for kind in ('活期', '股票', '基金'):
            self.dialog.vars['deposit_type'].set(kind)
            self.dialog.vars['deposit_date'].set('2026-01-01')
            self.assertEqual(self.dialog.vars['maturity_date'].get(), '')
            self.assertEqual(self.dialog.vars['term_months'].get(), '0')
        self.dialog.vars['deposit_type'].set('定期3年')
        self.assertEqual(self.dialog.vars['maturity_date'].get(), '2029-01-01')

    def test_immediate_save_persists_auto_date_and_rejects_blank_manual_date(self):
        self.dialog.field_inputs['bank'].set('工行')
        self.type_in('amount', '10000')
        self.dialog.field_inputs['deposit_type'].set('定期6个月')
        self.type_in('deposit_date', '2026-01-31')
        displayed = self.dialog.field_inputs['maturity_date'].get()
        self.assertEqual(displayed, '2026-07-31')
        self.dialog.save()
        self.assertEqual(self.store.all()[0]['maturity_date'], displayed)
        self.assertEqual(self.store.all()[0]['amount'], '10000.00')
        self.dialog = AssetDialog(self.app)
        self.dialog.vars['bank'].set('工行')
        self.dialog.vars['amount'].set('10000')
        self.dialog.auto_maturity.set(False)
        self.dialog.vars['maturity_date'].set('')
        self.dialog.save()
        self.assertEqual(len(self.store.all()), 1)
        self.assertIn('手动到期日期', self.dialog.error.get())


if __name__ == '__main__':
    unittest.main()
