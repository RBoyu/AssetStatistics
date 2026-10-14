"""A quiet, Chinese desktop workspace built only with Tk/ttk."""
from __future__ import annotations

import json
import logging
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from decimal import Decimal
from pathlib import Path

from . import __version__
from .domain import (BANKS, TYPES, CURRENCIES, RENEWALS, today, money, decimal, status,
                     active, totals, distribution, estimated_interest, maturity_schedule,
                     insights, search, parse_quick_entry, parse_date, add_months, term_months,
                     is_investment, has_maturity)
from .storage import Store, read_records
from .export import export_xlsx, export_svg

BG = "#F5F6F2"
WHITE = "#FFFFFF"
INK = "#233B33"
MUTED = "#7A867F"
GREEN = "#2B6754"
PALE = "#EAF1EB"
LINE = "#E3E8E1"
AMBER = "#A57225"
RED = "#AD514B"
FONT = "Microsoft YaHei UI"


def label(parent, text, size=10, color=INK, bold=False, bg=None, **kwargs):
    return tk.Label(parent, text=text, font=(FONT, size, "bold" if bold else "normal"),
                    fg=color, bg=bg or parent.cget("bg"), anchor="w", **kwargs)


def button(parent, text, command, primary=False, danger=False):
    return ttk.Button(parent, text=text, command=command,
                      style="Primary.TButton" if primary else "Danger.TButton" if danger else "TButton")


class ScrollFrame(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.window_id = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window_id, width=e.width))

    def scroll(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), "units")


class BarChart(tk.Canvas):
    def __init__(self, parent, data, currency, privacy=False, height=200, vertical=False):
        super().__init__(parent, height=height, bg=WHITE, highlightthickness=0)
        self.data, self.currency, self.privacy, self.vertical = data, currency, privacy, vertical
        self.bind("<Configure>", self.draw)

    def draw(self, event=None):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if not self.data or not any(value for _, value in self.data):
            self.create_text(width/2, height/2, text="暂无可统计的记录", fill=MUTED, font=(FONT, 11))
            return
        maximum = float(max(v for _, v in self.data)) or 1
        if self.vertical:
            n = len(self.data)
            column = (width - 32) / n
            baseline = height - 28
            self.create_line(16, baseline, width-16, baseline, fill=LINE)
            for i, (name, value) in enumerate(self.data):
                bar_height = float(value) / maximum * (height-68)
                x = 16 + i * column
                self.create_rectangle(x+column*.22, baseline-bar_height, x+column*.78, baseline, fill=GREEN, outline="")
                self.create_text(x+column/2, baseline+16, text=name[5:] if len(name) == 7 else name[-5:], fill=MUTED, font=(FONT, 9))
                if value:
                    self.create_text(x+column/2, baseline-bar_height-12, text="•••" if self.privacy else f"{float(value)/10000:.1f}万" if value >= 10000 else f"{value:,.0f}", fill=GREEN, font=(FONT, 8))
        else:
            rows = self.data[:6]
            row_height = max(26, (height-12) / len(rows))
            for i, (name, value) in enumerate(rows):
                y = 8 + i*row_height
                text_width = min(140, width*.28)
                bar_width = max(20, width-text_width-135)
                self.create_text(6, y+12, text=name[:13], anchor="w", fill=INK, font=(FONT, 9))
                self.create_rectangle(text_width, y+5, text_width+bar_width, y+20, fill=PALE, outline="")
                self.create_rectangle(text_width, y+5, text_width+float(value)/maximum*bar_width, y+20, fill=GREEN, outline="")
                self.create_text(width-8, y+12, text="••••" if self.privacy else f"{value:,.2f}", anchor="e", fill=GREEN, font=(FONT, 9))


