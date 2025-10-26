import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.dates as mdates
import requests
import tkinter as tk
from tkinter import messagebox, ttk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from ttkbootstrap import Style


POPULAR_SYMBOLS = [
    "SEV",
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "TSLA",
    "META",
    "NVDA",
    "NFLX",
    "AMD",
    "INTC",
    "SHOP",
    "BABA",
    "DIS",
    "UBER",
    "BA",
]

REFRESH_INTERVAL_OPTIONS = {
    "5s": 5_000,
    "30s": 30_000,
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
}
DEFAULT_REFRESH_LABEL = "1m"
MAX_HISTORY_POINTS = 240
DEFAULT_THEME = "Light"
THEME_PRESETS = {
    "Light": {
        "bootstrap_theme": "flatly",
        "bg": "#f3f4f6",
        "figure_bg": "#f3f4f6",
        "chart_bg": "#ffffff",
        "line": "#2563eb",
        "fill": "#3b82f6",
        "accent": "#dc2626",
        "grid": "#d1d5db",
        "text": "#0f172a",
        "status_fg": "#1f2937",
        "tree_bg": "#ffffff",
        "tree_field_bg": "#ffffff",
        "tree_alt_bg": "#f8fafc",
        "tree_selected_bg": "#2563eb",
        "tree_selected_fg": "#ffffff",
        "tree_border": "#e5e7eb",
        "muted_text": "#64748b",
        "summary_bg": "#ffffff",
        "summary_border": "#e2e8f0",
    },
    "Dark": {
        "bootstrap_theme": "darkly",
        "bg": "#0f172a",
        "figure_bg": "#0f172a",
        "chart_bg": "#111827",
        "line": "#60a5fa",
        "fill": "#2563eb",
        "accent": "#f87171",
        "grid": "#1f2937",
        "text": "#e5e7eb",
        "status_fg": "#e5e7eb",
        "tree_bg": "#111827",
        "tree_field_bg": "#111827",
        "tree_alt_bg": "#1b2335",
        "tree_selected_bg": "#2563eb",
        "tree_selected_fg": "#f8fafc",
        "tree_border": "#1f2937",
        "muted_text": "#94a3b8",
        "summary_bg": "#111827",
        "summary_border": "#1f2937",
    },
    "Nord": {
        "bootstrap_theme": "minty",
        "bg": "#e5e9f0",
        "figure_bg": "#e5e9f0",
        "chart_bg": "#f1f5fb",
        "line": "#5e81ac",
        "fill": "#81a1c1",
        "accent": "#bf616a",
        "grid": "#cbd5e1",
        "text": "#2e3440",
        "status_fg": "#2e3440",
        "tree_bg": "#ffffff",
        "tree_field_bg": "#ffffff",
        "tree_alt_bg": "#eef2f8",
        "tree_selected_bg": "#5e81ac",
        "tree_selected_fg": "#f8fafc",
        "tree_border": "#d8dee9",
        "muted_text": "#4c566a",
        "summary_bg": "#ffffff",
        "summary_border": "#d8dee9",
    },
}
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
REQUEST_TIMEOUT = 10
DB_PATH = Path(__file__).with_name("holdings.db")


@dataclass
class Holding:
    symbol: str
    shares: float
    price: Optional[float] = None
    last_updated: Optional[float] = None
    error: Optional[str] = None

    @property
    def value(self) -> Optional[float]:
        if self.price is None:
            return None
        return self.price * self.shares


