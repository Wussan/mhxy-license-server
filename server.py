from flask import Flask, request, jsonify, render_template_string, redirect, session, Response
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import datetime
import random
import string
import csv
import io
import os

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
app.secret_key = os.environ.get("SECRET_KEY", "MHXY_ADMIN_2026_SECRET")

ADMIN_USER = os.environ.get("ADMIN_USER", "huyili")
ADMIN_PWD = os.environ.get("ADMIN_PWD", "huyili10")


# ================== 工具函数 ==================
def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL 未配置，请到 Render -> Environment 添加 DATABASE_URL")
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor
    )


def is_expired(expire_at):
    if not expire_at:
        return True
    try:
        expire_time = datetime.datetime.strptime(str(expire_at), "%Y-%m-%d %H:%M:%S")
        return datetime.datetime.now() > expire_time
    except Exception:
        return True


def make_card_code(length=16):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def require_admin():
    return bool(session.get("admin_login"))


def normalize_time(value):
    if not value:
        return ""
    return str(value)


# ================== 初始化数据库 ==================
def init_db():
    conn = get_conn()
    cur = conn.cursor()



    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        device_id TEXT,
        expire_at TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cards (
        id SERIAL PRIMARY KEY,
        card_code TEXT UNIQUE NOT NULL,
        days INTEGER DEFAULT 30,
        price REAL DEFAULT 30,
        used INTEGER DEFAULT 0,
        used_by TEXT,
        used_time TEXT,
        created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id SERIAL PRIMARY KEY,
        username TEXT NOT NULL,
        card_code TEXT NOT NULL,
        days INTEGER NOT NULL,
        price REAL NOT NULL,
        old_expire_at TEXT,
        new_expire_at TEXT,
        created_at TEXT NOT NULL
    )
    """)

    conn.commit()
    cur.close()
    conn.close()


# ================== API：注册 ==================
@app.route("/api/register", methods=["POST"])
def register():
    data = request.json or {}

    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    device_id = data.get("device_id", "").strip()

    if len(username) < 2:
        return jsonify({"ok": False, "msg": "账号至少2位"})

    if len(password) < 6:
        return jsonify({"ok": False, "msg": "密码至少6位"})

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            INSERT INTO users(username, password_hash, device_id, expire_at, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (username, generate_password_hash(password), device_id, "", "active", now_str())
        )
        conn.commit()
        return jsonify({"ok": True, "msg": "注册成功，请充值后登录"})
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"ok": False, "msg": "账号已存在"})
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "msg": f"注册失败：{e}"})
    finally:
        cur.close()
        conn.close()


# ================== API：登录 ==================
@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}

    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    device_id = data.get("device_id", "").strip()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cur.fetchone()

    if not user:
        cur.close()
        conn.close()
        return jsonify({"ok": False, "msg": "账号不存在"})

    if not check_password_hash(user["password_hash"], password):
        cur.close()
        conn.close()
        return jsonify({"ok": False, "msg": "密码错误"})

    if user["status"] != "active":
        cur.close()
        conn.close()
        return jsonify({"ok": False, "msg": "账号已被封禁"})

    if is_expired(user["expire_at"]):
        cur.close()
        conn.close()
        return jsonify({"ok": False, "msg": "会员已过期，请充值"})

    old_device = user["device_id"]

    if old_device and old_device != device_id:
        cur.close()
        conn.close()
        return jsonify({"ok": False, "msg": "该账号已绑定其他电脑"})

    if not old_device:
        cur.execute(
            "UPDATE users SET device_id = %s WHERE username = %s",
            (device_id, username)
        )
        conn.commit()

    expire_at = user["expire_at"]

    cur.close()
    conn.close()

    return jsonify({
        "ok": True,
        "msg": "登录成功",
        "expire_at": expire_at
    })


# ================== API：充值 ==================
@app.route("/api/recharge", methods=["POST"])
def recharge():
    data = request.json or {}

    username = data.get("username", "").strip()
    card_code = data.get("card", "").strip()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cur.fetchone()

    cur.execute("SELECT * FROM cards WHERE card_code = %s", (card_code,))
    card = cur.fetchone()

    if not user:
        cur.close()
        conn.close()
        return jsonify({"ok": False, "msg": "账号不存在"})

    if not card:
        cur.close()
        conn.close()
        return jsonify({"ok": False, "msg": "卡密无效"})

    if int(card["used"]) == 1:
        cur.close()
        conn.close()
        return jsonify({"ok": False, "msg": "卡密已被使用"})

    old_expire_at = user["expire_at"]

    if old_expire_at and not is_expired(old_expire_at):
        base_time = datetime.datetime.strptime(str(old_expire_at), "%Y-%m-%d %H:%M:%S")
    else:
        base_time = datetime.datetime.now()

    new_expire = base_time + datetime.timedelta(days=int(card["days"]))
    new_expire_str = new_expire.strftime("%Y-%m-%d %H:%M:%S")

    try:
        cur.execute(
            "UPDATE users SET expire_at = %s WHERE username = %s",
            (new_expire_str, username)
        )

        cur.execute(
            "UPDATE cards SET used = 1, used_by = %s, used_time = %s WHERE card_code = %s",
            (username, now_str(), card_code)
        )

        cur.execute(
            """
            INSERT INTO orders(username, card_code, days, price, old_expire_at, new_expire_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                username,
                card_code,
                int(card["days"]),
                float(card["price"]),
                normalize_time(old_expire_at),
                new_expire_str,
                now_str()
            )
        )

        conn.commit()
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"ok": False, "msg": f"充值失败：{e}"})

    cur.close()
    conn.close()

    return jsonify({
        "ok": True,
        "msg": f"充值成功，到期时间：{new_expire_str}",
        "expire_at": new_expire_str,
        "expire": new_expire_str
    })


