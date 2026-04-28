from flask import Flask, request, jsonify, render_template_string, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import datetime
import random
import string
import os

app = Flask(__name__)

# ================== 配置 ==================
DATABASE_URL = os.environ.get("DATABASE_URL")
app.secret_key = "mhxy_secret_key"

ADMIN_USER = "admin"
ADMIN_PWD = "admin123"

# ================== 工具 ==================
def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_conn():
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
    except:
        return True

def make_card():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))

# ================== 初始化数据库 ==================
def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT,
        device TEXT,
        expire_at TEXT,
        status TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cards (
        id SERIAL PRIMARY KEY,
        code TEXT UNIQUE,
        days INT,
        used INT,
        created_at TEXT
    )
    """)

    conn.commit()
    cur.close()
    conn.close()

# ================== API ==================

@app.route("/api/register", methods=["POST"])
def register():
    data = request.json
    username = data["username"]
    password = data["password"]

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            "INSERT INTO users(username,password,status,created_at) VALUES (%s,%s,%s,%s)",
            (username, generate_password_hash(password), "active", now_str())
        )
        conn.commit()
        return jsonify({"ok": True})
    except:
        conn.rollback()
        return jsonify({"ok": False, "msg": "账号已存在"})
    finally:
        cur.close()
        conn.close()

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    username = data["username"]
    password = data["password"]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE username=%s", (username,))
    user = cur.fetchone()

    if not user:
        return jsonify({"ok": False, "msg": "不存在"})

    if not check_password_hash(user["password"], password):
        return jsonify({"ok": False, "msg": "密码错"})

    if is_expired(user["expire_at"]):
        return jsonify({"ok": False, "msg": "过期"})

    return jsonify({
        "ok": True,
        "expire": user["expire_at"]
    })

@app.route("/api/recharge", methods=["POST"])
def recharge():
    data = request.json
    username = data["username"]
    code = data["card"]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM cards WHERE code=%s", (code,))
    card = cur.fetchone()

    if not card or card["used"]:
        return jsonify({"ok": False, "msg": "卡密无效"})

    new_time = datetime.datetime.now() + datetime.timedelta(days=card["days"])
    expire_str = new_time.strftime("%Y-%m-%d %H:%M:%S")

    cur.execute("UPDATE users SET expire_at=%s WHERE username=%s", (expire_str, username))
    cur.execute("UPDATE cards SET used=1 WHERE code=%s", (code,))

    conn.commit()

    return jsonify({"ok": True, "expire": expire_str})

# ================== 后台 ==================

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "GET":
        return "<form method='post'>账号<input name='u'><br>密码<input name='p'><button>登录</button></form>"

    if request.form["u"] == ADMIN_USER and request.form["p"] == ADMIN_PWD:
        session["admin"] = True
        return redirect("/admin")

    return "错误"

@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect("/admin/login")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users")
    users = cur.fetchall()

    cur.execute("SELECT * FROM cards")
    cards = cur.fetchall()

    html = "<h2>用户</h2>"
    for u in users:
        html += f"{u['username']} - {u['expire_at']}<br>"

    html += "<h2>卡密</h2>"
    for c in cards:
        html += f"{c['code']} - {'已用' if c['used'] else '未用'}<br>"

    html += "<br><a href='/admin/create'>生成卡密</a>"

    return html

@app.route("/admin/create")
def create_card():
    conn = get_conn()
    cur = conn.cursor()

    code = make_card()

    cur.execute(
        "INSERT INTO cards(code,days,used,created_at) VALUES (%s,%s,%s,%s)",
        (code,30,0,now_str())
    )

    conn.commit()

    return f"卡密：{code}"

# ================== 启动 ==================

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
