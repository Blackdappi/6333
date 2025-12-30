import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go
from datetime import datetime

st.set_page_config(page_title="SOL RSI Bot Dashboard", layout="wide")
st.title("🚀 Battle-Tested SOL Buy Low Sell High Bot – Live Dashboard")

# --- Persistent Simulation Balances (overlever refreshes) ---
if 'usdc_balance' not in st.session_state:
    st.session_state.usdc_balance = 1000.0
    st.session_state.sol_balance = 0.0
    st.session_state.total_cost_basis = 0.0
    st.session_state.trade_size_pct = 0.1
    st.session_state.trade_history = []

# Config – kan tunes her eller med sliders senere
increase_per_win = 0.05
max_trade_size_pct = 0.5
rsi_period = 14
rsi_buy = 30
rsi_sell = 70
price_change_filter = -5   # Buy kun hvis 24h change > -5%
price_pump_sell = 20       # Ekstra sell-trigger hvis +20% på 24h

# --- Hent live SOL data fra CoinGecko ---
@st.cache_data(ttl=60)  # Opdater max hver 60 sek
def get_sol_data():
    url = "https://api.coingecko.com/api/v3/coins/solana/market_chart"
    params = {'vs_currency': 'usd', 'days': '1', 'interval': 'hourly'}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()['prices']
            df = pd.DataFrame(data, columns=['timestamp', 'price'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
    except Exception as e:
        st.error(f"Data fejl: {e}")
    return pd.DataFrame()

df = get_sol_data()

if df.empty or len(df) < rsi_period + 10:
    st.error("Kunne ikke hente nok data – tjek forbindelse eller vent lidt.")
    st.stop()

# --- Beregn RSI ---
delta = df['price'].diff()
gain = delta.where(delta > 0, 0).rolling(window=rsi_period).mean()
loss = -delta.where(delta < 0, 0).rolling(window=rsi_period).mean()
rs = gain / loss
rs = rs.replace([float('inf'), -float('inf')], 100)
df['RSI'] = 100 - (100 / (1 + rs))

# Aktuelle værdier
current_price = df['price'].iloc[-1]
current_rsi = df['RSI'].iloc[-1]
price_change_24h = (current_price / df['price'].iloc[0] - 1) * 100
portfolio_value = st.session_state.usdc_balance + st.session_state.sol_balance * current_price

# --- Top Metrics ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("SOL Pris", f"${current_price:,.2f}", f"{price_change_24h:+.2f}% (24h)")
col2.metric("RSI (14)", f"{current_rsi:.1f}")
col3.metric("Trade Size", f"{st.session_state.trade_size_pct*100:.0f}% af USDC")
col4.metric("Total Portfolio", f"${portfolio_value:,.2f}")

# --- Live Chart: Pris + RSI (dual axis) ---
fig = go.Figure()
fig.add_trace(go.Scatter(x=df.index, y=df['price'], name="SOL Pris (USD)", line=dict(color="#00FF00", width=3)))
fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], name="RSI (14)", yaxis="y2", line=dict(color="#FF4444", dash="dot")))

fig.update_layout(
    title="SOL Live Pris & RSI (Hourly Data) – Dec 30, 2025",
    xaxis_title="Tid",
    yaxis=dict(title="Pris (USD)", side="left"),
    yaxis2=dict(title="RSI", side="right", overlaying="y", range=[0, 100]),
    legend=dict(x=0.01, y=0.99),
    template="plotly_dark",
    height=600
)
fig.add_hline(y=rsi_buy, line_dash="dash", line_color="lime", annotation_text="Buy Zone")
fig.add_hline(y=rsi_sell, line_dash="dash", line_color="red", annotation_text="Sell Zone")

st.plotly_chart(fig, use_container_width=True)

# --- Bot Logic – kører på hver refresh ---
trade_executed = False
trade_amount = st.session_state.usdc_balance * st.session_state.trade_size_pct

if current_rsi < rsi_buy and price_change_24h > price_change_filter and trade_amount >= 10:
    sol_bought = trade_amount / current_price
    st.session_state.sol_balance += sol_bought
    st.session_state.usdc_balance -= trade_amount
    st.session_state.total_cost_basis += trade_amount
    trade_executed = True
    st.session_state.trade_history.append(
        f"🟢 BUY {sol_bought:.4f} SOL @ ${current_price:,.2f} | RSI {current_rsi:.1f} | Size {st.session_state.trade_size_pct*100:.0f}%"
    )

elif (current_rsi > rsi_sell or price_change_24h > price_pump_sell) and st.session_state.sol_balance > 0:
    usdc_received = st.session_state.sol_balance * current_price
    profit = us
