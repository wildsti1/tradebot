import os
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, render_template_string, jsonify, request, session

SOFIA_TZ = ZoneInfo('Europe/Sofia')
app = Flask(__name__)
app.secret_key = os.getenv('TRADEBOT_SECRET_KEY') or secrets.token_hex(32)

# These will be populated by main.py
balance_info = {'balance': 0.0, 'available': 0.0}
open_positions = {}
closed_trades = []
request_stats = {'count': 0, 'last_request': None}
capital_api_stats = {'count': 0, 'last_request': None}
server_start_time = datetime.now(SOFIA_TZ)
strategy_name = 'Unknown'
watch_pairs = []


def set_strategy_and_pairs(strategy, pairs):
    global strategy_name, watch_pairs
    strategy_name = strategy or 'Unknown'
    watch_pairs = [p.upper() for p in (pairs or [])]

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>TradeBot Dashboard</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1e1e2e 0%, #2d2d44 100%);
            color: #e0e0e0;
            padding: 20px;
            min-height: 100vh;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        .header {
            text-align: center;
            margin-bottom: 40px;
            padding-bottom: 20px;
            border-bottom: 2px solid #00d4ff;
        }
        
        .header h1 {
            font-size: 2.5em;
            color: #00d4ff;
            text-shadow: 0 0 10px rgba(0, 212, 255, 0.5);
            margin-bottom: 5px;
        }
        
        .header p {
            color: #a0a0a0;
            font-size: 0.9em;
        }
        
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }
        
        .card {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(0, 212, 255, 0.3);
            border-radius: 10px;
            padding: 20px;
            backdrop-filter: blur(10px);
            transition: all 0.3s ease;
        }
        
        .card:hover {
            background: rgba(255, 255, 255, 0.1);
            border-color: #00d4ff;
            transform: translateY(-5px);
        }
        
        .card-title {
            color: #00d4ff;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 10px;
            opacity: 0.8;
        }
        
        .card-value {
            font-size: 1.8em;
            font-weight: bold;
            color: #00ff88;
            margin-bottom: 5px;
        }
        
        .card-subtitle {
            color: #a0a0a0;
            font-size: 0.85em;
        }
        
        .positive { color: #00ff88; }
        .negative { color: #ff4444; }
        .neutral { color: #ffa500; }
        
        .section {
            margin-bottom: 40px;
        }
        
        .section-title {
            font-size: 1.5em;
            color: #00d4ff;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid rgba(0, 212, 255, 0.3);
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
	        table {
	            width: 100%;
	            border-collapse: collapse;
	            background: rgba(255, 255, 255, 0.05);
	            border-radius: 8px;
	            overflow: hidden;
	            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3);
	            min-width: 720px;
	        }

	        .table-wrap {
	            width: 100%;
	            overflow-x: auto;
	            -webkit-overflow-scrolling: touch;
	            border-radius: 8px;
	        }

	        @media (max-width: 768px) {
	            table {
	                min-width: 680px;
	            }
	        }
        
        thead {
            background: rgba(0, 212, 255, 0.1);
            border-bottom: 2px solid rgba(0, 212, 255, 0.3);
        }
        
        th {
            padding: 15px;
            text-align: left;
            color: #00d4ff;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-size: 0.85em;
        }
        
        td {
            padding: 12px 15px;
            border-bottom: 1px solid rgba(0, 212, 255, 0.1);
        }
        
        tbody tr {
            transition: background 0.2s ease;
        }
        
        tbody tr:hover {
            background: rgba(0, 212, 255, 0.1);
        }
        
        tbody tr:last-child td {
            border-bottom: none;
        }
        
        .no-data {
            text-align: center;
            padding: 30px;
            color: #a0a0a0;
            font-style: italic;
        }
        
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.8em;
            font-weight: 600;
            text-transform: uppercase;
        }
        
        .badge-long {
            background: rgba(0, 255, 136, 0.2);
            color: #00ff88;
            border: 1px solid #00ff88;
        }
        
        .badge-short {
            background: rgba(255, 68, 68, 0.2);
            color: #ff4444;
            border: 1px solid #ff4444;
        }
        
        .badge-profit {
            background: rgba(0, 255, 136, 0.2);
            color: #00ff88;
            border: 1px solid #00ff88;
        }
        
        .badge-loss {
            background: rgba(255, 68, 68, 0.2);
            color: #ff4444;
            border: 1px solid #ff4444;
        }

        .refresh-hud {
            position: fixed;
            right: 14px;
            bottom: 14px;
            z-index: 50;
            width: 260px;
            padding: 10px 12px;
            border-radius: 12px;
            background: rgba(0,0,0,0.35);
            border: 1px solid rgba(255,255,255,0.14);
            backdrop-filter: blur(10px);
        }
        .refresh-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            font-size: 12px;
            color: rgba(231,234,243,0.85);
            margin-bottom: 8px;
        }
        .refresh-pill {
            padding: 3px 8px;
            border-radius: 999px;
            border: 1px solid rgba(0,212,255,0.35);
            color: rgba(0,212,255,0.95);
            font-weight: 700;
            letter-spacing: 0.2px;
        }
        .refresh-bar {
            height: 8px;
            border-radius: 999px;
            background: rgba(255,255,255,0.10);
            overflow: hidden;
            border: 1px solid rgba(255,255,255,0.12);
        }
        .refresh-bar > div {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, rgba(0,212,255,0.95), rgba(0,255,136,0.85));
            border-radius: 999px;
        }

        .reload-overlay {
            position: fixed;
            inset: 0;
            z-index: 60;
            display: none;
            align-items: center;
            justify-content: center;
            background: rgba(11,16,32,0.70);
            backdrop-filter: blur(6px);
        }
        .reload-card {
            width: min(420px, calc(100vw - 36px));
            padding: 18px;
            border-radius: 14px;
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(0,212,255,0.28);
            text-align: center;
        }
        .spinner {
            width: 34px;
            height: 34px;
            border-radius: 999px;
            border: 3px solid rgba(255,255,255,0.18);
            border-top-color: rgba(0,212,255,0.95);
            margin: 0 auto 10px;
            animation: spin 0.8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        
        @media (max-width: 768px) {
            .grid {
                grid-template-columns: 1fr;
            }
            
            .header h1 {
                font-size: 1.8em;
            }
            
            th, td {
                padding: 10px;
                font-size: 0.9em;
            }
        }
    </style>
    <script>
        // Auto-refresh every 10 seconds with a small HUD + loading overlay.
        const REFRESH_MS = 10000;
        let startedAt = Date.now();
        let reloadScheduled = false;

        function setHud(remainingMs) {
            const secs = Math.max(0, Math.ceil(remainingMs / 1000));
            const pct = Math.min(100, Math.max(0, ((REFRESH_MS - remainingMs) / REFRESH_MS) * 100));
            const countdown = document.getElementById('refreshCountdown');
            const bar = document.getElementById('refreshBarFill');
            if (countdown) countdown.textContent = `${secs}s`;
            if (bar) bar.style.width = `${pct}%`;
        }

        function showOverlay() {
            const overlay = document.getElementById('reloadOverlay');
            if (overlay) overlay.style.display = 'flex';
        }

        function tick() {
            const now = Date.now();
            const elapsed = now - startedAt;
            const remaining = REFRESH_MS - elapsed;
            setHud(remaining);
            if (!reloadScheduled && remaining <= 250) {
                reloadScheduled = true;
                showOverlay();
                setTimeout(() => location.reload(), 120);
                return;
            }
            requestAnimationFrame(tick);
        }

        window.addEventListener('load', () => {
            startedAt = Date.now();
            setHud(REFRESH_MS);
            requestAnimationFrame(tick);
        });
    </script>
</head>
<body>
    <div class="refresh-hud" id="refreshHud">
        <div class="refresh-row">
            <div>Auto refresh</div>
            <div class="refresh-pill" id="refreshCountdown">10s</div>
        </div>
        <div class="refresh-bar"><div id="refreshBarFill"></div></div>
    </div>
    <div class="container">
        <div class="header">
            <h1>📊 TradeBot Dashboard</h1>
            <p>{{ strategy_name }} | Watching: {{ watch_pairs_display }} | 15-Minute Candles</p>
        </div>
        
        <div class="grid">
            <div class="card">
                <div class="card-title">Bot Status</div>
                <div class="card-value positive">Online</div>
                <div class="card-subtitle">Server is running</div>
            </div>
            
            <div class="card">
                <div class="card-title">Server Started</div>
                <div class="card-value">{{ server_start_time }}</div>
                <div class="card-subtitle">Sofia time zone</div>
            </div>
            
            <div class="card">
                <div class="card-title">API Calls</div>
                <div class="card-value neutral">{{ capital_api_count }}</div>
                <div class="card-subtitle">Broker requests from bot</div>
            </div>
            
            <div class="card">
                <div class="card-title">Last API Call</div>
                <div class="card-value">{{ capital_api_last_request }}</div>
                <div class="card-subtitle">Last Capital API request</div>
            </div>
            <div class="card">
                <div class="card-title">Latest Spread</div>
                <div class="card-value">{{ "%.5f"|format(latest_spread) }}</div>
                <div class="card-subtitle">Most recent open position spread</div>
            </div>
            
            <div class="card">
                <div class="card-title">Account Balance</div>
                <div class="card-value positive">{{ "%.2f"|format(balance_info.get('balance', 0) | float) }}</div>
                <div class="card-subtitle">Total Balance</div>
            </div>
            
            <div class="card">
                <div class="card-title">Available</div>
                <div class="card-value">{{ "%.2f"|format(balance_info.get('available', 0) | float) }}</div>
                <div class="card-subtitle">Free Capital</div>
            </div>
            
            <div class="card">
                <div class="card-title">Total P&L</div>
                <div class="card-value {% if total_pnl >= 0 %}positive{% else %}negative{% endif %}">{{ "%.2f"|format(total_pnl) }}</div>
                <div class="card-subtitle">All Closed Trades</div>
            </div>
            
            <div class="card">
                <div class="card-title">Win Rate</div>
                <div class="card-value neutral">{{ "%.1f"|format(win_rate) }}%</div>
                <div class="card-subtitle">{{ win_count }} Wins / {{ closed_trades|length }} Total</div>
            </div>
            
            <div class="card">
                <div class="card-title">Open Positions</div>
                <div class="card-value">{{ open_positions|length }}</div>
                <div class="card-subtitle">Active Trades</div>
            </div>
        </div>
        
	        <div class="section">
	            <div class="section-title">📍 Open Positions</div>
	            {% if open_positions %}
	            <div class="table-wrap">
	            <table>
	                <thead>
	                    <tr>
	                        <th>Symbol</th>
                        <th>Direction</th>
                        <th>Size</th>
                        <th>Entry Price</th>
                        <th>Spread</th>
                        <th>Stop Loss</th>
                        <th>Take Profit</th>
                    </tr>
                </thead>
                <tbody>
                    {% for symbol, pos in open_positions.items() %}
                    <tr>
                        <td><strong>{{ symbol }}</strong></td>
                        <td>
                            <span class="badge {% if pos.direction == 'LONG' %}badge-long{% else %}badge-short{% endif %}">
                                {{ pos.direction }}
                            </span>
                        </td>
                        <td>{{ pos.size }}</td>
                        <td>{{ "%.5f"|format(pos.entry_price) }}</td>
                        <td>{{ "%.5f"|format(pos.spread) }}</td>
                        <td>{% if pos.stop_loss is not none %}{{ "%.5f"|format(pos.stop_loss) }}{% else %}—{% endif %}</td>
                        <td>{% if pos.take_profit is not none %}{{ "%.5f"|format(pos.take_profit) }}{% else %}—{% endif %}</td>
                    </tr>
                    {% endfor %}
	                </tbody>
	            </table>
	            </div>
	            {% else %}
	            <div class="no-data">No open positions</div>
	            {% endif %}
	        </div>
        
	        <div class="section">
	            <div class="section-title">✅ Closed Trades</div>
	            {% if closed_trades %}
	            <div class="table-wrap">
	            <table>
	                <thead>
	                    <tr>
	                        <th>Symbol</th>
                        <th>Direction</th>
                        <th>Entry Price</th>
                        <th>Exit Price</th>
                        <th>P&L</th>
                        <th>Reason</th>
                    </tr>
                </thead>
                <tbody>
                    {% for trade in closed_trades %}
                    <tr>
                        <td><strong>{{ trade.symbol }}</strong></td>
                        <td>
                            <span class="badge {% if trade.direction == 'LONG' %}badge-long{% else %}badge-short{% endif %}">
                                {{ trade.direction }}
                            </span>
                        </td>
                        <td>{{ "%.5f"|format(trade.entry_price) }}</td>
                        <td>{{ "%.5f"|format(trade.exit_price) }}</td>
                        <td>
                            <span class="{% if trade.pnl > 0 %}positive{% else %}negative{% endif %}">
                                {{ "%.2f"|format(trade.pnl) }}
                            </span>
                        </td>
                        <td>{{ trade.reason }}</td>
                    </tr>
                    {% endfor %}
	                </tbody>
	            </table>
	            </div>
	            {% else %}
	            <div class="no-data">No closed trades yet</div>
	            {% endif %}
	        </div>
    </div>
</body>
</html>
"""

@app.before_request
def track_request():
    if request.path.startswith('/api/'):
        request_stats['count'] += 1
        request_stats['last_request'] = datetime.now(SOFIA_TZ)
    app.logger.info(f"Incoming request: {request.method} {request.path}")

@app.route('/')
def dashboard():
    total_pnl = sum(t.pnl for t in closed_trades)
    win_count = sum(1 for t in closed_trades if t.pnl > 0)
    win_rate = (win_count / len(closed_trades) * 100) if closed_trades else 0
    last_request = request_stats['last_request'].strftime('%Y-%m-%d %H:%M:%S %Z') if request_stats['last_request'] else 'N/A'
    
    last_capital_request = capital_api_stats['last_request'].strftime('%Y-%m-%d %H:%M:%S %Z') if capital_api_stats['last_request'] else 'N/A'
    latest_spread = next((float(pos.spread) for pos in open_positions.values()), 0.0)
    watch_pairs_display = ', '.join(watch_pairs) if watch_pairs else 'None'
    return render_template_string(
        HTML_TEMPLATE,
        balance_info=balance_info,
        open_positions=open_positions,
        closed_trades=closed_trades,
        total_pnl=total_pnl,
        win_rate=win_rate,
        win_count=win_count,
        request_count=request_stats['count'],
        capital_api_count=capital_api_stats['count'],
        capital_api_last_request=last_capital_request,
        last_request_time=last_request,
        latest_spread=latest_spread,
        watch_pairs_display=watch_pairs_display,
        strategy_name=strategy_name,
        server_start_time=server_start_time.strftime('%Y-%m-%d %H:%M:%S %Z')
    )

@app.route('/api/status')
def api_status():
    return jsonify({
        'online': True,
        'server_start_time': server_start_time.isoformat(),
        'requests': request_stats['count'],
        'last_request_time': request_stats['last_request'].isoformat() if request_stats['last_request'] else None
    })

@app.route('/api/balance')
def api_balance():
    return jsonify(balance_info)

@app.route('/api/positions')
def api_positions():
    return jsonify({k: v.__dict__ for k, v in open_positions.items()})

@app.route('/api/trades')
def api_trades():
    return jsonify([t.__dict__ for t in closed_trades])

def run_web_server():
    """Run the Flask web server."""
    app.run(host='0.0.0.0', port=5000, debug=False)
