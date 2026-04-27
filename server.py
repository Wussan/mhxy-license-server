from flask import Flask, request, jsonify, render_template_string, redirect, session, Response
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import datetime
import random
import string
import csv
import io
import os

app = Flask(__name__)

# ================== 基础配置 ==================
DB_PATH = "license.db"

# Render / 云端建议后期改成环境变量
app.secret_key = os.environ.get("SECRET_KEY", "MHXY_ADMIN_2026_SECRET")

# 管理员账号密码：你可以自己改
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PWD = os.environ.get("ADMIN_PWD", "admin123")


# ================== 通用工具函数 ==================
def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row):
    return dict(row) if row else None


def is_expired(expire_at):
    if not expire_at:
        return True

    try:
        expire_time = datetime.datetime.strptime(expire_at, "%Y-%m-%d %H:%M:%S")
        return datetime.datetime.now() > expire_time
    except Exception:
        return True


def make_card_code(length=16):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def require_admin():
    return bool(session.get("admin_login"))


# ================== 数据库初始化/升级 ==================
def column_exists(conn, table_name, column_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # 用户表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        device_id TEXT,
        expire_at TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL
    )
    """)

    # 卡密表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_code TEXT UNIQUE NOT NULL,
        days INTEGER DEFAULT 30,
        price REAL DEFAULT 30,
        used INTEGER DEFAULT 0,
        used_by TEXT,
        used_time TEXT,
        created_at TEXT NOT NULL
    )
    """)

    # 订单表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        card_code TEXT NOT NULL,
        days INTEGER NOT NULL,
        price REAL NOT NULL,
        old_expire_at TEXT,
        new_expire_at TEXT,
        created_at TEXT NOT NULL
    )
    """)

    # 兼容旧数据库：如果旧 cards 表没有 price 字段，自动添加
    if not column_exists(conn, "cards", "price"):
        cur.execute("ALTER TABLE cards ADD COLUMN price REAL DEFAULT 30")

    conn.commit()
    conn.close()


# ================== 客户端 API：注册 ==================
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

    try:
        conn.execute(
            "INSERT INTO users(username, password_hash, device_id, expire_at, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                username,
                generate_password_hash(password),
                device_id,
                "",
                "active",
                now_str()
            )
        )
        conn.commit()
        return jsonify({"ok": True, "msg": "注册成功，请充值后登录"})

    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "msg": "账号已存在"})

    finally:
        conn.close()


# ================== 客户端 API：登录 ==================
@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}

    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    device_id = data.get("device_id", "").strip()

    conn = get_conn()
    user = conn.execute(
        "SELECT * FROM users WHERE username = ?",
        (username,)
    ).fetchone()

    if not user:
        conn.close()
        return jsonify({"ok": False, "msg": "账号不存在"})

    if not check_password_hash(user["password_hash"], password):
        conn.close()
        return jsonify({"ok": False, "msg": "密码错误"})

    if user["status"] != "active":
        conn.close()
        return jsonify({"ok": False, "msg": "账号已被封禁"})

    if is_expired(user["expire_at"]):
        conn.close()
        return jsonify({"ok": False, "msg": "会员已过期，请充值"})

    old_device = user["device_id"]

    if old_device and old_device != device_id:
        conn.close()
        return jsonify({"ok": False, "msg": "该账号已绑定其他电脑"})

    if not old_device:
        conn.execute(
            "UPDATE users SET device_id = ? WHERE username = ?",
            (device_id, username)
        )
        conn.commit()

    conn.close()

    return jsonify({
        "ok": True,
        "msg": "登录成功",
        "expire_at": user["expire_at"]
    })


# ================== 客户端 API：卡密充值 ==================
@app.route("/api/recharge", methods=["POST"])
def recharge():
    data = request.json or {}

    username = data.get("username", "").strip()
    card_code = data.get("card", "").strip()

    conn = get_conn()

    user = conn.execute(
        "SELECT * FROM users WHERE username = ?",
        (username,)
    ).fetchone()

    card = conn.execute(
        "SELECT * FROM cards WHERE card_code = ?",
        (card_code,)
    ).fetchone()

    if not user:
        conn.close()
        return jsonify({"ok": False, "msg": "账号不存在"})

    if not card:
        conn.close()
        return jsonify({"ok": False, "msg": "卡密无效"})

    if card["used"] == 1:
        conn.close()
        return jsonify({"ok": False, "msg": "卡密已被使用"})

    now = datetime.datetime.now()
    old_expire_at = user["expire_at"]

    if old_expire_at and not is_expired(old_expire_at):
        base_time = datetime.datetime.strptime(
            old_expire_at,
            "%Y-%m-%d %H:%M:%S"
        )
    else:
        base_time = now

    new_expire = base_time + datetime.timedelta(days=card["days"])
    new_expire_str = new_expire.strftime("%Y-%m-%d %H:%M:%S")

    conn.execute(
        "UPDATE users SET expire_at = ? WHERE username = ?",
        (new_expire_str, username)
    )

    conn.execute(
        "UPDATE cards SET used = 1, used_by = ?, used_time = ? WHERE card_code = ?",
        (username, now_str(), card_code)
    )

    conn.execute(
        "INSERT INTO orders(username, card_code, days, price, old_expire_at, new_expire_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            username,
            card_code,
            card["days"],
            card["price"],
            old_expire_at,
            new_expire_str,
            now_str()
        )
    )

    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "msg": f"充值成功，到期时间：{new_expire_str}",
        "expire_at": new_expire_str
    })


# ================== 客户端 API：授权检查 ==================
@app.route("/api/check", methods=["POST"])
def check():
    data = request.json or {}

    username = data.get("username", "").strip()
    device_id = data.get("device_id", "").strip()

    conn = get_conn()

    user = conn.execute(
        "SELECT * FROM users WHERE username = ?",
        (username,)
    ).fetchone()

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


# ================== 管理员后台 HTML ==================
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
        body {
            font-family: 微软雅黑, Arial;
            padding: 20px;
            background: #f4f6f8;
            color: #222;
        }
        h2, h3 {
            color: #222;
        }
        .topbar {
            display:flex;
            justify-content:space-between;
            align-items:center;
            margin-bottom:16px;
        }
        .cards {
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            margin-bottom: 20px;
        }
        .card {
            background: white;
            padding: 16px;
            border-radius: 10px;
            box-shadow: 0 2px 8px #ddd;
            min-width: 150px;
        }
        .num {
            font-size: 24px;
            font-weight: bold;
            color: #1677ff;
            margin-top:8px;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            background: white;
            margin: 15px 0 30px;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 8px;
            font-size: 13px;
            word-break: break-all;
        }
        th {
            background: #eef3ff;
        }
        a {
            color: #1677ff;
            text-decoration: none;
            margin-right: 8px;
        }
        .danger {
            color: red;
        }
        .ok {
            color: green;
            font-weight:bold;
        }
        .bad {
            color: red;
            font-weight:bold;
        }
        .form-box {
            background: white;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px #ddd;
        }
        input, button, select {
            padding: 6px 10px;
            margin: 4px;
        }
        button {
            cursor:pointer;
        }
        .small {
            color:#666;
            font-size:12px;
        }
    </style>
</head>
<body>

<div class="topbar">
    <h2>梦幻工具箱 - 专业管理后台</h2>
    <div>
        <a href="/admin/export_orders">导出订单CSV</a>
        <a href="/admin/export_cards">导出卡密CSV</a>
        <a href="/admin/logout">退出登录</a>
    </div>
</div>

<div class="cards">
    <div class="card">
        <div>用户总数</div>
        <div class="num">{{ user_count }}</div>
    </div>
    <div class="card">
        <div>有效会员</div>
        <div class="num">{{ active_user_count }}</div>
    </div>
    <div class="card">
        <div>卡密总数</div>
        <div class="num">{{ total_cards }}</div>
    </div>
    <div class="card">
        <div>已用卡密</div>
        <div class="num">{{ used_cards }}</div>
    </div>
    <div class="card">
        <div>未用卡密</div>
        <div class="num">{{ unused_cards }}</div>
    </div>
    <div class="card">
        <div>总收入</div>
        <div class="num">￥{{ "%.2f"|format(total_income) }}</div>
    </div>
    <div class="card">
        <div>今日收入</div>
        <div class="num">￥{{ "%.2f"|format(today_income) }}</div>
    </div>
    <div class="card">
        <div>本月收入</div>
        <div class="num">￥{{ "%.2f"|format(month_income) }}</div>
    </div>
</div>

<div class="form-box">
    <h3>批量生成卡密</h3>
    <form method="post" action="/admin/create_card">
        数量：<input name="count" value="1" style="width:60px;">
        天数：<input name="days" value="30" style="width:60px;">
        金额：<input name="price" value="30" style="width:60px;">
        <button type="submit">生成卡密</button>
    </form>
    <div class="small">说明：30元/月就填 天数=30，金额=30。</div>
</div>

<div class="form-box">
    <h3>手动续费用户</h3>
    <form method="post" action="/admin/renew_user_post">
        用户名：<input name="username" placeholder="输入账号">
        续费天数：<input name="days" value="30" style="width:80px;">
        金额：<input name="price" value="0" style="width:80px;">
        <button type="submit">手动续费</button>
    </form>
    <div class="small">说明：如果是补偿用户，金额填0；如果是人工收款续费，金额填30。</div>
</div>

<h3>用户列表</h3>
<table>
<tr>
    <th>ID</th>
    <th>账号</th>
    <th>到期时间</th>
    <th>会员状态</th>
    <th>账号状态</th>
    <th>机器码</th>
    <th>注册时间</th>
    <th>操作</th>
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

<h3>卡密列表</h3>
<table>
<tr>
    <th>ID</th>
    <th>卡密</th>
    <th>天数</th>
    <th>金额</th>
    <th>是否使用</th>
    <th>使用者</th>
    <th>使用时间</th>
    <th>创建时间</th>
    <th>操作</th>
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

<h3>最近100条订单记录</h3>
<table>
<tr>
    <th>ID</th>
    <th>账号</th>
    <th>卡密/来源</th>
    <th>天数</th>
    <th>金额</th>
    <th>旧到期时间</th>
    <th>新到期时间</th>
    <th>充值时间</th>
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

</body>
</html>
"""


