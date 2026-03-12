# ================================
# admin.py - User Management & Admin Panel
# ================================
from flask import Blueprint, render_template_string, request, flash, redirect, url_for, session, current_app
from functools import wraps
from flask_bcrypt import Bcrypt
from datetime import datetime
import os
from db_utils import connect as db_connect, init_schema

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_FILE = os.getenv('DATABASE_FILE', os.path.join(BASE_DIR, 'order_counter.db'))

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')
bcrypt = Bcrypt()

# Admin users list (loaded from environment)
ADMIN_USERS = os.getenv('ADMIN_USERS', 'admin').split(',')

def get_db_connection():
    return db_connect(default_sqlite_db_file=DATABASE_FILE)


def admin_required(f):
    """Decorator to require admin privileges"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            flash("Please login to access admin panel.", "error")
            return redirect(url_for('login'))
        
        if session['user'] not in ADMIN_USERS:
            flash("You don't have permission to access admin panel.", "error")
            return redirect(url_for('home'))
        
        return f(*args, **kwargs)
    return decorated


def get_theme_css():
    """Return shared theme CSS"""
    return """
    <style>
        *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
        
        :root {
            --primary-color: #6366f1; --secondary-color: #8b5cf6; 
            --header-bg: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%);
            --bg-main: #fafbfc; --bg-card: #ffffff; --text-dark: #0f172a; --text-light: #ffffff;
            --text-muted: #64748b; --border-color: #e2e8f0; --shadow-color: rgba(99, 102, 241, 0.15);
            --success-bg: #dcfce7; --success-text: #166534; --error-bg: #fee2e2; --error-text: #dc2626;
            --warning-bg: #fef9c3; --warning-text: #854d0e;
            --gradient-accent: linear-gradient(135deg, rgba(99, 102, 241, 0.1), rgba(139, 92, 246, 0.1));
        }
        
        [data-theme='dark'] {
            --primary-color: #818cf8; --secondary-color: #a78bfa;
            --header-bg: linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #581c87 100%);
            --bg-main: #0f0f23; --bg-card: #1e1b4b; --text-dark: #f1f5f9; --text-light: #f8fafc;
            --text-muted: #94a3b8; --border-color: #334155; --shadow-color: rgba(129, 140, 248, 0.2);
        }
        
        html { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        body {
            background: var(--bg-main); min-height: 100vh; color: var(--text-dark);
            display: flex; align-items: center; justify-content: center; padding: 1.5rem;
        }
        
        .admin-container {
            background: var(--bg-card); border-radius: 24px; width: 100%; max-width: 1200px;
            box-shadow: 0 20px 40px -12px var(--shadow-color);
            border: 1px solid var(--border-color); overflow: hidden;
        }
        
        .admin-header {
            background: var(--header-bg); color: var(--text-light); padding: 2rem;
            text-align: center;
        }
        
        .admin-header h1 { color: white; -webkit-text-fill-color: white; margin-bottom: 0.5rem; }
        .admin-header p { opacity: 0.9; }
        
        .admin-nav {
            display: flex; gap: 1rem; justify-content: center; margin-top: 1.5rem; flex-wrap: wrap;
        }
        
        .admin-nav a {
            background: rgba(255,255,255,0.1); color: white; padding: 0.5rem 1rem;
            border-radius: 12px; text-decoration: none; font-size: 0.9rem;
            transition: all 0.2s ease; border: 1px solid rgba(255,255,255,0.2);
        }
        
        .admin-nav a:hover { background: rgba(255,255,255,0.2); transform: translateY(-2px); }
        .admin-nav a.active { background: rgba(255,255,255,0.3); }
        
        .admin-main { padding: 2rem; }
        
        .stats-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1.5rem; margin-bottom: 2rem;
        }
        
        .stat-card {
            background: var(--gradient-accent); border-radius: 16px; padding: 1.5rem;
            border: 1px solid var(--border-color); text-align: center;
            transition: transform 0.2s ease;
        }
        
        .stat-card:hover { transform: translateY(-4px); }
        .stat-card .icon { font-size: 2.5rem; margin-bottom: 0.5rem; }
        .stat-card .value { font-size: 2rem; font-weight: 700; color: var(--primary-color); }
        .stat-card .label { color: var(--text-muted); font-size: 0.9rem; }
        
        .section-card {
            background: var(--bg-card); border-radius: 16px;
            border: 1px solid var(--border-color); overflow: hidden; margin-bottom: 2rem;
        }
        
        .section-header {
            background: var(--gradient-accent); padding: 1rem 1.5rem;
            border-bottom: 1px solid var(--border-color);
            display: flex; justify-content: space-between; align-items: center;
        }
        
        .section-header h3 { color: var(--primary-color); margin: 0; }
        
        .section-body { padding: 1.5rem; }
        
        .data-table {
            width: 100%; border-collapse: collapse; font-size: 0.9rem;
        }
        
        .data-table th, .data-table td {
            padding: 1rem; text-align: left; border-bottom: 1px solid var(--border-color);
        }
        
        .data-table th { 
            background: var(--gradient-accent); font-weight: 600; 
            color: var(--primary-color); white-space: nowrap;
        }
        
        .data-table tr:hover td { background: var(--gradient-accent); }
        
        .btn {
            padding: 0.5rem 1rem; border: none; border-radius: 8px; font-weight: 500;
            cursor: pointer; text-decoration: none; display: inline-flex;
            align-items: center; gap: 0.5rem; transition: all 0.2s ease;
            font-size: 0.85rem;
        }
        
        .btn-primary { background: var(--primary-color); color: white; }
        .btn-danger { background: var(--error-text); color: white; }
        .btn-secondary { background: var(--bg-main); color: var(--text-dark); border: 1px solid var(--border-color); }
        .btn-success { background: var(--success-text); color: white; }
        
        .btn:hover { transform: translateY(-2px); opacity: 0.9; }
        
        .badge {
            display: inline-block; padding: 0.25rem 0.75rem; border-radius: 20px;
            font-size: 0.75rem; font-weight: 600;
        }
        
        .badge-admin { background: var(--primary-color); color: white; }
        .badge-user { background: var(--border-color); color: var(--text-dark); }
        .badge-active { background: var(--success-bg); color: var(--success-text); }
        .badge-inactive { background: var(--error-bg); color: var(--error-text); }
        
        .form-group { margin-bottom: 1rem; }
        .form-group label { display: block; margin-bottom: 0.5rem; font-weight: 500; }
        .form-group input, .form-group select {
            width: 100%; padding: 0.75rem 1rem; border: 1px solid var(--border-color);
            border-radius: 8px; font-size: 0.9rem; background: var(--bg-main);
            color: var(--text-dark);
        }
        .form-group input:focus, .form-group select:focus {
            outline: none; border-color: var(--primary-color);
            box-shadow: 0 0 0 3px var(--shadow-color);
        }
        
        .alert {
            padding: 1rem 1.5rem; margin-bottom: 1.5rem; border-radius: 12px;
            font-weight: 500; animation: slideIn 0.3s ease;
        }
        .alert-success { background: var(--success-bg); color: var(--success-text); }
        .alert-error { background: var(--error-bg); color: var(--error-text); }
        .alert-warning { background: var(--warning-bg); color: var(--warning-text); }
        
        @keyframes slideIn {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .modal-overlay {
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.5); display: none; align-items: center;
            justify-content: center; z-index: 1000;
        }
        
        .modal-overlay.active { display: flex; }
        
        .modal {
            background: var(--bg-card); border-radius: 16px; padding: 2rem;
            max-width: 500px; width: 90%; max-height: 90vh; overflow-y: auto;
            box-shadow: 0 20px 50px rgba(0,0,0,0.3);
        }
        
        .modal h2 { margin-bottom: 1.5rem; color: var(--primary-color); }
        
        .activity-list { max-height: 400px; overflow-y: auto; }
        .activity-item {
            display: flex; gap: 1rem; padding: 1rem; border-bottom: 1px solid var(--border-color);
            transition: background 0.2s;
        }
        .activity-item:hover { background: var(--gradient-accent); }
        .activity-icon { font-size: 1.5rem; }
        .activity-content { flex: 1; }
        .activity-time { color: var(--text-muted); font-size: 0.85rem; }
        
        @media (max-width: 768px) {
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .data-table { font-size: 0.8rem; }
            .data-table th, .data-table td { padding: 0.75rem 0.5rem; }
            .admin-nav { flex-direction: column; align-items: center; }
        }
    </style>
    """


# ==================== Admin Dashboard ====================
@admin_bp.route('/')
@admin_required
def admin_dashboard():
    try:
        conn = get_db_connection()
        
        # Stats
        total_orders = conn.execute("SELECT COUNT(*) as c FROM sale_orders").fetchone()['c']
        today = datetime.now().strftime('%Y-%m-%d')
        today_orders = conn.execute(
            "SELECT COUNT(*) as c FROM sale_orders WHERE generated_at LIKE ?",
            (f"{today}%",)
        ).fetchone()['c']
        issued_ids = conn.execute("SELECT COUNT(*) as c FROM issued_order_ids").fetchone()['c']
        active_sessions = conn.execute("SELECT COUNT(*) as c FROM active_sessions").fetchone()['c']
        
        # Recent activity
        recent_orders = conn.execute("""
            SELECT order_id, dealer_name, city, username, generated_at 
            FROM sale_orders ORDER BY generated_at DESC LIMIT 10
        """).fetchall()
        
        # Active sessions
        sessions = conn.execute("SELECT * FROM active_sessions ORDER BY issued_at DESC").fetchall()
        
        conn.close()
    except Exception as e:
        current_app.logger.error(f"Admin dashboard error: {e}")
        total_orders = today_orders = issued_ids = active_sessions = 0
        recent_orders = sessions = []
    
    # Build activity HTML
    activity_html = ""
    for order in recent_orders:
        activity_html += f"""
        <div class="activity-item">
            <div class="activity-icon">📋</div>
            <div class="activity-content">
                <div><strong>{order['dealer_name']}</strong> - {order['city']}</div>
                <div style="font-size: 0.85rem; color: var(--text-muted);">
                    Order ID: {order['order_id']} • By: {order['username']}
                </div>
            </div>
            <div class="activity-time">{order['generated_at']}</div>
        </div>
        """
    
    # Build sessions HTML
    sessions_html = ""
    for sess in sessions:
        sessions_html += f"""
        <tr>
            <td><strong>{sess['username']}</strong></td>
            <td>{sess['ip'] or 'N/A'}</td>
            <td style="font-size: 0.8rem; max-width: 200px; overflow: hidden; text-overflow: ellipsis;">
                {(sess['user_agent'] or 'N/A')[:50]}...
            </td>
            <td>{sess['issued_at']}</td>
            <td>
                <span class="badge badge-active">Active</span>
            </td>
        </tr>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Admin Dashboard - Sale Order System</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        {get_theme_css()}
    </head>
    <body>
        <div class="admin-container">
            <div class="admin-header">
                <h1>⚙️ Admin Dashboard</h1>
                <p>System management & monitoring</p>
                <nav class="admin-nav">
                    <a href="/admin" class="active">📊 Dashboard</a>
                    <a href="/admin/users">👥 Users</a>
                    <a href="/admin/orders">📋 All Orders</a>
                    <a href="/admin/sessions">🔐 Sessions</a>
                    <a href="/admin/logs">📝 Logs</a>
                    <a href="/">🏠 Home</a>
                </nav>
            </div>
            
            <div class="admin-main">
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="icon">📊</div>
                        <div class="value">{total_orders}</div>
                        <div class="label">Total Orders</div>
                    </div>
                    <div class="stat-card">
                        <div class="icon">📅</div>
                        <div class="value">{today_orders}</div>
                        <div class="label">Today's Orders</div>
                    </div>
                    <div class="stat-card">
                        <div class="icon">🎯</div>
                        <div class="value">{issued_ids}</div>
                        <div class="label">Issued IDs</div>
                    </div>
                    <div class="stat-card">
                        <div class="icon">🔐</div>
                        <div class="value">{active_sessions}</div>
                        <div class="label">Active Sessions</div>
                    </div>
                </div>
                
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 2rem;">
                    <div class="section-card">
                        <div class="section-header">
                            <h3>📋 Recent Orders</h3>
                            <a href="/admin/orders" class="btn btn-secondary">View All</a>
                        </div>
                        <div class="activity-list">
                            {activity_html if activity_html else '<p style="padding: 2rem; text-align: center; color: var(--text-muted);">No recent orders</p>'}
                        </div>
                    </div>
                    
                    <div class="section-card">
                        <div class="section-header">
                            <h3>🔐 Active Sessions</h3>
                            <a href="/admin/sessions" class="btn btn-secondary">Manage</a>
                        </div>
                        <div style="overflow-x: auto;">
                            <table class="data-table">
                                <thead>
                                    <tr>
                                        <th>User</th>
                                        <th>IP</th>
                                        <th>Device</th>
                                        <th>Login Time</th>
                                        <th>Status</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {sessions_html if sessions_html else '<tr><td colspan="5" style="text-align: center; padding: 2rem;">No active sessions</td></tr>'}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            // Apply saved theme
            const theme = localStorage.getItem('theme') || 'default';
            document.documentElement.setAttribute('data-theme', theme);
        </script>
    </body>
    </html>
    """
    return html


# ==================== Session Management ====================
@admin_bp.route('/sessions')
@admin_required
def manage_sessions():
    try:
        conn = get_db_connection()
        sessions = conn.execute("SELECT * FROM active_sessions ORDER BY issued_at DESC").fetchall()
        conn.close()
    except Exception as e:
        current_app.logger.error(f"Sessions fetch error: {e}")
        sessions = []
    
    sessions_html = ""
    for sess in sessions:
        sessions_html += f"""
        <tr>
            <td><strong>{sess['username']}</strong></td>
            <td>{sess['session_id'][:16]}...</td>
            <td>{sess['ip'] or 'N/A'}</td>
            <td style="max-width: 250px; overflow: hidden; text-overflow: ellipsis; font-size: 0.8rem;">
                {sess['user_agent'] or 'N/A'}
            </td>
            <td>{sess['issued_at']}</td>
            <td>
                <form method="POST" action="/admin/sessions/revoke/{sess['username']}" style="display: inline;">
                    <button type="submit" class="btn btn-danger" onclick="return confirm('Revoke session for {sess['username']}?')">
                        🚫 Revoke
                    </button>
                </form>
            </td>
        </tr>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Session Management - Admin</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        {get_theme_css()}
    </head>
    <body>
        <div class="admin-container">
            <div class="admin-header">
                <h1>🔐 Session Management</h1>
                <p>Monitor and manage active user sessions</p>
                <nav class="admin-nav">
                    <a href="/admin">📊 Dashboard</a>
                    <a href="/admin/users">👥 Users</a>
                    <a href="/admin/orders">📋 All Orders</a>
                    <a href="/admin/sessions" class="active">🔐 Sessions</a>
                    <a href="/">🏠 Home</a>
                </nav>
            </div>
            
            <div class="admin-main">
                <div class="section-card">
                    <div class="section-header">
                        <h3>Active Sessions ({len(sessions)})</h3>
                        <form method="POST" action="/admin/sessions/revoke-all">
                            <button type="submit" class="btn btn-danger" onclick="return confirm('Revoke ALL sessions? All users will be logged out.')">
                                🚫 Revoke All
                            </button>
                        </form>
                    </div>
                    <div style="overflow-x: auto;">
                        <table class="data-table">
                            <thead>
                                <tr>
                                    <th>Username</th>
                                    <th>Session ID</th>
                                    <th>IP Address</th>
                                    <th>User Agent</th>
                                    <th>Login Time</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {sessions_html if sessions_html else '<tr><td colspan="6" style="text-align: center; padding: 2rem;">No active sessions</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            const theme = localStorage.getItem('theme') || 'default';
            document.documentElement.setAttribute('data-theme', theme);
        </script>
    </body>
    </html>
    """
    return html


@admin_bp.route('/sessions/revoke/<username>', methods=['POST'])
@admin_required
def revoke_session(username):
    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM active_sessions WHERE username = ?", (username,))
        conn.commit()
        conn.close()
        flash(f"Session revoked for user: {username}", "success")
    except Exception as e:
        current_app.logger.error(f"Session revoke error: {e}")
        flash("Failed to revoke session", "error")
    return redirect(url_for('admin.manage_sessions'))


@admin_bp.route('/sessions/revoke-all', methods=['POST'])
@admin_required
def revoke_all_sessions():
    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM active_sessions")
        conn.commit()
        conn.close()
        flash("All sessions have been revoked", "success")
    except Exception as e:
        current_app.logger.error(f"Revoke all sessions error: {e}")
        flash("Failed to revoke all sessions", "error")
    return redirect(url_for('admin.manage_sessions'))


# ==================== All Orders (Admin View) ====================
@admin_bp.route('/orders')
@admin_required
def admin_orders():
    try:
        conn = get_db_connection()
        
        # Filters
        dealer_filter = request.args.get('dealer', '')
        city_filter = request.args.get('city', '')
        user_filter = request.args.get('user', '')
        page = int(request.args.get('page', 1))
        per_page = 50
        
        conditions = []
        params = []
        
        if dealer_filter:
            conditions.append("dealer_name LIKE ?")
            params.append(f"%{dealer_filter}%")
        if city_filter:
            conditions.append("city LIKE ?")
            params.append(f"%{city_filter}%")
        if user_filter:
            conditions.append("username LIKE ?")
            params.append(f"%{user_filter}%")
        
        where = " AND ".join(conditions) if conditions else "1=1"
        
        total = conn.execute(f"SELECT COUNT(*) as c FROM sale_orders WHERE {where}", tuple(params)).fetchone()['c']
        
        orders = conn.execute(f"""
            SELECT * FROM sale_orders WHERE {where}
            ORDER BY generated_at DESC
            LIMIT ? OFFSET ?
        """, (*params, per_page, (page-1)*per_page)).fetchall()
        
        conn.close()
    except Exception as e:
        current_app.logger.error(f"Admin orders error: {e}")
        orders = []
        total = 0
    
    total_pages = (total + per_page - 1) // per_page
    
    orders_html = ""
    for order in orders:
        orders_html += f"""
        <tr>
            <td><strong>{order['order_id']}</strong></td>
            <td>{order['dealer_name']}</td>
            <td>{order['city']}</td>
            <td>{order['username']}</td>
            <td>{order['generated_at']}</td>
            <td>
                <a href="/download/{order['report_name']}" class="btn btn-primary">📥</a>
            </td>
        </tr>
        """
    
    # Pagination HTML
    pagination_html = ""
    if total_pages > 1:
        pagination_html = '<div style="display: flex; gap: 0.5rem; justify-content: center; margin-top: 1rem;">'
        for p in range(1, total_pages + 1):
            active = "btn-primary" if p == page else "btn-secondary"
            pagination_html += f'<a href="?page={p}&dealer={dealer_filter}&city={city_filter}&user={user_filter}" class="btn {active}">{p}</a>'
        pagination_html += '</div>'
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>All Orders - Admin</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        {get_theme_css()}
    </head>
    <body>
        <div class="admin-container">
            <div class="admin-header">
                <h1>📋 All Orders</h1>
                <p>View and manage all system orders</p>
                <nav class="admin-nav">
                    <a href="/admin">📊 Dashboard</a>
                    <a href="/admin/users">👥 Users</a>
                    <a href="/admin/orders" class="active">📋 All Orders</a>
                    <a href="/admin/sessions">🔐 Sessions</a>
                    <a href="/">🏠 Home</a>
                </nav>
            </div>
            
            <div class="admin-main">
                <!-- Filters -->
                <div class="section-card" style="margin-bottom: 1.5rem;">
                    <div class="section-body">
                        <form method="GET" style="display: flex; gap: 1rem; flex-wrap: wrap; align-items: flex-end;">
                            <div class="form-group" style="flex: 1; min-width: 150px; margin: 0;">
                                <label>Dealer Name</label>
                                <input type="text" name="dealer" value="{dealer_filter}" placeholder="Search dealer...">
                            </div>
                            <div class="form-group" style="flex: 1; min-width: 150px; margin: 0;">
                                <label>City</label>
                                <input type="text" name="city" value="{city_filter}" placeholder="Search city...">
                            </div>
                            <div class="form-group" style="flex: 1; min-width: 150px; margin: 0;">
                                <label>Username</label>
                                <input type="text" name="user" value="{user_filter}" placeholder="Search user...">
                            </div>
                            <button type="submit" class="btn btn-primary">🔍 Search</button>
                            <a href="/admin/orders" class="btn btn-secondary">↻ Reset</a>
                        </form>
                    </div>
                </div>
                
                <div class="section-card">
                    <div class="section-header">
                        <h3>Orders ({total} total)</h3>
                    </div>
                    <div style="overflow-x: auto;">
                        <table class="data-table">
                            <thead>
                                <tr>
                                    <th>Order ID</th>
                                    <th>Dealer Name</th>
                                    <th>City</th>
                                    <th>Created By</th>
                                    <th>Generated At</th>
                                    <th>Download</th>
                                </tr>
                            </thead>
                            <tbody>
                                {orders_html if orders_html else '<tr><td colspan="6" style="text-align: center; padding: 2rem;">No orders found</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                    {pagination_html}
                </div>
            </div>
        </div>
        
        <script>
            const theme = localStorage.getItem('theme') || 'default';
            document.documentElement.setAttribute('data-theme', theme);
        </script>
    </body>
    </html>
    """
    return html


# ==================== Logs View ====================
@admin_bp.route('/logs')
@admin_required
def view_logs():
    log_file = os.getenv("LOG_FILE", os.path.join(BASE_DIR, "app.log"))
    log_content = ""
    
    try:
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()[-200:]  # Last 200 lines
                log_content = ''.join(lines)
    except Exception as e:
        log_content = f"Error reading log file: {e}"
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>System Logs - Admin</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        {get_theme_css()}
        <style>
            .log-viewer {{
                background: #1e1e2e; color: #cdd6f4; padding: 1.5rem;
                border-radius: 12px; font-family: 'Fira Code', 'Consolas', monospace;
                font-size: 0.8rem; line-height: 1.6; overflow-x: auto;
                max-height: 600px; overflow-y: auto; white-space: pre-wrap;
                word-wrap: break-word;
            }}
            .log-viewer .error {{ color: #f38ba8; }}
            .log-viewer .warning {{ color: #fab387; }}
            .log-viewer .info {{ color: #89b4fa; }}
        </style>
    </head>
    <body>
        <div class="admin-container">
            <div class="admin-header">
                <h1>📝 System Logs</h1>
                <p>View application logs (last 200 lines)</p>
                <nav class="admin-nav">
                    <a href="/admin">📊 Dashboard</a>
                    <a href="/admin/users">👥 Users</a>
                    <a href="/admin/orders">📋 All Orders</a>
                    <a href="/admin/sessions">🔐 Sessions</a>
                    <a href="/admin/logs" class="active">📝 Logs</a>
                    <a href="/">🏠 Home</a>
                </nav>
            </div>
            
            <div class="admin-main">
                <div class="section-card">
                    <div class="section-header">
                        <h3>Application Logs</h3>
                        <button onclick="location.reload()" class="btn btn-secondary">↻ Refresh</button>
                    </div>
                    <div class="section-body">
                        <div class="log-viewer" id="logViewer">{log_content if log_content else 'No logs available'}</div>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            const theme = localStorage.getItem('theme') || 'default';
            document.documentElement.setAttribute('data-theme', theme);
            
            // Scroll to bottom of logs
            const logViewer = document.getElementById('logViewer');
            logViewer.scrollTop = logViewer.scrollHeight;
            
            // Highlight log levels
            logViewer.innerHTML = logViewer.innerHTML
                .replace(/ERROR/g, '<span class="error">ERROR</span>')
                .replace(/WARNING/g, '<span class="warning">WARNING</span>')
                .replace(/INFO/g, '<span class="info">INFO</span>');
        </script>
    </body>
    </html>
    """
    return html


# ==================== Users Management Placeholder ====================
@admin_bp.route('/users')
@admin_required
def manage_users():
    # Note: Users are stored in .env file, this shows a read-only view
    from flask import current_app
    
    users_html = ""
    for username, _ in current_app.config.get('USERS_DICT', {}).items():
        is_admin = username in ADMIN_USERS
        badge = '<span class="badge badge-admin">Admin</span>' if is_admin else '<span class="badge badge-user">User</span>'
        users_html += f"""
        <tr>
            <td><strong>{username}</strong></td>
            <td>{badge}</td>
            <td><span class="badge badge-active">Active</span></td>
            <td>Stored in .env</td>
        </tr>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>User Management - Admin</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        {get_theme_css()}
    </head>
    <body>
        <div class="admin-container">
            <div class="admin-header">
                <h1>👥 User Management</h1>
                <p>View and manage system users</p>
                <nav class="admin-nav">
                    <a href="/admin">📊 Dashboard</a>
                    <a href="/admin/users" class="active">👥 Users</a>
                    <a href="/admin/orders">📋 All Orders</a>
                    <a href="/admin/sessions">🔐 Sessions</a>
                    <a href="/">🏠 Home</a>
                </nav>
            </div>
            
            <div class="admin-main">
                <div class="alert alert-warning">
                    ⚠️ <strong>Note:</strong> Users are configured via environment variables (.env file). 
                    To add/remove users, update the environment configuration.
                </div>
                
                <div class="section-card">
                    <div class="section-header">
                        <h3>System Users</h3>
                    </div>
                    <div style="overflow-x: auto;">
                        <table class="data-table">
                            <thead>
                                <tr>
                                    <th>Username</th>
                                    <th>Role</th>
                                    <th>Status</th>
                                    <th>Storage</th>
                                </tr>
                            </thead>
                            <tbody>
                                {users_html if users_html else '<tr><td colspan="4" style="text-align: center; padding: 2rem;">No users configured</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>
                
                <div class="section-card">
                    <div class="section-header">
                        <h3>📖 How to Add Users</h3>
                    </div>
                    <div class="section-body">
                        <p style="margin-bottom: 1rem;">Add users in your <code>.env</code> file with the following format:</p>
                        <pre style="background: #1e1e2e; color: #cdd6f4; padding: 1rem; border-radius: 8px; overflow-x: auto;">
USER1=username1:$2b$12$hashedpassword...
USER2=username2:$2b$12$hashedpassword...
ADMIN_USERS=admin,username1
                        </pre>
                        <p style="margin-top: 1rem; color: var(--text-muted);">
                            Use <code>bcrypt</code> to generate password hashes. Admin users are specified in ADMIN_USERS as a comma-separated list.
                        </p>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            const theme = localStorage.getItem('theme') || 'default';
            document.documentElement.setAttribute('data-theme', theme);
        </script>
    </body>
    </html>
    """
    return html
