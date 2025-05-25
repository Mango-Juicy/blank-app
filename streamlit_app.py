import asyncio, websockets, json, threading, time
import pandas as pd, numpy as np
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

COIN, WS_URL       = "BTC", "wss://api.hyperliquid.xyz/ws"
INTERVAL, SHIFT_SEC = "5min", 60
EPS, CVD_EPS        = 1e-8, 1e-3         # per evitare divisioni / segni instabili
RATIO_STRONG = 1
trades, lock        = [], threading.Lock()

# ════════════ WebSocket ════════════
async def on_msg(msg):
    data = json.loads(msg)["data"]
    if isinstance(data, list):
        with lock:
            trades.extend({
                "ts":   pd.to_datetime(t["time"], unit="ms"),
                "price":float(t["px"]),
                "vol":  float(t["sz"]),
                "side": t["side"]
            } for t in data)

async def ws_loop():
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                await ws.send(json.dumps({
                    "method":"subscribe",
                    "subscription":{"type":"trades","coin":COIN}
                }))
                while True:
                    await on_msg(await ws.recv())
        except Exception as e:
            print("WS error:", e, "reconnect...")
            await asyncio.sleep(5)

threading.Thread(target=lambda: asyncio.run(ws_loop()), daemon=True).start()

# ════════════ Data processing ════════════
def classify(delta_p, delta_cvd, ratio):
    """Ritorna la stringa di segnale in base al segno/magnitude."""
    if abs(delta_cvd) < CVD_EPS or abs(delta_p) < CVD_EPS:
        return "Neutral"    

    if ratio > RATIO_STRONG:
        return "Strong Long" if delta_p > 0 else "Strong Short" # Movimento coerente
    elif ratio > 0:
        return "Assorb, short?" if delta_p > 0 else "Assorb, long?" # Assorbimento dei buy/sell
    elif ratio < 0:
        return "Invert, short?" if delta_p > 0 else "Invert, long?"  # Inversione
    else:
        return "Neutral"

def build_frames(raw, interval):
    if not raw:
        return None, None, None, None
    df = pd.DataFrame(raw).set_index("ts").sort_index()

    price = df["price"].resample(interval).ohlc()
    df["delta"] = np.where(df["side"] == "B", df["vol"], -df["vol"])
    cvd_tick    = df["delta"].cumsum()
    cvd         = cvd_tick.resample(interval).ohlc()

    delta_price = price["close"] - price["open"]
    delta_cvd   = cvd["close"]   - cvd["open"]
    avg_abs_price = delta_price.abs().rolling(window=10, min_periods=1).mean()
    avg_abs_cvd   = delta_cvd.abs().rolling(window=10, min_periods=1).mean()

    norm_dp   = delta_price / (avg_abs_price + EPS)
    norm_dcvd = delta_cvd   / (avg_abs_cvd + EPS)

    ratio = (norm_dp / (norm_dcvd + EPS)).rename("ratio")

    # segnale testo per ogni barra
    signals = pd.Series(index=ratio.index, dtype="object")
    for t in ratio.index:
        signals[t] = classify(delta_price.loc[t], delta_cvd.loc[t], ratio.loc[t])

    cvd_shifted          = cvd.copy()
    cvd_shifted.index    = cvd_shifted.index - pd.Timedelta(seconds=SHIFT_SEC)

    return price.dropna(), cvd_shifted.dropna(), ratio.dropna(), signals.dropna()

# ════════════ Streamlit UI ════════════
st.set_page_config(layout="wide")
st.title(f"BTC ({INTERVAL}) — Price, CVD & Efficiency Signal")

placeholder = st.empty()

while True:
    with lock:
        price_ohlc, cvd_ohlc, ratio_ser, sig_ser = build_frames(trades, INTERVAL)

    if price_ohlc is not None:
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            specs=[[{"secondary_y": True}], [{}]],
            row_heights=[0.75, 0.25], vertical_spacing=0.03
        )

        # ── Row 1: Price
        fig.add_trace(go.Candlestick(
            name="Price",
            x=price_ohlc.index,
            open=price_ohlc["open"], high=price_ohlc["high"],
            low=price_ohlc["low"],   close=price_ohlc["close"],
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
            increasing_fillcolor="#26a69a",  decreasing_fillcolor="#ef5350",
            opacity=0.9),
            row=1, col=1, secondary_y=False
        )

        # ── Row 1: CVD (shiftato)
        fig.add_trace(go.Candlestick(
            name="CVD",
            x=cvd_ohlc.index,
            open=cvd_ohlc["open"], high=cvd_ohlc["high"],
            low=cvd_ohlc["low"],   close=cvd_ohlc["close"],
            increasing_line_color="#68C5FF", decreasing_line_color="#f9c74f",
            increasing_fillcolor="#68C5FF",  decreasing_fillcolor="#f9c74f",
            opacity=0.7),
            row=1, col=1, secondary_y=True
        )

        # ── Row 1: testo “ratio – segnale”
        labels = [
            f"{ratio_ser.reindex(price_ohlc.index)[i]:.2f} – {sig_ser.reindex(price_ohlc.index)[i]}"
            if i in ratio_ser else "" for i in price_ohlc.index
        ]
        fig.add_trace(go.Scatter(
            x=price_ohlc.index,
            y=price_ohlc["high"] * 1.0007,   # leggero offset
            mode="text", text=labels,
            textfont=dict(color="white", size=11),
            showlegend=False),
            row=1, col=1
        )

        # ── Row 2: ratio line
        fig.add_trace(go.Scatter(
            name="Efficiency",
            x=ratio_ser.index,
            y=ratio_ser.values,
            mode="lines+markers",
            line=dict(color="#00e3fd"),
            opacity=0.85),
            row=2, col=1
        )

        fig.update_layout(
            template="plotly_dark", height=760,
            xaxis_rangeslider_visible=False,
            yaxis=dict(title="Price"),
            yaxis2=dict(title="CVD", overlaying="y", side="right", showgrid=False),
            yaxis3=dict(title="Ratio"),
        )

        placeholder.plotly_chart(fig, use_container_width=True, key=f"plot_{time.time()}")

    time.sleep(5)