# ================== 管理员登录/退出 ==================
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


# ================== 管理员后台首页 ==================
@app.route("/admin")
def admin_index():
    if not require_admin():
        return redirect("/admin/login")

    conn = get_conn()

    users = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    cards = conn.execute("SELECT * FROM cards ORDER BY id DESC LIMIT 300").fetchall()
    orders = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100").fetchall()

    total_cards = conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
    used_cards = conn.execute("SELECT COUNT(*) AS n FROM cards WHERE used = 1").fetchone()["n"]
    user_count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

    now = now_str()
    active_user_count = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE expire_at != '' AND expire_at > ? AND status = 'active'",
        (now,)
    ).fetchone()["n"]

    total_income_row = conn.execute("SELECT SUM(price) AS total FROM orders").fetchone()
    total_income = total_income_row["total"] if total_income_row["total"] else 0

    today_prefix = datetime.datetime.now().strftime("%Y-%m-%d")
    today_income_row = conn.execute(
        "SELECT SUM(price) AS total FROM orders WHERE created_at LIKE ?",
        (today_prefix + "%",)
    ).fetchone()
    today_income = today_income_row["total"] if today_income_row["total"] else 0

    month_prefix = datetime.datetime.now().strftime("%Y-%m")
    month_income_row = conn.execute(
        "SELECT SUM(price) AS total FROM orders WHERE created_at LIKE ?",
        (month_prefix + "%",)
    ).fetchone()
    month_income = month_income_row["total"] if month_income_row["total"] else 0

    conn.close()

    return render_template_string(
        ADMIN_HTML,
        users=users,
        cards=cards,
        orders=orders,
        now=now,
        total_cards=total_cards,
        used_cards=used_cards,
        unused_cards=total_cards - used_cards,
        user_count=user_count,
        active_user_count=active_user_count,
        total_income=total_income,
        today_income=today_income,
        month_income=month_income
    )