# ================== API：授权检查 ==================
@app.route("/api/check", methods=["POST"])
def check():
    data = request.json or {}

    username = data.get("username", "").strip()
    device_id = data.get("device_id", "").strip()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cur.fetchone()

    cur.close()
    conn.close()

    if not user:
        return jsonify({"ok": False, "msg": "账号不存在"})

    if user["status"] != "active":
        return jsonify({"ok": False, "msg": "账号已被封禁"})

    if user["device_id"] and user["device_id"] != device_id:
        return jsonify({"ok": False, "msg": "机器码不匹配"})

    if is_expired(user["expire_at"]):
        return jsonify({"ok": False, "msg": "会员已过期"})

    return jsonify({
        "ok": True,
        "msg": "授权有效",
        "expire_at": user["expire_at"]
    })


# ================== 后台 HTML ==================
ADMIN_LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>管理员登录</title>
</head>
<body style="font-family:微软雅黑;padding:50px;text-align:center;background:#f4f6f8;">
    <div style="background:white;max-width:360px;margin:80px auto;padding:30px;border-radius:12px;box-shadow:0 2px 10px #ddd;">
        <h2>梦幻工具箱 - 管理员登录</h2>
        <form method="post" action="/admin/login">
            <p>账号：<input name="username" style="padding:8px;width:200px;"></p>
            <p>密码：<input name="password" type="password" style="padding:8px;width:200px;"></p>
            <p><button type="submit" style="padding:8px 30px;">登录</button></p>
        </form>
    </div>