class TickerApp:
    def __init__(self, root: tk.Tk, style: Style) -> None:
        self.root = root
        self.root.title("Ticker With Price")
        self.root.geometry("520x600")
        self.root.attributes("-topmost", True)
        self.root.configure(padx=16, pady=16)

        self.style = style
        self.theme_name = DEFAULT_THEME
        self.colors = THEME_PRESETS[self.theme_name]

        self.holdings: Dict[str, Holding] = {}
        self.price_history: Dict[str, List[Tuple[datetime, float]]] = {}
        self.conn: Optional[sqlite3.Connection] = None
        self.refresh_lock = threading.Lock()
        self.refresh_job: Optional[str] = None
        self.refresh_interval_label = DEFAULT_REFRESH_LABEL
        self.refresh_interval_ms = REFRESH_INTERVAL_OPTIONS[self.refresh_interval_label]
        self.current_chart_symbol: Optional[str] = None

        self._build_widgets()
        self._apply_theme(self.theme_name)
        self._init_db()
        self._load_holdings()
        self._schedule_refresh()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_widgets(self) -> None:
        self.header_frame = ttk.Frame(self.root)
        self.header_frame.pack(fill="x", pady=(0, 12))

        self.headline_label = ttk.Label(
            self.header_frame,
            text="Ticker With Price",
            style="Headline.TLabel",
        )
        self.headline_label.pack(anchor="w")

        self.subheading_label = ttk.Label(
            self.header_frame,
            text="Track your watchlist with a clean, always-on-top dashboard.",
            style="Subheading.TLabel",
        )
        self.subheading_label.pack(anchor="w", pady=(2, 0))

        self.summary_frame = ttk.Frame(self.root, padding=12, style="Summary.TFrame")
        self.summary_frame.pack(fill="x", pady=(0, 16))
        self.summary_frame.columnconfigure(0, weight=1)
        self.summary_frame.columnconfigure(1, weight=1)

        self.summary_value_var = tk.StringVar(value="$0.00")
        self.summary_holdings_var = tk.StringVar(value="No holdings yet")
        self.summary_last_updated_var = tk.StringVar(value="Waiting for first update")

        self.summary_value_label = ttk.Label(
            self.summary_frame,
            textvariable=self.summary_value_var,
            style="SummaryValue.TLabel",
        )
        self.summary_value_label.grid(row=0, column=0, rowspan=2, sticky="w")

        self.summary_holdings_label = ttk.Label(
            self.summary_frame,
            textvariable=self.summary_holdings_var,
            style="SummaryPrimary.TLabel",
        )
        self.summary_holdings_label.grid(row=0, column=1, sticky="e")

        self.summary_last_updated_label = ttk.Label(
            self.summary_frame,
            textvariable=self.summary_last_updated_var,
            style="SummarySecondary.TLabel",
        )
        self.summary_last_updated_label.grid(row=1, column=1, sticky="e", pady=(4, 0))

        ttk.Separator(self.root, orient="horizontal").pack(fill="x", pady=(0, 12))

        self.controls_frame = ttk.Frame(self.root)
        self.controls_frame.pack(fill="x", pady=(0, 12))

        symbol_label = ttk.Label(self.controls_frame, text="Symbol", style="AppCaption.TLabel")
        symbol_label.grid(row=0, column=0, sticky="w")

        self.symbol_var = tk.StringVar()
        self.symbol_combo = ttk.Combobox(
            self.controls_frame,
            textvariable=self.symbol_var,
            values=POPULAR_SYMBOLS,
            width=12,
            state="normal",
        )
        self.symbol_combo.grid(row=1, column=0, padx=(0, 8), sticky="ew")

        shares_label = ttk.Label(self.controls_frame, text="Shares", style="AppCaption.TLabel")
        shares_label.grid(row=0, column=1, sticky="w")

        self.shares_var = tk.StringVar()
        shares_entry = ttk.Entry(self.controls_frame, textvariable=self.shares_var, width=10)
        shares_entry.grid(row=1, column=1, padx=(0, 8), sticky="ew")

        self.add_button = ttk.Button(
            self.controls_frame,
            text="Add / Update",
            command=self.add_holding,
            style="PrimaryButton.TButton",
        )
        self.add_button.grid(row=1, column=2, sticky="ew")

        refresh_label = ttk.Label(self.controls_frame, text="Refresh", style="AppCaption.TLabel")
        refresh_label.grid(row=0, column=3, sticky="w")

        self.interval_var = tk.StringVar(value=self.refresh_interval_label)
        interval_combo = ttk.Combobox(
            self.controls_frame,
            textvariable=self.interval_var,
            values=list(REFRESH_INTERVAL_OPTIONS.keys()),
            width=6,
            state="readonly",
        )
        interval_combo.grid(row=1, column=3, padx=(8, 0), sticky="ew")
        interval_combo.bind("<<ComboboxSelected>>", self._on_interval_change)

        theme_label = ttk.Label(self.controls_frame, text="Theme", style="AppCaption.TLabel")
        theme_label.grid(row=0, column=4, sticky="w")

        self.theme_var = tk.StringVar(value=self.theme_name)
        theme_combo = ttk.Combobox(
            self.controls_frame,
            textvariable=self.theme_var,
            values=list(THEME_PRESETS.keys()),
            width=10,
            state="readonly",
        )
        theme_combo.grid(row=1, column=4, padx=(8, 0), sticky="ew")
        theme_combo.bind("<<ComboboxSelected>>", self._on_theme_change)

        self.controls_frame.columnconfigure(0, weight=2)
        self.controls_frame.columnconfigure(1, weight=1)
        self.controls_frame.columnconfigure(2, weight=1)
        self.controls_frame.columnconfigure(3, weight=1)
        self.controls_frame.columnconfigure(4, weight=1)

        self.tree = ttk.Treeview(
            self.root,
            columns=("symbol", "shares", "price", "value", "updated"),
            show="headings",
            height=9,
        )
        self.tree.heading("symbol", text="Symbol")
        self.tree.heading("shares", text="Shares")
        self.tree.heading("price", text="Price")
        self.tree.heading("value", text="Value")
        self.tree.heading("updated", text="Updated")
        self.tree.column("symbol", anchor="w", width=90)
        self.tree.column("shares", anchor="e", width=80)
        self.tree.column("price", anchor="e", width=80)
        self.tree.column("value", anchor="e", width=80)
        self.tree.column("updated", anchor="w", width=120)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        self.chart_frame = ttk.LabelFrame(self.root, text="Price History", style="Card.TLabelframe")
        self.chart_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.chart_frame.configure(padding=12)

        self.figure = Figure(figsize=(4.6, 2.8), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self._prepare_axes("No data yet")

        self.canvas = FigureCanvasTkAgg(self.figure, master=self.chart_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(fill="both", expand=True)
        self.canvas.draw_idle()

        self.actions_frame = ttk.Frame(self.root)
        self.actions_frame.pack(fill="x", pady=(12, 0))

        self.remove_button = ttk.Button(
            self.actions_frame,
            text="Remove Selected",
            command=self.remove_selected,
            style="DangerButton.TButton",
        )
        self.remove_button.pack(side="left")

        self.refresh_button = ttk.Button(
            self.actions_frame,
            text="Refresh Now",
            command=self.refresh_all_holdings,
            style="GhostButton.TButton",
        )
        self.refresh_button.pack(side="right")

        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(self.root, textvariable=self.status_var, anchor="w", style="Status.TLabel")
        self.status_label.pack(fill="x", pady=(8, 0))

        self._update_summary()

    def add_holding(self) -> None:
        symbol = self.symbol_var.get().strip().upper()
        if not symbol:
            messagebox.showinfo("Missing Symbol", "Please enter a stock symbol.")
            return

        try:
            shares = float(self.shares_var.get())
        except ValueError:
            messagebox.showinfo("Invalid Shares", "Shares must be a numeric value.")
            return

        if shares <= 0:
            messagebox.showinfo("Invalid Shares", "Shares must be greater than zero.")
            return

        holding = self.holdings.get(symbol)
        if holding:
            holding.shares = shares
            holding.error = None
        else:
            holding = Holding(symbol=symbol, shares=shares)
            self.holdings[symbol] = holding
            self.price_history.setdefault(symbol, [])

        self._save_holding(holding)
        self.symbol_var.set("")
        self.shares_var.set("")
        self.update_tree()
        self.refresh_holding(symbol)
        self.tree.selection_set(symbol)
        self._update_chart(symbol)
        self.status_var.set(f"Tracking {shares:g} shares of {symbol}.")

    def remove_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return

        for symbol in selection:
            self.holdings.pop(symbol, None)
            self.price_history.pop(symbol, None)
            self.tree.delete(symbol)
            self._delete_holding(symbol)

        self.status_var.set("Removed selected holding(s).")
        remaining = self.tree.get_children()
        if remaining:
            next_symbol = remaining[0]
            self.tree.selection_set(next_symbol)
            self._update_chart(next_symbol)
        else:
            self._clear_chart()

    def refresh_all_holdings(self) -> None:
        symbols = list(self.holdings.keys())
        if not symbols:
            self.status_var.set("No holdings to refresh.")
            return

        self.status_var.set(f"Refreshing {len(symbols)} holding(s)…")
        for symbol in symbols:
            self.refresh_holding(symbol, silent=True)

    def refresh_holding(self, symbol: str, silent: bool = False) -> None:
        holding = self.holdings.get(symbol)
        if not holding:
            return

        def task() -> None:
            try:
                price = self.fetch_price(symbol)
            except Exception as exc:  # noqa: BLE001
                holding.error = str(exc)
                holding.price = None
                holding.last_updated = time.time()
                self.root.after(0, self.update_tree)
                if not silent:
                    self.root.after(0, lambda: self.status_var.set(f"{symbol}: {holding.error}"))
                if self.tree.selection() and self.tree.selection()[0] == symbol:
                    self.root.after(0, lambda: self._update_chart(symbol))
                return

            holding.price = price
            holding.last_updated = time.time()
            holding.error = None
            self._record_price(symbol, price)
            self.root.after(0, self.update_tree)
            if self.tree.selection() and self.tree.selection()[0] == symbol:
                self.root.after(0, lambda: self._update_chart(symbol))
            if not silent:
                self.root.after(0, lambda: self.status_var.set(f"{symbol} updated to ${price:.2f}"))

        threading.Thread(target=task, daemon=True).start()

    def fetch_price(self, symbol: str) -> float:
        with self.refresh_lock:
            response = requests.get(
                YAHOO_CHART_URL.format(symbol=symbol),
                params={"interval": "1m", "range": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=REQUEST_TIMEOUT,
            )

        if response.status_code != 200:
            raise RuntimeError(f"Yahoo request failed ({response.status_code})")

        payload = response.json()
        chart = payload.get("chart", {})
        error = chart.get("error")
        if error:
            description = error.get("description", "Unknown error")
            raise RuntimeError(description)

        results = chart.get("result")
        if not results:
            raise RuntimeError("No quote data returned")

        meta = results[0].get("meta", {})
        price = meta.get("regularMarketPrice")
        if price is None:
            raise RuntimeError("Price unavailable")

        return float(price)

    def update_tree(self) -> None:
        existing_items = set(self.tree.get_children())
        for symbol in list(self.holdings.keys()):
            holding = self.holdings[symbol]
            item_id = self._ensure_tree_item(symbol)

            price_text = self._format_currency(holding.price)
            value_text = self._format_currency(holding.value)
            updated_text = self._format_updated(holding)

            self.tree.item(
                item_id,
                values=(
                    symbol,
                    f"{holding.shares:g}",
                    price_text,
                    value_text,
                    updated_text,
                ),
            )
            existing_items.discard(item_id)

        for orphan in existing_items:
            self.tree.delete(orphan)

    def _ensure_tree_item(self, symbol: str) -> str:
        if self.tree.exists(symbol):
            return symbol
        return self.tree.insert("", "end", iid=symbol, values=(symbol, "", "", "", ""))

    def _format_currency(self, value: Optional[float]) -> str:
        if value is None:
            return "—"
        return f"${value:,.2f}"

    def _format_updated(self, holding: Holding) -> str:
        if holding.error:
            return f"Error: {holding.error}"
        if holding.last_updated is None:
            return "Pending"
        elapsed = time.time() - holding.last_updated
        if elapsed < 60:
            return f"{int(elapsed)}s ago"
        minutes = int(elapsed // 60)
        if minutes < 60:
            return f"{minutes}m ago"
        hours = int(minutes // 60)
        return f"{hours}h ago"

    def _update_summary(self) -> None:
        if not hasattr(self, "summary_value_var"):
            return

        count = len(self.holdings)
        total_shares = sum(holding.shares for holding in self.holdings.values())
        priced = [holding for holding in self.holdings.values() if holding.value is not None]
        total_value = sum(holding.value for holding in priced if holding.value is not None)

        if count == 0:
            holdings_text = "No holdings yet"
        else:
            shares_text = f"{total_shares:g} share{'s' if total_shares != 1 else ''}"
            holdings_text = f"{count} holding{'s' if count != 1 else ''} • {shares_text}"
        self.summary_holdings_var.set(holdings_text)

        if priced:
            self.summary_value_var.set(f"${total_value:,.2f}")
            latest_timestamp = max(holding.last_updated or 0 for holding in priced)
            self.summary_last_updated_var.set(self._humanize_elapsed(latest_timestamp))
        else:
            self.summary_value_var.set("—")
            if count == 0:
                self.summary_last_updated_var.set("Waiting for first update")
            else:
                self.summary_last_updated_var.set("Waiting on pricing data")

    def _schedule_refresh(self) -> None:
        if self.refresh_job:
            self.root.after_cancel(self.refresh_job)
        self.refresh_job = self.root.after(self.refresh_interval_ms, self._on_refresh_timer)

    def _on_refresh_timer(self) -> None:
        if self.holdings:
            self.refresh_all_holdings()
        self._schedule_refresh()

    def _init_db(self) -> None:
        self.conn = sqlite3.connect(DB_PATH)
        with self.conn:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS holdings (symbol TEXT PRIMARY KEY, shares REAL NOT NULL)"
            )

    def _load_holdings(self) -> None:
        if not self.conn:
            return

        cursor = self.conn.execute("SELECT symbol, shares FROM holdings ORDER BY symbol")
        for symbol, shares in cursor.fetchall():
            self.holdings[symbol] = Holding(symbol=symbol, shares=float(shares))
            self.price_history.setdefault(symbol, [])

        if self.holdings:
            self.update_tree()
            first_symbol = next(iter(self.holdings))
            self.tree.selection_set(first_symbol)
            self.current_chart_symbol = first_symbol
            self._update_chart(first_symbol)
            for symbol in self.holdings:
                self.refresh_holding(symbol, silent=True)
            self.status_var.set(f"Loaded {len(self.holdings)} holding(s) from disk.")

    def _save_holding(self, holding: Holding) -> None:
        if not self.conn:
            return
        with self.conn:
            self.conn.execute(
                "INSERT INTO holdings (symbol, shares) VALUES (?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET shares=excluded.shares",
                (holding.symbol, holding.shares),
            )

    def _delete_holding(self, symbol: str) -> None:
        if not self.conn:
            return
        with self.conn:
            self.conn.execute("DELETE FROM holdings WHERE symbol = ?", (symbol,))

    def _apply_theme(self, theme_name: str) -> None:
        preset = THEME_PRESETS.get(theme_name, THEME_PRESETS[DEFAULT_THEME])
        self.theme_name = theme_name
        self.colors = preset

        self.style.theme_use(preset["bootstrap_theme"])

        self.root.configure(bg=preset["bg"])
        self.canvas_widget.configure(
            background=preset["figure_bg"],
            highlightthickness=0,
            borderwidth=0,
        )

        self.style.configure(
            "Status.TLabel",
            background=preset["bg"],
            foreground=preset["status_fg"],
            padding=(6, 4),
        )
        self.style.configure(
            "Card.TLabelframe",
            background=preset["bg"],
            foreground=preset["text"],
            borderwidth=0,
            relief="flat",
        )
        self.style.configure(
            "Card.TLabelframe.Label",
            background=preset["bg"],
            foreground=preset["text"],
            font=("Helvetica", 10, "bold"),
        )
        self.style.configure(
            "Treeview",
            background=preset["tree_bg"],
            fieldbackground=preset["tree_field_bg"],
            foreground=preset["text"],
            rowheight=26,
            borderwidth=0,
        )
        self.style.map(
            "Treeview",
            background=[("selected", preset["tree_selected_bg"])],
            foreground=[("selected", preset["tree_selected_fg"])],
        )
        self.style.configure(
            "Treeview.Heading",
            background=preset["tree_bg"],
            foreground=preset["text"],
            font=("Helvetica", 10, "bold"),
            borderwidth=0,
        )
        self.style.configure("TButton", padding=6)
        self.style.configure("TCombobox", padding=4)

        self.chart_frame.configure(style="Card.TLabelframe")
        self.status_label.configure(style="Status.TLabel")
        if hasattr(self, "theme_var"):
            self.theme_var.set(theme_name)

        for frame in (self.root,):
            frame.configure(bg=preset["bg"])

        self.figure.patch.set_facecolor(preset["figure_bg"])
        self.ax.set_facecolor(preset["chart_bg"])

        current_symbol = self.current_chart_symbol
        if current_symbol:
            self._update_chart(current_symbol)
        else:
            self._prepare_axes("No data yet")
            self.canvas.draw_idle()

    def _record_price(self, symbol: str, price: float) -> None:
        history = self.price_history.setdefault(symbol, [])
        history.append((datetime.now(), price))
        if len(history) > MAX_HISTORY_POINTS:
            del history[: len(history) - MAX_HISTORY_POINTS]

    def _on_interval_change(self, event: tk.Event) -> None:  # type: ignore[override]
        label = self.interval_var.get()
        interval = REFRESH_INTERVAL_OPTIONS.get(label)
        if not interval:
            return
        self.refresh_interval_label = label
        self.refresh_interval_ms = interval
        self._schedule_refresh()
        self.status_var.set(f"Refresh rate set to {label}.")

    def _on_theme_change(self, event: tk.Event) -> None:  # type: ignore[override]
        theme = self.theme_var.get()
        if theme == self.theme_name:
            return
        self._apply_theme(theme)
        self.status_var.set(f"{theme} theme applied.")

    def _on_tree_select(self, event: tk.Event) -> None:  # type: ignore[override]
        selection = self.tree.selection()
        if not selection:
            return
        symbol = selection[0]
        self._update_chart(symbol)

    def _update_chart(self, symbol: Optional[str]) -> None:
        if not symbol:
            self._clear_chart()
            return

        history = self.price_history.get(symbol)
        if not history:
            self._clear_chart(title=f"{symbol} (no data yet)")
            self.current_chart_symbol = symbol
            return

        palette = self.colors
        times, prices = zip(*history)
        self.ax.clear()
        self._prepare_axes(symbol)

        self.ax.plot(
            times,
            prices,
            color=palette["line"],
            linewidth=2.0,
            marker="o",
            markersize=3.5,
            markerfacecolor=palette["chart_bg"],
            markeredgecolor=palette["line"],
            markeredgewidth=0.8,
        )

        price_min = min(prices)
        price_max = max(prices)
        price_span = max(price_max - price_min, 1e-6)
        padding = price_span * 0.12
        baseline = price_min - padding
        if len(prices) > 1:
            self.ax.fill_between(
                times,
                prices,
                baseline,
                color=palette["fill"],
                alpha=0.18,
                linewidth=0,
            )
        self.ax.set_ylim(price_min - padding, price_max + padding)

        locator = mdates.AutoDateLocator(minticks=3, maxticks=6)
        formatter = mdates.ConciseDateFormatter(locator)
        self.ax.xaxis.set_major_locator(locator)
        self.ax.xaxis.set_major_formatter(formatter)
        self.figure.autofmt_xdate(rotation=25, ha="center")

        last_time = times[-1]
        last_price = prices[-1]
        self.ax.scatter([last_time], [last_price], color=palette["accent"], s=26, zorder=5)
        self.ax.annotate(
            f"${last_price:,.2f}",
            xy=(last_time, last_price),
            xytext=(0, 10),
            textcoords="offset points",
            fontsize=8,
            color=palette["accent"],
            bbox=dict(
                boxstyle="round,pad=0.3",
                facecolor=palette["chart_bg"],
                edgecolor=palette["accent"],
                linewidth=0.8,
                alpha=0.85,
            ),
            ha="center",
        )

        self.canvas.draw_idle()
        self.current_chart_symbol = symbol

    def _clear_chart(self, title: str = "No data yet") -> None:
        self.ax.clear()
        self._prepare_axes(title)
        self.canvas.draw_idle()
        self.current_chart_symbol = None

    def _prepare_axes(self, title: str) -> None:
        palette = self.colors
        self.ax.set_title(title, fontsize=11, pad=10, color=palette["text"])
        self.ax.set_xlabel("Time", fontsize=9, color=palette["text"])
        self.ax.set_ylabel("Price", fontsize=9, color=palette["text"])
        self.ax.tick_params(axis="both", labelsize=8, colors=palette["text"])
        self.ax.grid(True, which="major", color=palette["grid"], linestyle="--", linewidth=0.6)
        self.ax.set_facecolor(palette["chart_bg"])
        self.figure.patch.set_facecolor(palette["figure_bg"])
        for spine in self.ax.spines.values():
            spine.set_color(palette["grid"])
        self.ax.spines["top"].set_alpha(0.0)
        self.ax.spines["right"].set_alpha(0.0)

    def _humanize_elapsed(self, timestamp: float) -> str:
        if not timestamp:
            return "Waiting for first update"
        delta = max(0.0, time.time() - timestamp)
        if delta < 1:
            return "Updated moments ago"
        if delta < 60:
            return f"Updated {int(delta)}s ago"
        minutes = delta / 60
        if minutes < 60:
            return f"Updated {int(minutes)}m ago"
        hours = minutes / 60
        if hours < 24:
            return f"Updated {int(hours)}h ago"
        days = hours / 24
        return f"Updated {int(days)}d ago"

    def _shade_color(self, hex_color: str, factor: float) -> str:
        if not hex_color:
            return hex_color
        color = hex_color.lstrip("#")
        if len(color) != 6 or factor == 1:
            return f"#{color}" if not hex_color.startswith("#") else hex_color
        factor = max(0.0, min(2.0, factor))
        rgb = [int(color[i : i + 2], 16) for i in range(0, 6, 2)]
        adjusted = []
        for value in rgb:
            if factor < 1:
                new_value = int(value * factor)
            else:
                new_value = int(value + (255 - value) * (factor - 1))
            adjusted.append(max(0, min(255, new_value)))
        return "#{:02x}{:02x}{:02x}".format(*adjusted)

    def _on_close(self) -> None:
        if self.refresh_job:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None
        if self.conn:
            self.conn.close()
            self.conn = None
        self.root.destroy()


def main() -> None:
    style = Style(theme=THEME_PRESETS[DEFAULT_THEME]["bootstrap_theme"])
    root = style.master
    app = TickerApp(root, style)
    root.mainloop()


if __name__ == "__main__":
    main()