# ================== 管理员：批量生成卡密 ==================
@app.route("/admin/create_card", methods=["GET", "POST"])
def admin_create_card():
    # 兼容旧入口：如果没有登录，GET也可以生成一张卡密
    # 但正式后台建议必须登录后操作
    if request.method == "GET":
        if not require_admin():
            return redirect("/admin/login")

        conn = get_conn()
        card = make_card_code()
        conn.execute(
            "INSERT INTO cards(card_code, days, price, used, created_at) VALUES (?, ?, ?, ?, ?)",
            (card, 30, 30, 0, now_str())
        )
        conn.commit()
        conn.close()
        return f"新卡密：{card}"

    if not require_admin():
        return redirect("/admin/login")

    count = int(request.form.get("count", 1))
    days = int(request.form.get("days", 30))
    price = float(request.form.get("price", 30))

    if count < 1:
        count = 1

    if count > 500:
        count = 500

    conn = get_conn()

    for _ in range(count):
        # 避免极小概率重复
        while True:
            card = make_card_code()
            exists = conn.execute(
                "SELECT id FROM cards WHERE card_code = ?",
                (card,)
            ).fetchone()
            if not exists:
                break

        conn.execute(
            "INSERT INTO cards(card_code, days, price, used, created_at) VALUES (?, ?, ?, ?, ?)",
            (card, days, price, 0, now_str())
        )

    conn.commit()
    conn.close()

    return redirect("/admin")


