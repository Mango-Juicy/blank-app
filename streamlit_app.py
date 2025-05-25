import asyncio, websockets, json, threading, time
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

COIN      = "BTC"
WS_URL    = "wss://api.hyperliquid.xyz/ws"
INTERVAL  = "3min"          # resampling base
SHIFT_SEC = 30              # quanto anticipo dare alle candele CVD

# -----------  WS COLLECTION  -----------
trades, lock = [], threading.Lock()

async def on_msg(msg):
    global trades
    data = json.loads(msg)["data"]
    if isinstance(data, list):
        with lock:
            for t in data:
                trades.append({
                    "ts":   pd.to_datetime(t["time"], unit="ms"),
                    "price":float(t["px"]),
                    "vol":  float(t["sz"]),
                    "side": t["side"]          # "B" oppure "S"
                })

async def ws_loop():
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                await ws.send(json.dumps({
                    "method": "subscribe",
                    "subscription": {"type":"trades","coin":COIN}
                }))
                while True:
                    await on_msg(await ws.recv())
        except Exception as e:
            print("WS error:", e, "reconnect...")
            await asyncio.sleep(5)

threading.Thread(target=lambda: asyncio.run(ws_loop()), daemon=True).start()

# -----------  DATA PROCESSING  -----------
def build_ohlc_cvd(raw, interval):
    if not raw: 
        return None, None
    df = pd.DataFrame(raw).set_index("ts").sort_index()

    # Prezzo OHLC
    ohlc = df["price"].resample(interval).ohlc()

    # CVD delta tick-by-tick
    df["delta"] = df.apply(lambda r: r["vol"] if r["side"]=="B" else -r["vol"], axis=1)
    cvd_tick     = df["delta"].cumsum()

    # Ricampiona il CVD in OHLC sullo stesso frame
    cvd_ohlc = cvd_tick.resample(interval).ohlc().dropna()

    # Anticipa la candela CVD di -30 s
    cvd_ohlc.index = cvd_ohlc.index - pd.Timedelta(seconds=SHIFT_SEC)

    return ohlc.dropna(), cvd_ohlc

# -----------  STREAMLIT UI  -----------
st.set_page_config(layout="wide")
st.title("BTC — Candlestick + CVD (3min)")

placeholder = st.empty()

while True:
    with lock:
        price_ohlc, cvd_ohlc = build_ohlc_cvd(trades, INTERVAL)

    if price_ohlc is not None:
        fig = go.Figure()

        # 1) Prezzo
        fig.add_trace(go.Candlestick(
            name="Price",
            x=price_ohlc.index, open=price_ohlc["open"], high=price_ohlc["high"],
            low=price_ohlc["low"], close=price_ohlc["close"],
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
            increasing_fillcolor="#26a69a",  decreasing_fillcolor="#ef5350",
            opacity=0.9
        ))

        # 2) CVD (shiftato)
        fig.add_trace(go.Candlestick(
            name="CVD",
            x=cvd_ohlc.index,
            open=cvd_ohlc["open"], high=cvd_ohlc["high"],
            low=cvd_ohlc["low"], close=cvd_ohlc["close"],
            increasing_line_color="#68C5FF",  
            decreasing_line_color="#f9c74f",  
            increasing_fillcolor="#68C5FF",
            decreasing_fillcolor="#f9c74f",
            opacity=0.7, yaxis="y2"
        ))

        # Layout a doppio asse (sinistra prezzo, destra CVD)
        fig.update_layout(
            template="plotly_dark", height=650,
            xaxis=dict(title="", rangeslider_visible=False),
            yaxis=dict(title="Price"),
            yaxis2=dict(title="CVD", overlaying="y", side="right", showgrid=False)
        )

        placeholder.plotly_chart(fig, use_container_width=True)

    time.sleep(5)