class AssetDialog(tk.Toplevel):
    def __init__(self, app, asset=None, quick=False):
        super().__init__(app.root)
        self.app, self.asset = app, asset
        self._syncing_dates = False
        self.title("编辑资产" if asset else "新增资产")
        self.configure(bg=BG)
        self.geometry("730x790")
        self.minsize(660, 680)
        self.transient(app.root)
        self.grab_set()
        wrapper = ScrollFrame(self)
        wrapper.pack(fill="both", expand=True)
        self.bind('<MouseWheel>', wrapper.scroll)
        body = wrapper.inner
        label(body, "编辑这笔资产" if asset else "记录一笔新资产", 21, bold=True).pack(anchor="w", padx=28, pady=(22, 8))
        self.introduction = label(body, "填写实际记录，日期与利息会自动辅助计算。", color=MUTED)
        self.introduction.pack(anchor="w", padx=28)
        smart = tk.Frame(body, bg=PALE)
        smart.pack(fill="x", padx=28, pady=18)
        label(smart, "一句话录入", 11, GREEN, True).pack(anchor="w", padx=14, pady=(10, 3))
        label(smart, "例如：工行 5万元 三年 利率2.1%；或：中信证券 股票 市值3万元", 9, MUTED, wraplength=580).pack(anchor="w", padx=14)
        line = tk.Frame(smart, bg=PALE)
        line.pack(fill="x", padx=14, pady=12)
        self.quick = tk.StringVar()
        quick_entry = ttk.Entry(line, textvariable=self.quick)
        quick_entry.pack(side="left", fill="x", expand=True)
        button(line, "识别到表单", self.parse).pack(side="left", padx=(10, 0))
        self.feedback = tk.StringVar(value="识别结果只填入表单，核对后点击保存。")
        label(smart, "", 9, GREEN, textvariable=self.feedback, wraplength=580).pack(anchor="w", padx=14, pady=(0, 10))
        form = tk.Frame(body, bg=BG)
        form.pack(fill="x", padx=28)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        defaults = dict(bank="", deposit_type="定期3年", amount="", currency=app.currency.get(),
                        deposit_date=today().isoformat(), maturity_date="", interest_rate="", renewal_option="不续期",
                        term_months=36, account_name="", account_number="", notes="")
        if asset:
            defaults.update(asset)
        self.vars = {key: tk.StringVar(value=str(defaults.get(key) or "")) for key in
                     ("bank", "deposit_type", "amount", "currency", "deposit_date", "maturity_date", "interest_rate", "renewal_option", "term_months", "account_name", "account_number")}
        if defaults.get("interest_rate") is not None:
            self.vars["interest_rate"].set(str(defaults["interest_rate"]))
        auto_maturity = True
        if asset and asset.get('maturity_date') and not is_investment(asset):
            try:
                auto_maturity = add_months(parse_date(asset['deposit_date']), int(asset.get('term_months') or term_months(asset['deposit_type']))).isoformat() == asset['maturity_date']
            except (ValueError, OverflowError):
                auto_maturity = False
        self.auto_maturity = tk.BooleanVar(value=auto_maturity)
        fields = [("银行 / 机构 *", "bank", sorted(set(BANKS + [a['bank'] for a in app.assets]))),
                  ("资产类型 *", "deposit_type", TYPES), ("本金 / 金额 *", "amount", None),
                  ("币种 *", "currency", CURRENCIES), ("存入日期 *", "deposit_date", None),
                  ("期限（月）", "term_months", None), ("到期日期", "maturity_date", None),
                  ("年利率（%）", "interest_rate", None), ("续期方式", "renewal_option", RENEWALS),
                  ("账户名称", "account_name", None), ("账号（可选）", "account_number", None)]
        self.field_frames, self.field_labels, self.field_inputs = {}, {}, {}
        self.field_order = [key for _, key, _ in fields]
        for i, (title, key, options) in enumerate(fields):
            row, col = divmod(i, 2)
            field = tk.Frame(form, bg=BG)
            field.grid(row=row, column=col, sticky="ew", padx=(0, 12) if col == 0 else (12, 0), pady=(0, 9))
            self.field_frames[key] = field
            self.field_labels[key] = label(field, title, 9, MUTED)
            self.field_labels[key].pack(anchor="w", pady=(0, 5))
            entry = ttk.Combobox(field, textvariable=self.vars[key], values=options,
                                 state="readonly" if key == "renewal_option" else "normal") if options else ttk.Entry(field, textvariable=self.vars[key])
            entry.pack(fill="x", ipady=3)
            self.field_inputs[key] = entry
            if key == 'deposit_date':
                tk.Button(field, text='今天', command=lambda: self.vars['deposit_date'].set(today().isoformat()),
                          font=(FONT, 9), bg=BG, fg=GREEN, activebackground=PALE, bd=0, cursor='hand2').place(relx=1, y=0, anchor='ne')
            if key == 'maturity_date':
                self.auto_maturity_check = ttk.Checkbutton(field, text='自动计算到期日', variable=self.auto_maturity)
                self.auto_maturity_check.pack(anchor='w', pady=(5, 0))
        self.estimate = tk.StringVar()
        label(body, "", 10, GREEN, textvariable=self.estimate, wraplength=620).pack(anchor="w", padx=28, pady=(6, 8))
        self.notes_title = label(body, "备注", 9, MUTED)
        self.notes_title.pack(anchor="w", padx=28)
        self.notes = tk.Text(body, height=3, font=(FONT, 10), relief="flat", bg=WHITE,
                             highlightbackground=LINE, highlightthickness=1, wrap="word")
        self.notes.pack(fill="x", padx=28, pady=6)
        self.notes.insert("1.0", defaults.get("notes") or "")
        self.calculation_hint = tk.StringVar()
        label(body, "", 8, MUTED, wraplength=620, textvariable=self.calculation_hint).pack(anchor="w", padx=28, pady=6)
        self.error = tk.StringVar()
        label(body, "", 10, RED, textvariable=self.error, wraplength=620).pack(anchor="w", padx=28, pady=5)
        for key in ("amount", "interest_rate", "maturity_date"):
            self.vars[key].trace_add("write", lambda *_: self.update_estimate())
        self.vars['deposit_date'].trace_add('write', lambda *_: self.calculate_date())
        self.vars['term_months'].trace_add('write', lambda *_: self.months_changed())
        self.vars['deposit_type'].trace_add('write', lambda *_: self.kind_changed())
        self.auto_maturity.trace_add('write', lambda *_: self.calculate_date())
        # Loading an existing record must retain its saved dates and custom term.
        self._syncing_dates = True
        try:
            self.update_kind_fields()
        finally:
            self._syncing_dates = False
        self.calculate_date()
        footer = tk.Frame(self, bg=WHITE)
        footer.pack(fill="x", side="bottom")
        button(footer, "保存资产", self.save, primary=True).pack(side="right", padx=24, pady=14)
        button(footer, "取消", self.destroy).pack(side="right", pady=14)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-Return>", lambda e: self.save())
        if quick:
            quick_entry.focus_set()

    def values(self):
        return {key: var.get() for key, var in self.vars.items()} | {"notes": self.notes.get("1.0", "end-1c")}

    def kind_changed(self, event=None):
        if self._syncing_dates:
            return
        kind = self.vars["deposit_type"].get()
        self._syncing_dates = True
        try:
            self.vars['term_months'].set(str(term_months(kind)))
            self.update_kind_fields()
        finally:
            self._syncing_dates = False
        self.calculate_date()

    def months_changed(self):
        if self._syncing_dates:
            return
        kind = self.vars['deposit_type'].get()
        if kind.startswith('定期'):
            try:
                months = int(self.vars['term_months'].get())
                if 0 < months <= 1200 and term_months(kind) != months:
                    self._syncing_dates = True
                    try:
                        self.vars['deposit_type'].set(f'定期{months}个月')
                        self.update_kind_fields()
                    finally:
                        self._syncing_dates = False
            except ValueError:
                pass
        self.calculate_date()

    def update_kind_fields(self):
        investment = is_investment(self.vars['deposit_type'].get())
        self.introduction.configure(text='登记投资持仓的当前市值、机构与建仓信息。' if investment else '填写实际记录，日期与利息会自动辅助计算。')
        excluded = {'term_months', 'maturity_date', 'interest_rate', 'renewal_option'} if investment else set()
        for field in self.field_frames.values():
            field.grid_remove()
        for i, key in enumerate(key for key in self.field_order if key not in excluded):
            row, col = divmod(i, 2)
            self.field_frames[key].grid(row=row, column=col, sticky='ew', padx=(0, 12) if col == 0 else (12, 0), pady=(0, 9))
        self.field_labels['bank'].configure(text='券商 / 平台 / 机构 *' if investment else '银行 / 机构 *')
        self.field_labels['amount'].configure(text='持仓市值 / 金额 *' if investment else '本金 / 金额 *')
        self.field_labels['deposit_date'].configure(text='买入 / 建仓日期 *' if investment else '存入日期 *')
        self.notes_title.configure(text='标的名称、代码与备注' if investment else '备注')
        self.calculation_hint.set('投资按手动登记的当前市值统计，可通过编辑更新；不自动获取行情或估算固定利息。' if investment else
                                  '利息为单利估算：完整期限按月数/12，其他日期按实际天数/365；实际到账以银行为准。')
        if investment:
            for key, value in [('maturity_date', ''), ('term_months', '0'), ('interest_rate', ''), ('renewal_option', '不适用')]:
                self.vars[key].set(value)
        elif self.vars['renewal_option'].get() == '不适用':
            self.vars['renewal_option'].set('不续期')
        self.update_estimate()

    def calculate_date(self, event=None):
        if self._syncing_dates:
            return
        no_maturity = is_investment(self.vars['deposit_type'].get()) or self.vars['deposit_type'].get() == '活期'
        automatic = self.auto_maturity.get() and not no_maturity
        self.field_labels['maturity_date'].configure(text='到期日期（自动计算）' if automatic else '到期日期')
        self.field_inputs['maturity_date'].configure(
            state='readonly' if no_maturity or automatic else 'normal',
            style='Calculated.TEntry' if automatic else 'TEntry')
        self.auto_maturity_check.configure(state='disabled' if no_maturity else 'normal')
        if no_maturity:
            self.vars['maturity_date'].set('')
        elif self.auto_maturity.get():
            calculated = ''
            try:
                months = int(self.vars['term_months'].get() or 0)
                if 0 < months <= 1200:
                    calculated = add_months(parse_date(self.vars['deposit_date'].get()), months).isoformat()
            except (ValueError, OverflowError):
                pass
            # Incomplete input cannot leave a stale date that looks valid.
            self.vars['maturity_date'].set(calculated)
        self.update_estimate()

    def update_estimate(self):
        if is_investment(self.vars['deposit_type'].get()):
            self.estimate.set('投资持仓 · 无固定到期日')
            return
        try:
            result = estimated_interest(self.values()) if hasattr(self, "notes") else None
            self.estimate.set("预计到期利息  " + (money(result, self.vars["currency"].get()) if result is not None else "补全金额、期限和利率后显示"))
        except ValueError:
            self.estimate.set("请填写有效的金额、日期和利率")

    def parse(self):
        try:
            if not self.quick.get().strip():
                raise ValueError("先输入银行、金额、期限等信息")
            data = parse_quick_entry(self.quick.get())
            # Clear fields from a previous parse so incomplete input cannot silently reuse them.
            self._syncing_dates = True
            try:
                for key in ("bank", "amount", "deposit_type", "term_months", "maturity_date", "interest_rate"):
                    self.vars[key].set("")
                for key, value in data.items():
                    if key in self.vars:
                        self.vars[key].set('' if value is None else str(value))
                if is_investment(data):
                    self.notes.delete('1.0', 'end')
                    self.notes.insert('1.0', data.get('notes', ''))
                automatic = True
                if data.get('maturity_date') and not is_investment(data):
                    automatic = add_months(parse_date(data['deposit_date']), int(data.get('term_months') or 0)).isoformat() == data['maturity_date']
                self.auto_maturity.set(automatic)
                self.update_kind_fields()
            finally:
                self._syncing_dates = False
            self.calculate_date()
            missing = [title for key, title in (("bank", "机构"), ("amount", "金额"), ("deposit_type", "类型")) if not data.get(key)]
            self.feedback.set("已识别，仍需填写：" + "、".join(missing) if missing else "已填入表单，请核对金额、日期、利率后保存。")
            self.error.set("")
        except (ValueError, OverflowError) as error:
            self.error.set(str(error))

    def save(self):
        try:
            self.calculate_date()
            values = self.values()
            if not self.auto_maturity.get() and not is_investment(values) and values['deposit_type'].startswith('定期') and not values['maturity_date']:
                raise ValueError('请填写手动到期日期，或勾选“自动计算到期日”')
            self.app.store.save(values, self.asset["id"] if self.asset else None,
                                self.asset.get("updated_at") if self.asset else None)
            self.app.refresh()
            self.app.toast("资产已保存")
            self.destroy()
        except Exception as error:
            self.error.set(str(error))