# ================== 管理员：用户操作 ==================
@app.route("/admin/ban_user")
def admin_ban_user():
    if not require_admin():
        return redirect("/admin/login")

    username = request.args.get("username", "")

    conn = get_conn()
    conn.execute("UPDATE users SET status = 'banned' WHERE username = ?", (username,))
    conn.commit()
    conn.close()

    return redirect("/admin")


@app.route("/admin/unban_user")
def admin_unban_user():
    if not require_admin():
        return redirect("/admin/login")

    username = request.args.get("username", "")

    conn = get_conn()
    conn.execute("UPDATE users SET status = 'active' WHERE username = ?", (username,))
    conn.commit()
    conn.close()

    return redirect("/admin")


@app.route("/admin/delete_user")
def admin_delete_user():
    if not require_admin():
        return redirect("/admin/login")

    username = request.args.get("username", "")

    conn = get_conn()
    conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()

    return redirect("/admin")


@app.route("/admin/reset_device")
def admin_reset_device():
    if not require_admin():
        return redirect("/admin/login")

    username = request.args.get("username", "")

    conn = get_conn()
    conn.execute("UPDATE users SET device_id = '' WHERE username = ?", (username,))
    conn.commit()
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
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    if user:
        now = datetime.datetime.now()
        old_expire_at = user["expire_at"]

        if old_expire_at and not is_expired(old_expire_at):
            base_time = datetime.datetime.strptime(old_expire_at, "%Y-%m-%d %H:%M:%S")
        else:
            base_time = now

        new_expire = base_time + datetime.timedelta(days=days)
        new_expire_str = new_expire.strftime("%Y-%m-%d %H:%M:%S")

        conn.execute(
            "UPDATE users SET expire_at = ? WHERE username = ?",
            (new_expire_str, username)
        )

        conn.execute(
            "INSERT INTO orders(username, card_code, days, price, old_expire_at, new_expire_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                username,
                "管理员手动续费",
                days,
                price,
                old_expire_at,
                new_expire_str,
                now_str()
            )
        )

        conn.commit()

    conn.close()


# ================== 管理员：卡密操作 ==================
@app.route("/admin/reset_card")
def admin_reset_card():
    if not require_admin():
        return redirect("/admin/login")

    card_code = request.args.get("card_code", "")

    conn = get_conn()
    conn.execute(
        "UPDATE cards SET used = 0, used_by = '', used_time = '' WHERE card_code = ?",
        (card_code,)
    )
    conn.commit()
    conn.close()

    return redirect("/admin")


@app.route("/admin/delete_card")
def admin_delete_card():
    if not require_admin():
        return redirect("/admin/login")

    card_code = request.args.get("card_code", "")

    conn = get_conn()
    conn.execute("DELETE FROM cards WHERE card_code = ?", (card_code,))
    conn.commit()
    conn.close()

    return redirect("/admin")


# ================== 管理员：导出 CSV ==================
@app.route("/admin/export_orders")
def admin_export_orders():
    if not require_admin():
        return redirect("/admin/login")

    conn = get_conn()
    orders = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "账号", "卡密/来源", "天数", "金额", "旧到期时间", "新到期时间", "充值时间"])

    for o in orders:
        writer.writerow([
            o["id"],
            o["username"],
            o["card_code"],
            o["days"],
            o["price"],
            o["old_expire_at"],
            o["new_expire_at"],
            o["created_at"]
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
    cards = conn.execute("SELECT * FROM cards ORDER BY id DESC").fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "卡密", "天数", "金额", "是否使用", "使用者", "使用时间", "创建时间"])

    for c in cards:
        writer.writerow([
            c["id"],
            c["card_code"],
            c["days"],
            c["price"],
            "是" if c["used"] else "否",
            c["used_by"],
            c["used_time"],
            c["created_at"]
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
