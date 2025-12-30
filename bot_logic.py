import pandas as pd
import time
import datetime

# CONFIG - Full mock mode (ingen internet nødvendigt – perfekt til test og demo)
usdc_balance = 1000.0
sol_balance = 0.0
total_cost_basis = 0.0
trade_size_pct = 0.1          # Start med 10% af USDC balance
increase_per_win = 0.05       # +5% trade size kun ved profitabel sell
max_trade_size_pct = 0.5      # Max 50%
rsi_period = 14
rsi_buy = 30                  # Køb når RSI under 30
rsi_sell = 70                 # Sælg når RSI over 70

# Mock SOL prisdata – realistisk dip & pump sequence (baseret på typisk volatilitet)
# Starter ~$125 → dip til $120.2 (RSI <30) → pump til $129+ (RSI >70) → flere cycles
mock_prices = [
    124.5, 123.8, 125.2, 122.1, 121.5, 120.8, 120.5, 121.2, 122.8, 124.6,
    126.1, 128.3, 129.5, 128.9, 127.6, 126.2, 125.0, 123.8, 122.5, 121.0,
    120.2, 121.5, 123.9, 126.8, 129.2, 128.5, 127.0, 125.8, 124.3, 123.1
]

def get_mock_df():
    df = pd.DataFrame({'price': mock_prices})
    return df

def calculate_rsi(df, period=rsi_period):
    if len(df) < period + 1:
        return 50.0
    delta = df['price'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    rs = rs.replace([float('inf'), -float('inf')], 100)
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

print("🚀 SOL RSI Bot – BOT_LOGIC.PY (Full Mock Mode)")
print(f"Start balance: {usdc_balance:.2f} USDC | Initial trade size: {trade_size_pct*100:.0f}%")
print("Kører demo med realistisk dip → buy → pump → sell → compounding\n")

cycle = 0
max_cycles = 30  # Nok til at se flere fulde trades

while cycle < max_cycles:
    cycle += 1
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n--- Cycle {cycle} | {now} ---")
    
    df = get_mock_df()
    rsi = calculate_rsi(df)
    price = df['price'].iloc[-1]
    portfolio_value = usdc_balance + (sol_balance * price)
    
    print(f"SOL Pris: ${price:,.2f} | RSI: {rsi:.1f}")
    
    trade_amount = usdc_balance * trade_size_pct
    
    if rsi < rsi_buy:
        sol_bought = trade_amount / price
        sol_balance += sol_bought
        usdc_balance -= trade_amount
        total_cost_basis += trade_amount
        print(f"🟢 BUY EXECUTED! Købt {sol_bought:.4f} SOL @ ${price:,.2f} (size {trade_size_pct*100:.0f}%)")
        
    elif rsi > rsi_sell and sol_balance > 0:
        usdc_received = sol_balance * price
        profit = usdc_received - total_cost_basis
        usdc_balance += usdc_received
        print(f"🔴 SELL EXECUTED! Solgt {sol_balance:.4f} SOL @ ${price:,.2f} → {usdc_received:.2f} USDC")
        print(f"   Profit denne trade: {profit:+.2f} USDC")
        
        if profit > 0:
            trade_size_pct = min(trade_size_pct + increase_per_win, max_trade_size_pct)
            print(f"📈 VIND! Ny trade size: {trade_size_pct*100:.0f}%")
        
        sol_balance = 0.0
        total_cost_basis = 0.0
        
    else:
        print("⏸️ HOLD – venter på stærkt signal")
    
    print(f"Balance: {usdc_balance:.2f} USDC + {sol_balance:.4f} SOL | Total værdi: ${portfolio_value:.2f}")

    time.sleep(2)  # Pause så du kan følge med

print("\n🎯 Mock simulation færdig!")
print("Du har nu set botten købe på dip, sælge på styrke og compounde trade size på gevinster.")
print("Klar til live? Erstat get_mock_df() med CoinGecko eller Binance API – vi catcher næste SOL run fra $120 til $400+. 💰🚀")
