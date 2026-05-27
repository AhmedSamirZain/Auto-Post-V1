#!/bin/bash
# Kill any existing bot
pkill -f "python bot.py" 2>/dev/null || true
sleep 1
# Start bot in background
nohup python bot.py > bot_output.log 2>&1 &
echo $! > bot.pid
# Start streamlit
exec streamlit run streamlit_app.py --server.port 5000 --server.address 0.0.0.0 --server.headless true