</body>
</html>
"""

ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>梦幻工具箱 - 专业后台</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: 微软雅黑, Arial;
            margin: 0;
            padding: 0;
            background: #f3f6fb;
            color: #1f2937;
        }
        .layout {
            display: flex;
            min-height: 100vh;
        }
        .sidebar {
            width: 230px;
            background: linear-gradient(180deg, #0a1a40, #122866);
            color: white;
            padding: 24px 18px;
        }
        .logo {
            font-size: 22px;
            font-weight: bold;
            margin-bottom: 8px;
        }
        .subtitle {
            color: #9bbcff;
            font-size: 13px;
            margin-bottom: 30px;
        }
        .nav-item {
    display: block;
    padding: 12px 14px;
    border-radius: 10px;
    margin-bottom: 8px;
    background: rgba(255,255,255,0.08);
    color: white;
    text-decoration: none;
}
.nav-item:hover {
    background: rgba(255,255,255,0.16);
}
        .main {
            flex: 1;
            padding: 24px;
            overflow-x: auto;
        }
        .topbar {
            display:flex;
            justify-content:space-between;
            align-items:center;
            margin-bottom:20px;
        }
        .topbar h2 {
            margin: 0;
            font-size: 24px;
        }
        .links a {
            color: #2563eb;
            text-decoration: none;
            margin-left: 12px;
            font-size: 14px;
        }
        .cards {
            display: grid;
            grid-template-columns: repeat(4, minmax(150px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }
        .card {
            background: white;
            padding: 18px;
            border-radius: 14px;
            box-shadow: 0 6px 18px rgba(15,23,42,0.08);
            border: 1px solid #e5e7eb;
        }
        .card-title {
            color: #64748b;
            font-size: 14px;
        }
        .num {
            font-size: 28px;
            font-weight: bold;
            color: #1677ff;
            margin-top:8px;
        }
        .form-box {
            background: white;
            padding: 18px;
            border-radius: 14px;
            margin-bottom: 18px;
            box-shadow: 0 6px 18px rgba(15,23,42,0.08);
            border: 1px solid #e5e7eb;
        }
        .form-box h3 {
            margin-top: 0;
        }
        input, button, select {
            padding: 8px 10px;
            margin: 4px;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
        }
        button {
            background: #1677ff;
            color: white;
            border: none;
            cursor: pointer;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            background: white;
            margin: 12px 0 28px;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 6px 18px rgba(15,23,42,0.06);
        }
        th, td {
            border-bottom: 1px solid #e5e7eb;
            padding: 10px 8px;
            font-size: 13px;
            text-align: left;
            word-break: break-all;
        }
        th {
            background: #eef3ff;
            font-weight: bold;
        }
        a {
            color: #1677ff;
            text-decoration: none;
            margin-right: 8px;
        }
        .danger { color: #dc2626; }
        .ok { color: #16a34a; font-weight:bold; }
        .bad { color: #dc2626; font-weight:bold; }
        .small {
            color:#64748b;
            font-size:12px;
        }
        @media (max-width: 900px) {
            .layout { display: block; }
            .sidebar { width: 100%; }
            .cards { grid-template-columns: repeat(2, 1fr); }
            .main { padding: 14px; }
        }
    </style>
</head>
<body>
<div class="layout">
    <div class="sidebar">
        <div class="logo">梦幻工具箱</div>
        <div class="subtitle">专业授权管理后台</div>
        <a href="#dashboard" class="nav-item">📊 数据总览</a>
        <a href="#users" class="nav-item">👤 用户管理</a>
        <a href="#cards" class="nav-item">🎫 卡密管理</a>
        <a href="#orders" class="nav-item">💰 订单统计</a>
    </div>

    <div class="main">
        <div class="topbar">
            <h2>梦幻工具箱 - 专业管理后台</h2>
            <div class="links">
                <a href="/admin/export_orders">导出订单CSV</a>
                <a href="/admin/export_cards">导出卡密CSV</a>
                <a href="/admin/logout">退出登录</a>
            </div>
        </div>

        <div id="dashboard" class="cards">
            <div class="card"><div class="card-title">用户总数</div><div class="num">{{ user_count }}</div></div>
            <div class="card"><div class="card-title">有效会员</div><div class="num">{{ active_user_count }}</div></div>
            <div class="card"><div class="card-title">卡密总数</div><div class="num">{{ total_cards }}</div></div>
            <div class="card"><div class="card-title">已用卡密</div><div class="num">{{ used_cards }}</div></div>
            <div class="card"><div class="card-title">未用卡密</div><div class="num">{{ unused_cards }}</div></div>
            <div class="card"><div class="card-title">总收入</div><div class="num">￥{{ "%.2f"|format(total_income) }}</div></div>
            <div class="card"><div class="card-title">今日收入</div><div class="num">￥{{ "%.2f"|format(today_income) }}</div></div>
            <div class="card"><div class="card-title">本月收入</div><div class="num">￥{{ "%.2f"|format(month_income) }}</div></div>
        </div>

        <div class="form-box">
            <h3>用户搜索 / 到期筛选</h3>
            <form method="get" action="/admin">
                关键词：<input name="q" value="{{ q }}" placeholder="账号 / 机器码 / 状态">
                用户状态：
                <select name="status">
                    <option value="" {% if status == "" %}selected{% endif %}>全部</option>
                    <option value="active" {% if status == "active" %}selected{% endif %}>正常</option>
                    <option value="banned" {% if status == "banned" %}selected{% endif %}>封禁</option>
                </select>
                到期筛选：
                <select name="expire_filter">
                    <option value="" {% if expire_filter == "" %}selected{% endif %}>全部</option>
                    <option value="valid" {% if expire_filter == "valid" %}selected{% endif %}>有效会员</option>
                    <option value="expired" {% if expire_filter == "expired" %}selected{% endif %}>已过期/未充值</option>
                    <option value="soon7" {% if expire_filter == "soon7" %}selected{% endif %}>7天内到期</option>
                    <option value="soon3" {% if expire_filter == "soon3" %}selected{% endif %}>3天内到期</option>
                </select>
                <button type="submit">搜索</button>
                <a href="/admin">清空筛选</a>
            </form>
        </div>

        <div class="form-box">
            <h3>即将到期会员提醒</h3>
            {% if expiring_users %}
            <table>
                <tr><th>账号</th><th>到期时间</th><th>机器码</th><th>操作</th></tr>
                {% for u in expiring_users %}
                <tr>
                    <td>{{ u.username }}</td>
                    <td>{{ u.expire_at }}</td>
                    <td>{{ u.device_id if u.device_id else "未绑定" }}</td>
                    <td>
                        <a href="/admin/renew_user?username={{ u.username }}&days=30&price=30">续费30天并记收入30</a>
                        <a href="/admin/renew_user?username={{ u.username }}&days=30&price=0">免费补偿30天</a>
                    </td>
                </tr>
                {% endfor %}
            </table>
            {% else %}
            <div class="small">暂无7天内到期会员。</div>
            {% endif %}
        </div>

        <div class="form-box">
            <h3>批量生成卡密</h3>
            <form method="post" action="/admin/create_card">
                数量：<input name="count" value="1" style="width:70px;">
                天数：<input name="days" value="30" style="width:70px;">
                金额：<input name="price" value="30" style="width:70px;">
                <button type="submit">生成卡密</button>
            </form>
            <div class="small">30元/月就填：天数=30，金额=30。</div>
        </div>

        <div class="form-box">
            <h3>手动续费用户</h3>
            <form method="post" action="/admin/renew_user_post">
                用户名：<input name="username" placeholder="输入账号">
                续费天数：<input name="days" value="30" style="width:80px;">
                金额：<input name="price" value="0" style="width:80px;">
                <button type="submit">手动续费</button>
            </form>
        </div>

        <h3 id="users">用户列表{% if q or status or expire_filter %}（筛选结果）{% endif %}</h3>
        <table>
            <tr>
                <th>ID</th><th>账号</th><th>到期时间</th><th>会员状态</th><th>账号状态</th><th>机器码</th><th>注册时间</th><th>操作</th>
            </tr>
            {% for u in users %}
            <tr>
                <td>{{ u.id }}</td>
                <td>{{ u.username }}</td>
                <td>{{ u.expire_at if u.expire_at else "未充值" }}</td>
                <td>
                    {% if u.expire_at and u.expire_at > now and u.status == "active" %}
                        <span class="ok">有效</span>
                    {% else %}
                        <span class="bad">无效/过期</span>
                    {% endif %}
                </td>
                <td>{{ u.status }}</td>
                <td>{{ u.device_id if u.device_id else "未绑定" }}</td>
                <td>{{ u.created_at }}</td>
                <td>
                    <a href="/admin/renew_user?username={{ u.username }}&days=30&price=0">续费30天</a>
                    <a href="/admin/reset_device?username={{ u.username }}">重置机器码</a>
                    {% if u.status == "active" %}
                        <a class="danger" href="/admin/ban_user?username={{ u.username }}">封号</a>
                    {% else %}
                        <a href="/admin/unban_user?username={{ u.username }}">解封</a>
                    {% endif %}
                    <a class="danger" href="/admin/delete_user?username={{ u.username }}" onclick="return confirm('确定删除该用户？')">删除</a>
                </td>
            </tr>
            {% endfor %}
        </table>

        <h3 id="cards">卡密列表</h3>
        <table>
            <tr>
                <th>ID</th><th>卡密</th><th>天数</th><th>金额</th><th>是否使用</th><th>使用者</th><th>使用时间</th><th>创建时间</th><th>操作</th>
            </tr>
            {% for c in cards %}
            <tr>
                <td>{{ c.id }}</td>
                <td>{{ c.card_code }}</td>
                <td>{{ c.days }}</td>
                <td>￥{{ "%.2f"|format(c.price or 0) }}</td>
                <td>{{ "是" if c.used else "否" }}</td>
                <td>{{ c.used_by if c.used_by else "—" }}</td>
                <td>{{ c.used_time if c.used_time else "—" }}</td>
                <td>{{ c.created_at }}</td>
                <td>
                    <a href="/admin/reset_card?card_code={{ c.card_code }}">重置</a>
                    <a class="danger" href="/admin/delete_card?card_code={{ c.card_code }}" onclick="return confirm('确定删除该卡密？')">删除</a>
                </td>
            </tr>
            {% endfor %}
        </table>

        <h3 id="orders">最近100条订单记录</h3>
        <table>
            <tr>
                <th>ID</th><th>账号</th><th>卡密/来源</th><th>天数</th><th>金额</th><th>旧到期时间</th><th>新到期时间</th><th>充值时间</th>
            </tr>
            {% for o in orders %}
            <tr>
                <td>{{ o.id }}</td>
                <td>{{ o.username }}</td>
                <td>{{ o.card_code }}</td>
                <td>{{ o.days }}</td>
                <td>￥{{ "%.2f"|format(o.price or 0) }}</td>
                <td>{{ o.old_expire_at }}</td>
                <td>{{ o.new_expire_at }}</td>
                <td>{{ o.created_at }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
</div>
</body>
</html>
"""


