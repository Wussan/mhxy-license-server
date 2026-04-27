from flask import Flask, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import datetime
import random
import string

app = Flask(__name__)

DB_PATH = "license.db"


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_code TEXT UNIQUE NOT NULL,
        days INTEGER DEFAULT 30,
        used INTEGER DEFAULT 0,
        used_by TEXT,
        used_time TEXT,
        created_at TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()


def is_expired(expire_at):
    if not expire_at:
        return True

    expire_time = datetime.datetime.strptime(expire_at, "%Y-%m-%d %H:%M:%S")
    return datetime.datetime.now() > expire_time


@app.route("/api/register", methods=["POST"])
def register():
    data = request.json

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
            "INSERT INTO users(username, password_hash, device_id, expire_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                username,
                generate_password_hash(password),
                device_id,
                "",
                now_str()
            )
        )
        conn.commit()
        return jsonify({"ok": True, "msg": "注册成功，请充值后登录"})

    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "msg": "账号已存在"})

    finally:
        conn.close()


@app.route("/api/login", methods=["POST"])
def login():
    data = request.json

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


@app.route("/api/recharge", methods=["POST"])
def recharge():
    data = request.json

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

    if user["expire_at"] and not is_expired(user["expire_at"]):
        base_time = datetime.datetime.strptime(
            user["expire_at"],
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

    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "msg": f"充值成功，到期时间：{new_expire_str}",
        "expire_at": new_expire_str
    })


@app.route("/api/check", methods=["POST"])
def check():
    data = request.json

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


@app.route("/admin/create_card")
def create_card():
    conn = get_conn()

    card = "".join(
        random.choices(string.ascii_uppercase + string.digits, k=16)
    )

    conn.execute(
        "INSERT INTO cards(card_code, days, used, created_at) VALUES (?, ?, ?, ?)",
        (card, 30, 0, now_str())
    )

    conn.commit()
    conn.close()

    return f"新卡密：{card}"


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)