# Sol-RSI-Bot

Min Solana trading bot med RSI-beregninger (mock trading først) + dashboard til profit/logs.  
Under udvikling – fokus på leverage på SOL/USDC via Jupiter/Drift.

## Vigtige filer
- bot_logic.py → RSI og mock trading  
- dashboard.py → Dashboard (web-interface)  
- railway.json → Konfig til Railway-deploy  
- nixpacks.toml → Builder-opsætning  
- requirements.txt → Pakker  

## Lokalt setup
1. `pip install -r requirements.txt`  
2. Lav .env med RPC_URL, PRIVATE_KEY osv.  
3. Kør: `uvicorn dashboard:app --reload --host 0.0.0.0 --port 8000`  
4. Åbn: http://localhost:8000  

## Railway-problem
Får stadig "Application failed to respond" (502) – arbejder på port/host-fix i railway.json eller Procfile.

Mere info kommer...