# ================== 后台路由 ==================
@app.route("/")
def root():
    return redirect("/admin")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return render_template_string(ADMIN_LOGIN_HTML)

    username = request.form.get("username", "")
    password = request.form.get("password", "")

    if username == ADMIN_USER and password == ADMIN_PWD:
        session["admin_login"] = True
        return redirect("/admin")

    return "管理员账号或密码错误"


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")


@app.route("/admin")
def admin_index():
    if not require_admin():
        return redirect("/admin/login")

    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    expire_filter = request.args.get("expire_filter", "").strip()

    conn = get_conn()
    cur = conn.cursor()

    now = now_str()
    now_dt = datetime.datetime.now()
    soon7 = (now_dt + datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    soon3 = (now_dt + datetime.timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")

    user_sql = "SELECT * FROM users WHERE 1=1"
    params = []

    if q:
        user_sql += " AND (username ILIKE %s OR device_id ILIKE %s OR status ILIKE %s)"
        like_q = f"%{q}%"
        params.extend([like_q, like_q, like_q])

    if status:
        user_sql += " AND status = %s"
        params.append(status)

    if expire_filter == "valid":
        user_sql += " AND expire_at != '' AND expire_at > %s AND status = 'active'"
        params.append(now)
    elif expire_filter == "expired":
        user_sql += " AND (expire_at = '' OR expire_at <= %s OR status != 'active')"
        params.append(now)
    elif expire_filter == "soon7":
        user_sql += " AND expire_at != '' AND expire_at > %s AND expire_at <= %s AND status = 'active'"
        params.extend([now, soon7])
    elif expire_filter == "soon3":
        user_sql += " AND expire_at != '' AND expire_at > %s AND expire_at <= %s AND status = 'active'"
        params.extend([now, soon3])

    user_sql += " ORDER BY id DESC"
    cur.execute(user_sql, tuple(params))
    users = cur.fetchall()

    cur.execute(
        "SELECT * FROM users WHERE expire_at != '' AND expire_at > %s AND expire_at <= %s AND status = 'active' ORDER BY expire_at ASC LIMIT 50",
        (now, soon7)
    )
    expiring_users = cur.fetchall()

    cur.execute("SELECT * FROM cards ORDER BY id DESC LIMIT 300")
    cards = cur.fetchall()

    cur.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100")
    orders = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS n FROM cards")
    total_cards = cur.fetchone()["n"]

    cur.execute("SELECT COUNT(*) AS n FROM cards WHERE used = 1")
    used_cards = cur.fetchone()["n"]

    cur.execute("SELECT COUNT(*) AS n FROM users")
    user_count = cur.fetchone()["n"]

    cur.execute(
        "SELECT COUNT(*) AS n FROM users WHERE expire_at != '' AND expire_at > %s AND status = 'active'",
        (now,)
    )
    active_user_count = cur.fetchone()["n"]

    cur.execute("SELECT SUM(price) AS total FROM orders")
    total_income_row = cur.fetchone()
    total_income = total_income_row["total"] if total_income_row["total"] else 0

    today_prefix = datetime.datetime.now().strftime("%Y-%m-%d")
    cur.execute(
        "SELECT SUM(price) AS total FROM orders WHERE created_at LIKE %s",
        (today_prefix + "%",)
    )
    today_income_row = cur.fetchone()
    today_income = today_income_row["total"] if today_income_row["total"] else 0

    month_prefix = datetime.datetime.now().strftime("%Y-%m")
    cur.execute(
        "SELECT SUM(price) AS total FROM orders WHERE created_at LIKE %s",
        (month_prefix + "%",)
    )
    month_income_row = cur.fetchone()
    month_income = month_income_row["total"] if month_income_row["total"] else 0

    cur.close()
    conn.close()

    return render_template_string(
        ADMIN_HTML,
        users=users,
        cards=cards,
        orders=orders,
        expiring_users=expiring_users,
        q=q,
        status=status,
        expire_filter=expire_filter,
        now=now,
        total_cards=total_cards,
        used_cards=used_cards,
        unused_cards=total_cards - used_cards,
        user_count=user_count,
        active_user_count=active_user_count,
        total_income=float(total_income),
        today_income=float(today_income),
        month_income=float(month_income)
    )


@app.route("/admin/create_card", methods=["GET", "POST"])
def admin_create_card():
    if not require_admin():
        return redirect("/admin/login")

    conn = get_conn()
    cur = conn.cursor()

    if request.method == "GET":
        card = make_card_code()
        cur.execute(
            "INSERT INTO cards(card_code, days, price, used, created_at) VALUES (%s, %s, %s, %s, %s)",
            (card, 30, 30, 0, now_str())
        )
        conn.commit()
        cur.close()
        conn.close()
        return f"新卡密：{card}"

    count = int(request.form.get("count", 1))
    days = int(request.form.get("days", 30))
    price = float(request.form.get("price", 30))

    count = max(1, min(count, 500))

    for _ in range(count):
        while True:
            card = make_card_code()
            cur.execute("SELECT id FROM cards WHERE card_code = %s", (card,))
            exists = cur.fetchone()
            if not exists:
                break

        cur.execute(
            "INSERT INTO cards(card_code, days, price, used, created_at) VALUES (%s, %s, %s, %s, %s)",
            (card, days, price, 0, now_str())
        )

    conn.commit()
    cur.close()
    conn.close()

    return redirect("/admin")


@app.route("/admin/ban_user")
def admin_ban_user():
    if not require_admin():
        return redirect("/admin/login")
    username = request.args.get("username", "")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET status = 'banned' WHERE username = %s", (username,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect("/admin")


@app.route("/admin/unban_user")
def admin_unban_user():
    if not require_admin():
        return redirect("/admin/login")
    username = request.args.get("username", "")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET status = 'active' WHERE username = %s", (username,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect("/admin")


@app.route("/admin/delete_user")
def admin_delete_user():
    if not require_admin():
        return redirect("/admin/login")
    username = request.args.get("username", "")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE username = %s", (username,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect("/admin")


@app.route("/admin/reset_device")
def admin_reset_device():
    if not require_admin():
        return redirect("/admin/login")
    username = request.args.get("username", "")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET device_id = '' WHERE username = %s", (username,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect("/admin")


@app.route("/admin/renew_user")
def admin_renew_user():
    if not require_admin():
        return redirect("/admin/login")
    username = request.args.get("username", "")
    days = int(request.args.get("days", 30))
    price = float(request.args.get("price", 0))
    renew_user_internal(username, days, price)
    return redirect("/admin")


@app.route("/admin/renew_user_post", methods=["POST"])
def admin_renew_user_post():
    if not require_admin():
        return redirect("/admin/login")
    username = request.form.get("username", "").strip()
    days = int(request.form.get("days", 30))
    price = float(request.form.get("price", 0))
    renew_user_internal(username, days, price)
    return redirect("/admin")


def renew_user_internal(username, days, price):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cur.fetchone()

    if user:
        old_expire_at = user["expire_at"]

        if old_expire_at and not is_expired(old_expire_at):
            base_time = datetime.datetime.strptime(str(old_expire_at), "%Y-%m-%d %H:%M:%S")
        else:
            base_time = datetime.datetime.now()

        new_expire_str = (base_time + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

        cur.execute(
            "UPDATE users SET expire_at = %s WHERE username = %s",
            (new_expire_str, username)
        )

        cur.execute(
            """
            INSERT INTO orders(username, card_code, days, price, old_expire_at, new_expire_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                username,
                "管理员手动续费",
                days,
                price,
                normalize_time(old_expire_at),
                new_expire_str,
                now_str()
            )
        )

        conn.commit()

    cur.close()
    conn.close()


@app.route("/admin/reset_card")
def admin_reset_card():
    if not require_admin():
        return redirect("/admin/login")
    card_code = request.args.get("card_code", "")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE cards SET used = 0, used_by = '', used_time = '' WHERE card_code = %s",
        (card_code,)
    )
    conn.commit()
    cur.close()
    conn.close()
    return redirect("/admin")


@app.route("/admin/delete_card")
def admin_delete_card():
    if not require_admin():
        return redirect("/admin/login")
    card_code = request.args.get("card_code", "")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM cards WHERE card_code = %s", (card_code,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect("/admin")


@app.route("/admin/export_orders")
def admin_export_orders():
    if not require_admin():
        return redirect("/admin/login")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders ORDER BY id DESC")
    orders = cur.fetchall()
    cur.close()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "账号", "卡密/来源", "天数", "金额", "旧到期时间", "新到期时间", "充值时间"])

    for o in orders:
        writer.writerow([
            o["id"], o["username"], o["card_code"], o["days"], o["price"],
            o["old_expire_at"], o["new_expire_at"], o["created_at"]
        ])

    output.seek(0)

    return Response(
        output,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=orders.csv"}
    )


@app.route("/admin/export_cards")
def admin_export_cards():
    if not require_admin():
        return redirect("/admin/login")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM cards ORDER BY id DESC")
    cards = cur.fetchall()
    cur.close()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "卡密", "天数", "金额", "是否使用", "使用者", "使用时间", "创建时间"])

    for c in cards:
        writer.writerow([
            c["id"], c["card_code"], c["days"], c["price"],
            "是" if c["used"] else "否",
            c["used_by"], c["used_time"], c["created_at"]
        ])

    output.seek(0)

    return Response(
        output,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=cards.csv"}
    )


# ================== 启动 ==================
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
