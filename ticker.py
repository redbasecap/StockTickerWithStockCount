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
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Ticker With Price")
        self.root.geometry("480x560")
        self.root.attributes("-topmost", True)
        self.root.configure(padx=12, pady=12)

        self.holdings: Dict[str, Holding] = {}
        self.price_history: Dict[str, List[Tuple[datetime, float]]] = {}
        self.conn: Optional[sqlite3.Connection] = None
        self.refresh_lock = threading.Lock()
        self.refresh_job: Optional[str] = None
        self.refresh_interval_label = DEFAULT_REFRESH_LABEL
        self.refresh_interval_ms = REFRESH_INTERVAL_OPTIONS[self.refresh_interval_label]
        self.current_chart_symbol: Optional[str] = None

        self._build_widgets()
        self._init_db()
        self._load_holdings()
        self._schedule_refresh()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_widgets(self) -> None:
        controls = ttk.Frame(self.root)
        controls.pack(fill="x", pady=(0, 8))

        symbol_label = ttk.Label(controls, text="Symbol")
        symbol_label.grid(row=0, column=0, sticky="w")

        self.symbol_var = tk.StringVar()
        self.symbol_combo = ttk.Combobox(
            controls,
            textvariable=self.symbol_var,
            values=POPULAR_SYMBOLS,
            width=12,
            state="normal",
        )
        self.symbol_combo.grid(row=1, column=0, padx=(0, 8), sticky="ew")

        shares_label = ttk.Label(controls, text="Shares")
        shares_label.grid(row=0, column=1, sticky="w")

        self.shares_var = tk.StringVar()
        shares_entry = ttk.Entry(controls, textvariable=self.shares_var, width=10)
        shares_entry.grid(row=1, column=1, padx=(0, 8), sticky="ew")

        add_button = ttk.Button(controls, text="Add / Update", command=self.add_holding)
        add_button.grid(row=1, column=2, sticky="ew")

        refresh_label = ttk.Label(controls, text="Refresh")
        refresh_label.grid(row=0, column=3, sticky="w")

        self.interval_var = tk.StringVar(value=self.refresh_interval_label)
        interval_combo = ttk.Combobox(
            controls,
            textvariable=self.interval_var,
            values=list(REFRESH_INTERVAL_OPTIONS.keys()),
            width=6,
            state="readonly",
        )
        interval_combo.grid(row=1, column=3, padx=(8, 0), sticky="ew")
        interval_combo.bind("<<ComboboxSelected>>", self._on_interval_change)

        controls.columnconfigure(0, weight=2)
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(2, weight=1)
        controls.columnconfigure(3, weight=1)

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

        chart_frame = ttk.LabelFrame(self.root, text="Price History")
        chart_frame.pack(fill="both", expand=True, pady=(8, 0))

        self.figure = Figure(figsize=(4.4, 2.6), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self._prepare_axes("No data yet")

        self.canvas = FigureCanvasTkAgg(self.figure, master=chart_frame)
        self.canvas.draw_idle()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        actions = ttk.Frame(self.root)
        actions.pack(fill="x", pady=(8, 0))

        remove_button = ttk.Button(actions, text="Remove Selected", command=self.remove_selected)
        remove_button.pack(side="left")

        refresh_button = ttk.Button(actions, text="Refresh Now", command=self.refresh_all_holdings)
        refresh_button.pack(side="right")

        self.status_var = tk.StringVar(value="Ready")
        status_label = ttk.Label(self.root, textvariable=self.status_var, anchor="w")
        status_label.pack(fill="x", pady=(4, 0))

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

        times, prices = zip(*history)
        self.ax.clear()
        self._prepare_axes(symbol)

        self.ax.plot(
            times,
            prices,
            color="#1f6feb",
            linewidth=1.8,
            marker="o",
            markersize=3,
            markerfacecolor="#ffffff",
            markeredgewidth=0.6,
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
                color="#1f6feb",
                alpha=0.14,
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
        self.ax.scatter([last_time], [last_price], color="#d73a4a", s=20, zorder=5)
        self.ax.annotate(
            f"${last_price:,.2f}",
            xy=(last_time, last_price),
            xytext=(0, 10),
            textcoords="offset points",
            fontsize=8,
            color="#d73a4a",
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
        self.ax.set_title(title, fontsize=11, pad=10)
        self.ax.set_xlabel("Time", fontsize=9)
        self.ax.set_ylabel("Price", fontsize=9)
        self.ax.tick_params(axis="both", labelsize=8)
        self.ax.grid(True, which="major", color="#d0d0d0", linestyle="--", linewidth=0.6)
        self.ax.set_facecolor("#fdfdfd")
        self.figure.patch.set_facecolor("#f7f7f7")
        for spine in ("top", "right"):
            self.ax.spines[spine].set_visible(False)

    def _on_close(self) -> None:
        if self.refresh_job:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None
        if self.conn:
            self.conn.close()
            self.conn = None
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure("Treeview.Heading", font=("Helvetica", 10, "bold"))
    app = TickerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