class App:
    PAGES = [("overview", "资产总览", "◈"), ("ledger", "资产明细", "≡"), ("calendar", "到期日历", "▦"),
             ("analysis", "资产分析", "▥"), ("assistant", "智能助手", "✦"), ("backups", "备份与恢复", "↺"), ("settings", "设置", "⚙")]

    def __init__(self, root, store):
        self.root, self.store = root, store
        # Geometry uses physical pixels; keep point fonts consistent with that geometry on high-DPI Windows.
        root.tk.call('tk', 'scaling', 4 / 3)
        self.assets = store.all()
        self.page = "overview"
        self.privacy = False
        self.reminder_days = int(store.setting("reminder_days", 30))
        self.currency = tk.StringVar(value=store.setting("currency", "人民币"))
        self.search_var = tk.StringVar()
        self.bank_var = tk.StringVar(value="全部机构")
        self.state_var = tk.StringVar(value="全部状态")
        self.ledger_currency = tk.StringVar(value="全部币种")
        self.filtered = []
        self.sort_key, self.sort_desc = "deposit_date", True
        self.search_job = None
        self.timer = None
        self.root.title(f"资产账本 · {__version__}")
        self.root.geometry("1440x900")
        self.root.minsize(1080, 720)
        self.root.configure(bg=BG)
        icon = store.base / 'icon.ico'
        if not icon.exists():
            icon = Path(__file__).resolve().parent / 'resources' / 'icon.ico'
        if icon.exists():
            self.root.iconbitmap(str(icon))
        self.configure_styles()
        self.build_shell()
        self.show("overview")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Control-n>", lambda e: self.new_asset())
        self.root.bind("<Control-f>", lambda e: self.focus_search())
        self.root.bind("<F5>", lambda e: self.refresh())
        self.root.bind("<Control-Shift-P>", lambda e: self.toggle_privacy())
        self.root.bind_all("<MouseWheel>", self.scroll_page, add="+")
        self.root.report_callback_exception = self.callback_error
        if store.setting("auto_backup", True):
            store.daily_backup()
        self.timer = root.after(60000, self.tick)
        if store.migration_messages:
            self.root.after(300, lambda: messagebox.showinfo("旧数据导入结果", "\n".join(store.migration_messages) + "\n\n原数据库、JSON 和已有备份均保留。", parent=root))

    def configure_styles(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", font=(FONT, 10), background=BG, foreground=INK)
        s.configure("TButton", padding=(14, 9), background=WHITE, borderwidth=1, bordercolor=LINE)
        s.map("TButton", background=[("active", PALE)], foreground=[("disabled", MUTED)])
        s.configure("Primary.TButton", background=GREEN, foreground=WHITE, bordercolor=GREEN)
        s.map("Primary.TButton", background=[("active", "#215440"), ("disabled", MUTED)], foreground=[("active", WHITE)])
        s.configure("Danger.TButton", foreground=RED)
        s.configure("TEntry", padding=7, fieldbackground=WHITE, bordercolor=LINE)
        s.configure('Calculated.TEntry', foreground=GREEN, fieldbackground=PALE)
        s.map('Calculated.TEntry', foreground=[('readonly', GREEN)], fieldbackground=[('readonly', PALE)])
        s.configure("TCombobox", padding=7, fieldbackground=WHITE, bordercolor=LINE, arrowsize=12)
        s.map("TCombobox", fieldbackground=[("readonly", WHITE)], selectbackground=[("readonly", WHITE)], selectforeground=[("readonly", INK)])
        s.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=INK,
                    rowheight=42, borderwidth=0, font=(FONT, 10))
        s.configure("Treeview.Heading", background="#EDF0EA", foreground=MUTED,
                    font=(FONT, 9, "bold"), relief="flat", padding=(8, 10))
        s.map("Treeview", background=[("selected", "#DCEADD")], foreground=[("selected", INK)])
        s.configure("TScrollbar", background=LINE, troughcolor=BG, borderwidth=0)
        s.configure("TCheckbutton", background=BG)

    def build_shell(self):
        sidebar = tk.Frame(self.root, bg=INK, width=206)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=INK)
        brand.pack(fill="x", padx=22, pady=(34, 5))
        label(brand, "资", 22, "#C6DDAC", True).pack(side="left")
        label(brand, "资产账本", 16, WHITE, True).pack(side="left", padx=10)
        label(sidebar, "LOCAL ASSET BOOK", 8, "#9AADA0").pack(anchor="w", padx=25, pady=(4, 35))
        self.nav = {}
        for key, name, symbol in self.PAGES:
            btn = tk.Button(sidebar, text=f"{symbol}    {name}", command=lambda k=key: self.show(k),
                            font=(FONT, 11), bg=INK, fg="#BCC9C0", activebackground=GREEN,
                            activeforeground=WHITE, bd=0, relief="flat", anchor="w", padx=18, pady=13, cursor="hand2")
            btn.pack(fill="x", padx=12, pady=3)
            self.nav[key] = btn
        bottom = tk.Frame(sidebar, bg=INK)
        bottom.pack(side="bottom", fill="x", padx=24, pady=26)
        label(bottom, "●  本地保存", 10, "#BAD5A7").pack(anchor="w")
        label(bottom, f"版本 {__version__}  ·  无需联网", 8, "#92A599").pack(anchor="w", pady=7)
        main = tk.Frame(self.root, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        header = tk.Frame(main, bg=BG)
        header.pack(fill="x", padx=30, pady=(24, 18))
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        self.title = tk.StringVar()
        self.subtitle = tk.StringVar()
        label(titles, "", 24, bold=True, textvariable=self.title).pack(anchor="w")
        label(titles, "", 9, MUTED, textvariable=self.subtitle).pack(anchor="w", pady=(5, 0))
        button(header, "+ 新增资产", self.new_asset, True).pack(side="right")
        self.privacy_btn = button(header, "隐藏金额", self.toggle_privacy)
        self.privacy_btn.pack(side="right", padx=10)
        self.currency_combo = ttk.Combobox(header, textvariable=self.currency, width=9, state="readonly",
                                           values=sorted(set(CURRENCIES + [a['currency'] for a in self.assets])))
        self.currency_combo.pack(side="right", padx=6)
        self.currency_combo.bind("<<ComboboxSelected>>", lambda e: self.currency_changed())
        self.body = tk.Frame(main, bg=BG)
        self.body.pack(fill="both", expand=True, padx=(30, 18))
        footer = tk.Frame(main, bg=BG)
        footer.pack(fill="x", padx=30, pady=(10, 12))
        self.message = tk.StringVar(value="所有数据保存在本机  ·  Ctrl+N 新增  ·  Ctrl+F 搜索  ·  F5 刷新")
        label(footer, "", 8, MUTED, textvariable=self.message).pack(side="left")
        label(footer, today().strftime("%Y / %m / %d"), 8, MUTED).pack(side="right")

    def show(self, page):
        self.page = page
        self.assets = self.store.all()
        for child in self.body.winfo_children():
            child.destroy()
        self.scroll = None
        name = next(name for key, name, _ in self.PAGES if key == page)
        self.title.set(name)
        subtitles = {"overview": "每一笔资产，清楚可见。", "ledger": "搜索、整理和管理你的全部资产记录。", "calendar": "提前安排到期事项，核对后登记处理结果。",
                     "analysis": "按币种独立统计，了解资产结构与到期节奏。", "assistant": "根据你的本地数据，提供可以核对的提醒。",
                     "backups": "保留历史快照，恢复前自动备份当前数据。", "settings": "调整提醒与显示偏好。"}
        self.subtitle.set(subtitles[page])
        for key, btn in self.nav.items():
            btn.configure(bg=GREEN if key == page else INK, fg=WHITE if key == page else "#BCC9C0")
        if page == "ledger":
            self.ledger(self.body)
        else:
            self.scroll = ScrollFrame(self.body)
            self.scroll.pack(fill="both", expand=True)
            getattr(self, page)(self.scroll.inner)

    def refresh(self):
        self.show(self.page)

    def tick(self):
        if self.page in ("overview", "calendar", "assistant") and not self.root.grab_current():
            self.refresh()
        self.timer = self.root.after(60000, self.tick)

    def scroll_page(self, event):
        if self.scroll and not self.root.grab_current() and event.widget.winfo_class() not in ("Treeview", "Text", "TCombobox"):
            self.scroll.scroll(event)

    def close(self):
        if self.timer:
            self.root.after_cancel(self.timer)
        if self.search_job:
            self.root.after_cancel(self.search_job)
        self.store.close()
        self.root.destroy()

    def callback_error(self, kind, value, tb):
        logging.error("UI callback failed", exc_info=(kind, value, tb))
        messagebox.showerror("操作未完成", f"{value}\n\n详细信息已写入 logs/assetbook.log。", parent=self.root)

    def currency_changed(self):
        self.store.set_setting("currency", self.currency.get())
        self.refresh()

    def toggle_privacy(self):
        self.privacy = not self.privacy
        self.privacy_btn.configure(text="显示金额" if self.privacy else "隐藏金额")
        self.refresh()

    def fmt(self, value, currency=None):
        return "••••••" if self.privacy else money(value, currency or self.currency.get())

    def toast(self, text):
        self.message.set(text)

    def card(self, parent, title, expand=True):
        outer = tk.Frame(parent, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        if expand:
            outer.pack(fill="x", pady=(0, 16))
        label(outer, title, 12, bold=True).pack(anchor="w", padx=20, pady=(17, 12))
        return outer

    def metrics(self, parent):
        currency = self.currency.get()
        assets = [a for a in self.assets if a["currency"] == currency and active(a)]
        total = sum((decimal(a["amount"]) for a in assets), Decimal(0))
        due = [a for a in assets if status(a, reminder_days=self.reminder_days) in ("已到期", "即将到期")]
        annual = sum((decimal(a["amount"])*decimal(a["interest_rate"])/100 for a in assets if not is_investment(a) and a.get("interest_rate") is not None), Decimal(0))
        fixed = [a for a in assets if has_maturity(a) and estimated_interest(a) is not None]
        projected = sum((estimated_interest(a) for a in fixed), Decimal(0))
        specs = [("在账资产总额", self.fmt(total), f"{currency} · {len(assets)} 笔在账资产"),
                 ("预计整期利息", self.fmt(projected), f"{len(fixed)} 笔已填利率的定期记录"),
                 ("到期待关注", f"{len(due)} 笔", f"已到期或 {self.reminder_days} 天内到期"),
                 ("存款按年利率估算", self.fmt(annual), "本金 × 已填年利率，非实际年收益")]
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(0, 20))
        for i, (title, value, hint) in enumerate(specs):
            row.columnconfigure(i, weight=1, uniform="metric")
            card = tk.Frame(row, bg=GREEN if i == 0 else WHITE, highlightbackground=LINE, highlightthickness=0 if i == 0 else 1)
            card.grid(row=0, column=i, sticky="nsew", padx=(0, 12) if i < 3 else 0)
            label(card, title, 10, "#D0E2D3" if i == 0 else MUTED).pack(anchor="w", padx=18, pady=(18, 8))
            label(card, value, 20 if len(value) < 14 else 15, WHITE if i == 0 else INK, True).pack(anchor="w", padx=18)
            label(card, hint, 8, "#D0E2D3" if i == 0 else MUTED, wraplength=230).pack(anchor="w", padx=18, pady=(10, 18))

    def overview(self, parent):
        self.metrics(parent)
        notices = insights(self.assets, self.reminder_days)
        info = tk.Frame(parent, bg=PALE)
        info.pack(fill="x", pady=(0, 20))
        label(info, "✦  " + notices[0]["title"], 11, GREEN, True).pack(side="left", padx=18, pady=14)
        button(info, "查看提醒 →", lambda: self.show("assistant")).pack(side="right", padx=14, pady=8)
        charts = tk.Frame(parent, bg=BG)
        charts.pack(fill="x", pady=(0, 20))
        charts.columnconfigure(0, weight=1, uniform="chart")
        charts.columnconfigure(1, weight=1, uniform="chart")
        for i, (title, data, vertical) in enumerate([
                ("机构分布", distribution(self.assets, self.currency.get()), False),
                ("未来 12 个月到期本金", maturity_schedule(self.assets, self.currency.get()), True)]):
            panel = self.card(charts, title, False)
            panel.grid(row=0, column=i, sticky="nsew", padx=(0, 16) if i == 0 else 0)
            BarChart(panel, data, self.currency.get(), self.privacy, height=180, vertical=vertical).pack(fill="x", padx=14, pady=(0, 12))
            label(panel, "按当前所选币种统计" if i == 0 else "按登记到期日排列，包含本金，不含续期假设", 8, MUTED).pack(anchor="w", padx=20, pady=(0, 16))
        card = self.card(parent, "近期到期事项")
        due = [a for a in self.assets if a["currency"] == self.currency.get() and active(a) and has_maturity(a)]
        due.sort(key=lambda a: a["maturity_date"])
        self.compact_table(card, due[:5])
        links = tk.Frame(card, bg=WHITE)
        links.pack(fill="x", padx=18, pady=12)
        label(links, "历史到期记录仍计入在账本金，结清后移出。", 8, MUTED).pack(side="left")
        button(links, "查看全部 →", lambda: self.show("calendar")).pack(side="right")
        all_totals = totals(self.assets)
        if len(all_totals) > 1:
            label(parent, "各币种在账资产：" + "  /  ".join(f"{c} {self.fmt(v, c)}" for c, v in all_totals.items()), 9, MUTED, wraplength=1050).pack(anchor="w", pady=(0, 14))
        label(parent, '存款按本金、投资按手动登记市值汇总；投资不计入固定利息或到期计划。', 9, MUTED).pack(anchor='w', pady=(0, 14))

    def compact_table(self, parent, assets, height=5):
        tree = self.table(parent, [("bank", "银行 / 机构", 180), ("deposit_type", "类型", 100), ("amount", "本金", 130),
                                   ("maturity_date", "到期日", 130), ("interest", "预计利息", 130), ("status", "状态", 110)], height=height)
        for a in assets:
            interest = estimated_interest(a)
            tree.insert("", "end", iid=a["id"], values=(a["bank"], a["deposit_type"], self.fmt(a["amount"], a["currency"]),
                        a["maturity_date"], self.fmt(interest, a["currency"]) if interest is not None else "—", status(a, reminder_days=self.reminder_days)))
        if not assets:
            label(parent, "暂无相关资产记录", 10, MUTED).pack(anchor="w", padx=20, pady=12)
        tree.bind("<Double-1>", lambda e: self.detail(tree.selection()[0]) if tree.selection() else None)
        return tree

    def table(self, parent, columns, height=10):
        frame = tk.Frame(parent, bg=WHITE)
        frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        tree = ttk.Treeview(frame, columns=[key for key, _, _ in columns], show="headings", selectmode="browse", height=height)
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=width, anchor="w", stretch=True)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree.tag_configure("even", background="#F8FAF7")
        tree.tag_configure("due", foreground=RED)
        tree.tag_configure("soon", foreground=AMBER)
        tree.tag_configure("closed", foreground=MUTED)
        return tree

    def ledger(self, parent):
        tools = tk.Frame(parent, bg=BG)
        tools.pack(fill="x", pady=(0, 12))
        self.search_entry = ttk.Entry(tools, textvariable=self.search_var)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.search_entry.bind("<KeyRelease>", lambda e: self.debounce_search())
        for variable, options, width in [(self.ledger_currency, ["全部币种"]+sorted(set(CURRENCIES + [a['currency'] for a in self.assets])), 10),
                                        (self.bank_var, ["全部机构"]+sorted({a['bank'] for a in self.assets}), 15),
                                        (self.state_var, ["全部状态", "存续中", "持有中", "即将到期", "已到期", "活期", "未开始", "已结清", "已删除", "待核对"], 11)]:
            combo = ttk.Combobox(tools, textvariable=variable, values=options, state="readonly", width=width)
            combo.pack(side="left", padx=(0, 8))
            combo.bind("<<ComboboxSelected>>", lambda e: self.fill_ledger())
        button(tools, "重置", self.reset_search).pack(side="left")
        label(parent, "支持银行、账户、备注关键词，也支持：工行  /  未来30天到期  /  超过5万  /  本月到期", 8, MUTED).pack(anchor="w", pady=(0, 16))
        actions = tk.Frame(parent, bg=BG)
        actions.pack(fill="x", pady=(0, 12))
        for text, action in [("编辑", self.edit_selected), ("详情", self.detail_selected), ("登记续期", self.renew_selected),
                             ("结清 / 重新打开", self.settle_selected), ("删除 / 恢复", self.delete_selected)]:
            button(actions, text, action).pack(side="left", padx=(0, 8))
        button(actions, "导出", self.export).pack(side="right")
        button(actions, "导入数据", self.import_data).pack(side="right", padx=8)
        card = tk.Frame(parent, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="both", expand=True)
        label(card, "资产记录 · 双击查看详情，点击列名排序", 10, MUTED).pack(anchor="w", padx=18, pady=14)
        columns = [("bank", "银行 / 机构", 150), ("deposit_type", "类型", 105), ("amount", "本金 / 市值", 125), ("currency", "币种", 70),
                   ("deposit_date", "存入 / 建仓日", 105), ("maturity_date", "到期日", 105), ("interest_rate", "年利率", 70),
                   ("status", "状态", 90), ("renewal_option", "续期", 100), ("account_name", "账户", 90), ("notes", "备注", 140)]
        self.tree = self.table(card, columns, height=10)
        for key, title, width in columns:
            self.tree.heading(key, text=title, command=lambda k=key: self.sort_ledger(k))
        self.tree.bind("<Double-1>", lambda e: self.detail_selected())
        self.summary = tk.StringVar()
        label(card, "", 9, MUTED, textvariable=self.summary, wraplength=1000).pack(anchor="w", padx=18, pady=12)
        self.fill_ledger()

    def debounce_search(self):
        if self.search_job:
            self.root.after_cancel(self.search_job)
        self.search_job = self.root.after(160, self.fill_ledger)

    def fill_ledger(self):
        self.search_job = None
        if self.page != "ledger" or not self.tree.winfo_exists():
            return
        selected = self.tree.selection()
        for child in self.tree.get_children():
            self.tree.delete(child)
        self.filtered = search(self.assets, self.search_var.get(), self.ledger_currency.get(), self.state_var.get(), self.bank_var.get(), self.reminder_days)
        def key(a):
            value = status(a, reminder_days=self.reminder_days) if self.sort_key == "status" else a.get(self.sort_key)
            if self.sort_key in ("amount", "interest_rate"):
                return decimal(value) if value not in (None, "") else Decimal(-1)
            return str(value or "")
        self.filtered.sort(key=key, reverse=self.sort_desc)
        for i, a in enumerate(self.filtered):
            s = status(a, reminder_days=self.reminder_days)
            tag = "due" if s == "已到期" else "soon" if s == "即将到期" else "closed" if s in ("已结清", "已删除") else "even" if i%2 == 0 else ""
            self.tree.insert("", "end", iid=a["id"], tags=(tag,), values=(a["bank"], a["deposit_type"], self.fmt(a["amount"], a["currency"]),
                a["currency"], a["deposit_date"], '不适用' if is_investment(a) else a.get("maturity_date") or "—", f"{a['interest_rate']}%" if a['interest_rate'] is not None and not is_investment(a) else "—",
                s, '不适用' if is_investment(a) else a["renewal_option"], "•••" if self.privacy and a.get("account_name") else a.get("account_name", ""), "已隐藏" if self.privacy and a.get('notes') else a["notes"] or ""))
        for asset_id in selected:
            if self.tree.exists(asset_id):
                self.tree.selection_set(asset_id)
        amounts = totals(self.filtered)
        self.summary.set(f"找到 {len(self.filtered)} 笔记录   ·   在账资产 " + ("  /  ".join(f"{c} {self.fmt(v,c)}" for c, v in amounts.items()) or "—"))

    def sort_ledger(self, key):
        self.sort_desc = not self.sort_desc if self.sort_key == key else False
        self.sort_key = key
        self.fill_ledger()

    def reset_search(self):
        self.search_var.set("")
        self.bank_var.set("全部机构")
        self.state_var.set("全部状态")
        self.ledger_currency.set("全部币种")
        self.fill_ledger()

    def focus_search(self):
        self.show("ledger")
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")

    def selected(self):
        choices = self.tree.selection() if self.page == "ledger" else ()
        if not choices:
            messagebox.showinfo("选择记录", "请先在资产明细中选择一笔记录。", parent=self.root)
            return None
        return self.store.get(choices[0])

    def new_asset(self, quick=False):
        return AssetDialog(self, quick=quick)

    def edit_selected(self):
        asset = self.selected()
        if asset:
            if asset.get("deleted_at") or asset.get("closed_at"):
                messagebox.showinfo("记录状态", "请先恢复或重新打开记录再编辑。", parent=self.root)
            else:
                AssetDialog(self, asset)

    def detail_selected(self):
        asset = self.selected()
        if asset:
            self.detail(asset["id"])

    def detail(self, asset_id):
        a = self.store.get(asset_id)
        text = f"{a['bank']} · {a['deposit_type']}\n\n"
        names = {"id": "编号", "amount": "本金", "currency": "币种", "deposit_date": "存入日期", "maturity_date": "到期日",
                 "interest_rate": "年利率（%）", "renewal_option": "续期方式", "term_months": "期限月数", "auto_renew_count": "续期次数",
                 "account_name": "账户名称", "account_number": "账号", "notes": "备注", "source": "数据来源", "source_id": "旧版编号",
                 "created_at": "导入 / 创建时间", "updated_at": "更新时间"}
        if is_investment(a):
            names['amount'], names['deposit_date'], names['notes'] = '登记持仓市值', '买入 / 建仓日', '标的名称、代码与备注'
            for key in ('maturity_date', 'interest_rate', 'renewal_option', 'term_months', 'auto_renew_count'):
                names.pop(key)
        for key, name in names.items():
            value = a.get(key)
            if key == "amount":
                value = self.fmt(a['amount'], a['currency'])
            if key == "account_number" and value:
                value = "•••• " + value[-4:]
            if self.privacy and key in ("account_name", "notes"):
                value = "已隐藏"
            text += f"{name}：{value if value not in (None, '') else '—'}\n"
        text += f"当前状态：{status(a, reminder_days=self.reminder_days)}\n"
        interest = estimated_interest(a)
        text += '投资持仓按登记市值汇总，无固定到期日。' if is_investment(a) else "预计整期利息：" + (self.fmt(interest, a['currency']) if interest is not None else "—")
        text += "\n\n操作历史\n" + "\n\n".join(self.history_text(h) for h in self.store.history(asset_id))
        win = self.text_window("资产详情", text)
        actions = tk.Frame(win, bg=BG)
        actions.pack(fill="x", padx=20, pady=12)
        if not a.get('deleted_at') and not a.get('closed_at'):
            button(actions, "编辑记录", lambda: (win.destroy(), AssetDialog(self, a))).pack(side="right")
        if a.get("raw_json") and not self.privacy:
            button(actions, "查看原始导入记录", lambda: self.text_window("原始记录", json.dumps(json.loads(a["raw_json"]), ensure_ascii=False, indent=2))).pack(side="left")

    def settle_selected(self):
        asset = self.selected()
        if not asset:
            return
        action = "重新打开" if asset.get("closed_at") else "结清"
        if asset.get("deleted_at"):
            messagebox.showinfo("记录状态", "请先恢复已删除记录。", parent=self.root)
            return
        if messagebox.askyesno(action, f"{action} {asset['bank']} 的这笔记录？\n" + ("结清后不再计入在账本金，历史记录仍保留。" if action == "结清" else "重新打开后恢复计入在账本金。"), parent=self.root):
            self.store.change_state(asset['id'], action)
            self.refresh()
            self.toast("已" + action)

    def delete_selected(self):
        asset = self.selected()
        if not asset:
            return
        action = "恢复" if asset.get("deleted_at") else "删除"
        if action == "恢复" or messagebox.askyesno("删除记录", "将记录移入回收站？\n以后可选择“已删除”状态恢复。", parent=self.root):
            self.store.change_state(asset['id'], action)
            self.refresh()
            self.toast("记录已" + action)

    def renew_selected(self):
        asset = self.selected()
        if not asset:
            return
        if is_investment(asset):
            messagebox.showinfo('投资持仓', '投资没有固定到期日；可编辑更新市值，卖出后结清记录。', parent=self.root)
            return
        if status(asset) != "已到期":
            messagebox.showinfo("续期条件", "请选择已到期的记录，并核对银行实际续期信息。", parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("登记实际续期")
        win.configure(bg=BG)
        win.geometry("470x450")
        win.transient(self.root)
        win.grab_set()
        label(win, "登记银行确认的续期", 18, bold=True).pack(anchor="w", padx=24, pady=(22, 8))
        label(win, "每次登记一轮；新本金与新利率由你核对。", 9, MUTED).pack(anchor="w", padx=24, pady=(0, 16))
        variables = {"start": tk.StringVar(value=today().isoformat()), "months": tk.StringVar(value=str(asset['term_months'] or 12)),
                     "amount": tk.StringVar(value=asset['amount']), "rate": tk.StringVar(value=asset['interest_rate'] or "")}
        for key, title in [("start", "本期存入日期"), ("months", "本期期限（月）"), ("amount", "本期本金"), ("rate", "本期年利率（%）")]:
            row = tk.Frame(win, bg=BG)
            row.pack(fill="x", padx=24, pady=6)
            label(row, title, 10, width=16).pack(side="left")
            ttk.Entry(row, textvariable=variables[key]).pack(side="left", fill="x", expand=True)
        error = tk.StringVar()
        label(win, "", 9, RED, textvariable=error, wraplength=420).pack(anchor="w", padx=24, pady=10)
        def save():
            try:
                self.store.renew(asset['id'], variables['rate'].get(), variables['start'].get(), int(variables['months'].get()), variables['amount'].get())
                self.refresh()
                self.toast("续期已登记，上一期信息保存在操作历史中")
                win.destroy()
            except (ValueError, OverflowError) as exc:
                error.set(str(exc))
        button(win, "确认登记", save, True).pack(anchor="e", padx=24, pady=12)

    def calendar(self, parent):
        currency = self.currency.get()
        chart = self.card(parent, "到期本金 · 未来 12 个月")
        BarChart(chart, maturity_schedule(self.assets, currency), currency, self.privacy, 210, True).pack(fill="x", padx=16, pady=(0, 18))
        assets = [a for a in self.assets if a['currency'] == currency and active(a) and has_maturity(a)]
        assets.sort(key=lambda a: a['maturity_date'])
        due = [a for a in assets if status(a, reminder_days=self.reminder_days) == '已到期']
        soon = [a for a in assets if status(a, reminder_days=self.reminder_days) == '即将到期']
        later = [a for a in assets if a not in due and a not in soon]
        for title, data in [("已到期 · 待核对处理", due), (f"未来 {self.reminder_days} 天到期", soon), ("后续到期计划", later)]:
            card = self.card(parent, f"{title}  /  {len(data)} 笔")
            self.compact_table(card, data, min(max(len(data), 2), 8))
        label(parent, "选择资产明细中的记录，可登记续期或结清；日历只提供提醒。", 9, MUTED).pack(anchor="w", pady=12)

    def analysis(self, parent):
        self.metrics(parent)
        selected = tk.StringVar(value="机构分布")
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(0, 16))
        choices = ttk.Combobox(row, textvariable=selected, values=["机构分布", "资产类型", "续期方式", "存入月份", "到期本金"], state="readonly", width=18)
        choices.pack(side="left")
        holder = tk.Frame(parent, bg=BG)
        holder.pack(fill="both", expand=True)
        current = {}
        def render(event=None):
            for child in holder.winfo_children():
                child.destroy()
            mode = selected.get()
            currency = self.currency.get()
            field = {"机构分布": "bank", "资产类型": "deposit_type", "续期方式": "renewal_option"}.get(mode)
            if field:
                data = distribution(self.assets, currency, field)
            elif mode == "到期本金":
                data = maturity_schedule(self.assets, currency)
            else:
                grouped = {}
                for a in self.assets:
                    if a['currency'] == currency and not a.get('deleted_at'):
                        month = a['deposit_date'][:7]
                        grouped[month] = grouped.get(month, Decimal(0)) + decimal(a['amount'])
                data = sorted(grouped.items())
            current.update(data=data, title=mode, currency=currency)
            panel = self.card(holder, mode)
            BarChart(panel, data, currency, self.privacy, min(max(len(data)*36, 220), 440), mode == '到期本金').pack(fill="x", padx=20, pady=(0, 16))
            label(panel, "图表展示前 6 项，完整数据在下表。" if len(data) > 6 and mode != '到期本金' else "所选币种独立统计。", 9, MUTED).pack(anchor="w", padx=20, pady=(0, 16))
            table = self.table(holder, [("name", "分组", 300), ("amount", "金额", 200), ("share", "占比", 120)], min(max(len(data), 2), 10))
            total = sum((v for _, v in data), Decimal(0))
            for name, value in data:
                table.insert("", "end", values=(name, self.fmt(value, currency), f"{value/total:.1%}" if total else "—"))
            label(holder, "月份统计按存入 / 建仓日汇总登记金额，包含已结清记录；它不代表历史净资产或收益曲线。" if mode == '存入月份' else "存款按本金、投资按登记市值统计，不折算汇率；投资没有固定利息。", 9, MUTED, wraplength=1000).pack(anchor="w", padx=12, pady=12)
        def save_chart():
            if self.privacy:
                messagebox.showinfo("金额隐藏中", "先显示金额，再导出包含完整数值的图表。", parent=self.root)
                return
            path = filedialog.asksaveasfilename(parent=self.root, title="导出矢量图表", defaultextension=".svg", filetypes=[("SVG 图表", "*.svg")])
            if path:
                export_svg(path, current['data'], current['title'], current['currency'])
                self.toast("图表已导出：" + path)
        button(row, "导出图表 SVG", save_chart).pack(side="right")
        choices.bind("<<ComboboxSelected>>", render)
        render()

    def assistant(self, parent):
        intro = self.card(parent, "让记录更轻松")
        label(intro, "一句话录入存款或投资，自动识别类型、机构、金额与日期。", 11, MUTED).pack(anchor="w", padx=20, pady=(0, 8))
        label(intro, "工行 5万元 三年 利率2.1% 2026-10-10 自动续期", 12, GREEN).pack(anchor="w", padx=20, pady=10)
        label(intro, '中信证券 股票 市值3万元 2026-10-10', 12, GREEN).pack(anchor='w', padx=20, pady=(0, 10))
        button(intro, "开始一句话录入 →", lambda: self.new_asset(True), True).pack(anchor="w", padx=20, pady=(8, 20))
        label(parent, "根据当前记录生成", 13, bold=True).pack(anchor="w", pady=(8, 16))
        for notice in insights(self.assets, self.reminder_days):
            card = self.card(parent, f"{notice['level']}  ·  {notice['title']}")
            label(card, notice['detail'], 10, MUTED, wraplength=920).pack(anchor="w", padx=20, pady=(0, 18))
            if notice['ids']:
                row = tk.Frame(card, bg=WHITE)
                row.pack(fill="x", padx=20, pady=(0, 15))
                for i, asset_id in enumerate(notice['ids'][:6]):
                    asset = self.store.get(asset_id)
                    button(row, asset['bank'] + " · " + asset['deposit_date'], lambda k=asset_id: self.detail(k)).grid(row=i//3, column=i%3, sticky='w', padx=(0, 8), pady=4)
                if len(notice['ids']) > 6:
                    label(card, f"另有 {len(notice['ids'])-6} 笔，可在资产明细中查看。", 9, MUTED).pack(anchor="w", padx=20, pady=(0, 12))
        label(parent, "助手使用可解释的本地规则，不调用外部模型，也不会上传资产数据。", 9, MUTED).pack(anchor="w", pady=12)

    def import_data(self):
        path = filedialog.askopenfilename(parent=self.root, title="导入资产记录", filetypes=[("资产数据", "*.csv *.json *.db *.sqlite *.sqlite3"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            preview = self.store.preview_import(path)
        except Exception as error:
            messagebox.showerror("无法读取", str(error), parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("导入预览")
        win.geometry("900x590")
        win.configure(bg=BG)
        win.transient(self.root)
        win.grab_set()
        label(win, "先核对，再导入", 20, bold=True).pack(anchor="w", padx=24, pady=20)
        label(win, f"有效新记录 {len(preview['valid'])} 笔   ·   疑似重复 {len(preview['duplicates'])} 笔   ·   无效 {len(preview['errors'])} 行", 11, GREEN).pack(anchor="w", padx=24, pady=(0, 18))
        tree = self.table(win, [("bank", "机构", 170), ("amount", "金额", 120), ("currency", "币种", 80), ("start", "存入日期", 130), ("end", "到期日", 130)], 6)
        for a in preview['valid'][:200]:
            tree.insert("", "end", values=(a['bank'], self.fmt(a['amount'], a['currency']), a['currency'], a['deposit_date'], a['maturity_date']))
        if len(preview['valid']) > 200:
            label(win, "预览显示前 200 笔，确认后导入全部有效记录。", 9, MUTED).pack(anchor="w", padx=24)
        include = tk.BooleanVar(value=False)
        ttk.Checkbutton(win, text="确认疑似重复为独立资产，仍然导入这些记录", variable=include).pack(anchor="w", padx=24, pady=12)
        if preview['errors']:
            button(win, "查看无效行的原因", lambda: self.text_window("无效记录", "\n".join(preview['errors']))).pack(anchor="w", padx=24, pady=6)
        label(win, "无效行不会导入；导入前会自动创建当前数据库快照。", 9, MUTED).pack(anchor="w", padx=24, pady=10)
        def confirm():
            try:
                count = self.store.import_preview(preview, include.get())
                self.refresh()
                self.toast(f"已导入 {count} 笔记录")
                win.destroy()
            except Exception as error:
                messagebox.showerror("导入未完成", str(error), parent=win)
        button(win, "确认导入", confirm, True).pack(anchor="e", padx=24, pady=16)

    def export(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="导出当前筛选记录", defaultextension=".xlsx", filetypes=[("Excel 工作簿", "*.xlsx"), ("CSV 数据", "*.csv"), ("JSON 完整记录", "*.json")])
        if not path:
            return
        try:
            suffix = Path(path).suffix.lower()
            if suffix == '.xlsx':
                export_xlsx(path, self.filtered)
            elif suffix == '.json':
                self.store.export_json(path, self.filtered)
            elif suffix == '.csv':
                self.store.export_csv(path, self.filtered)
            else:
                raise ValueError("请使用 .xlsx、.csv 或 .json 后缀")
            self.toast(f"已导出 {len(self.filtered)} 笔记录：{path}")
        except Exception as error:
            messagebox.showerror("导出未完成", str(error), parent=self.root)

    def backups(self, parent):
        info = self.card(parent, "备份你的资产记录")
        label(info, "原有 backups 文件始终保留；新版快照使用 assetbook_ 前缀。", 11, MUTED).pack(anchor="w", padx=20, pady=(0, 12))
        row = tk.Frame(info, bg=WHITE)
        row.pack(fill="x", padx=20, pady=(0, 20))
        def backup():
            path = self.store.backup()
            self.refresh()
            self.toast("快照已保存：" + path.name)
        button(row, "立即备份", backup, True).pack(side="left")
        button(row, "从文件恢复…", self.restore_backup).pack(side="left", padx=12)
        def open_folder():
            folder = self.store.base / 'backups'
            folder.mkdir(exist_ok=True)
            os.startfile(str(folder))
        button(row, "打开备份目录", open_folder).pack(side="left")
        files = sorted((self.store.base / 'backups').glob('*.db'), key=lambda p: p.stat().st_mtime, reverse=True)
        card = self.card(parent, f"数据库快照  /  {len(files)} 个")
        tree = self.table(card, [("name", "文件", 480), ("size", "大小", 120), ("kind", "类型", 140)], min(max(len(files), 4), 12))
        mapping = {}
        for i, path in enumerate(files):
            key = str(i)
            mapping[key] = path
            tree.insert('', 'end', iid=key, values=(path.name, f"{path.stat().st_size/1024:.1f} KB", '新版完整快照' if path.name.startswith('assetbook_') else '旧版存款备份'))
        def restore_selected():
            if tree.selection():
                self.restore_backup(mapping[tree.selection()[0]])
        button(card, "恢复选中的快照", restore_selected).pack(anchor="e", padx=20, pady=16)
        label(parent, "恢复会替换当前账本；系统先验证文件，再备份现有数据。旧版 .db 可直接恢复为新版账本。", 9, MUTED, wraplength=1000).pack(anchor="w", pady=12)

    def restore_backup(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(parent=self.root, initialdir=self.store.base / 'backups', title="选择数据库备份", filetypes=[("数据库备份", "*.db *.sqlite *.sqlite3")])
        if not path:
            return
        try:
            count = len(read_records(Path(path)))
        except Exception as error:
            messagebox.showerror("备份不可用", str(error), parent=self.root)
            return
        if messagebox.askyesno("确认恢复", f"使用 {Path(path).name} 替换当前账本？\n备份中有 {count} 笔记录。\n当前数据会先保存为 before_restore 快照。", parent=self.root):
            try:
                safety = self.store.restore(path)
                self.reminder_days = int(self.store.setting('reminder_days', 30))
                self.refresh()
                self.toast("已恢复；恢复前快照：" + safety.name)
            except Exception as error:
                messagebox.showerror("恢复未完成", str(error), parent=self.root)

    def settings(self, parent):
        card = self.card(parent, "提醒与备份")
        row = tk.Frame(card, bg=WHITE)
        row.pack(fill="x", padx=20, pady=12)
        label(row, "提前提醒天数", 11, width=18).pack(side="left")
        reminder = tk.StringVar(value=str(self.reminder_days))
        ttk.Spinbox(row, from_=1, to=365, textvariable=reminder, width=10).pack(side="left")
        auto = tk.BooleanVar(value=self.store.setting('auto_backup', True))
        ttk.Checkbutton(card, text="每天首次打开时自动创建快照", variable=auto).pack(anchor="w", padx=20, pady=12)
        def save():
            try:
                days = int(reminder.get())
                if not 1 <= days <= 365:
                    raise ValueError("提前提醒天数应在 1–365 之间")
                self.store.set_setting('reminder_days', days)
                self.store.set_setting('auto_backup', auto.get())
                self.reminder_days = days
                self.toast("设置已保存")
            except ValueError as error:
                messagebox.showerror("检查设置", str(error), parent=self.root)
        button(card, "保存设置", save, True).pack(anchor="w", padx=20, pady=18)
        details = self.card(parent, "账本信息")
        for text in [f"版本：{__version__}", f"程序与数据目录：{self.store.base}", f"数据库：{self.store.path.name}",
                     "数据和处理均在本机，不需要联网。", "金额隐藏仅影响界面显示；导出的文件保留完整数据。", "账号默认只显示最后四位；原始导入记录和 JSON 导出保留完整内容。",
                     '股票、基金、ETF 与其他投资按手动登记市值统计，没有到期日期、固定年利率或续期方式。',
                     "到期状态随日期计算，自动续期选项记录银行约定，实际续期需要你核对后登记。",
                     "单利估算：完整期限按月数/12，否则按实际天数/365；不包含税费、提前支取规则或产品复利。"]:
            label(details, text, 10, MUTED, wraplength=960).pack(anchor="w", padx=20, pady=6)
        button(details, "查看操作历史", self.show_history).pack(anchor="w", padx=20, pady=18)
        button(details, "导入 CSV / JSON / 数据库", self.import_data).pack(anchor="w", padx=20, pady=(0, 18))

    def show_history(self):
        text = '\n\n'.join(self.history_text(h) for h in self.store.history())
        self.text_window("操作历史 · 最近 1000 条", text or "暂无操作历史")

    def history_text(self, history):
        text = f"{history['created_at']}  {history['action']}  {history['asset_id'] or '账本'}"
        for key, name in [('before_json', '之前'), ('after_json', '之后')]:
            if not history[key]:
                continue
            data = json.loads(history[key])
            if data.get('amount') is not None:
                text += f"\n  {name}：{data.get('bank', '')}  {self.fmt(data['amount'], data.get('currency', '人民币'))}  "
                text += f"{data.get('deposit_date', '')}  {data.get('deposit_type', '')} · 登记市值" if is_investment(data) else f"{data.get('deposit_date', '')} → {data.get('maturity_date') or '无到期日'}  利率 {data.get('interest_rate') if data.get('interest_rate') is not None else '未填写'}%"
            elif data.get('file'):
                text += f"\n  {name}：{data['file']}"
        return text

    def text_window(self, title, content):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry('720x660')
        win.configure(bg=BG)
        win.transient(self.root)
        label(win, title, 17, bold=True).pack(anchor='w', padx=20, pady=18)
        frame = tk.Frame(win, bg=WHITE)
        frame.pack(fill='both', expand=True, padx=20)
        text = tk.Text(frame, wrap='word', font=(FONT, 10), bg=WHITE, fg=INK, relief='flat', padx=16, pady=16)
        bar = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        text.insert('1.0', content)
        text.configure(state='disabled')
        button(win, '关闭', win.destroy).pack(anchor='e', padx=20, pady=12)
        return win
