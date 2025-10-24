# Ticker With Price

A minimalist always-on-top desktop ticker built with Python. Add the stocks you own, track your share counts, and watch current values update automatically using free Yahoo Finance data.

## Features

- Lightweight Tkinter UI that stays above other windows (`ticker.py`).
- Quick add/update controls with a popular-symbol dropdown.
- Live pricing via Yahoo's public chart endpoint (no API key required).
- Adjustable auto-refresh cadence (down to 5 seconds) plus manual refresh and inline status feedback.
- Persisted holdings stored locally in SQLite so tracked shares survive restarts.
- Embedded price-history chart with smooth styling to visualize the latest trend.
- Instant light/dark themes with refreshed, modern UI styling.

## Requirements

- Python 3.9 or newer.
- `requests`
- `matplotlib`
- `ttkbootstrap`

## Run It

```bash
python ticker.py
```

Enter a symbol (or pick one from the dropdown), set the share count, and click **Add / Update**. Prices refresh in the background and the window remains on top of your workspace.

> Yahoo's free endpoints occasionally throttle. If a row shows an error, wait a few seconds and use **Refresh Now**.
