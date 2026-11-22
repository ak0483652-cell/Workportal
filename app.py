import io
import os
import random
import secrets
import sqlite3
from datetime import datetime, date
from functools import wraps

from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, abort, send_file)
from markupsafe import Markup
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data.db")

app = Flask(__name__)


def _load_secret():
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    path = os.path.join(BASE, ".secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


app.secret_key = _load_secret()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

DEFAULT_SETTINGS = {
    "cat1_name": "Work 1 - Captcha",
    "cat2_name": "Work 2 - Assignment",
    "cat3_name": "Work 3",
    "cat4_name": "Work 4",
    "captcha_rate": "0.50",
    "captcha_daily_limit": "100",
    "min_withdrawal": "100",
    "site_name": "WorkPortal",
}

# ----------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  upi_id TEXT DEFAULT '',
  balance REAL NOT NULL DEFAULT 0,
  is_admin INTEGER NOT NULL DEFAULT 0,
  is_blocked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  reward REAL NOT NULL,
  assigned_to INTEGER,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  answer TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS withdrawals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  amount REAL NOT NULL,
  upi_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  amount REAL NOT NULL,
  kind TEXT NOT NULL,
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    has_admin = db.execute("SELECT 1 FROM users WHERE is_admin=1").fetchone()
    if not has_admin:
        pw = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(9)
        db.execute(
            "INSERT INTO users(username,name,password_hash,is_admin,created_at) VALUES(?,?,?,1,?)",
            ("admin", "Admin", generate_password_hash(pw), now()),
        )
        print("=" * 60)
        print("ADMIN LOGIN CREATED  ->  username: admin   password: %s" % pw)
        print("(Change it by setting ADMIN_PASSWORD before first run.)")
        print("=" * 60)
    db.commit()
    db.close()


def setting(key):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")


def money(v):
    return "\u20b9{:,.2f}".format(float(v or 0))


app.jinja_env.filters["money"] = money


def add_txn(user_id, amount, kind, note=""):
    get_db().execute(
        "INSERT INTO transactions(user_id,amount,kind,note,created_at) VALUES(?,?,?,?,?)",
        (user_id, amount, kind, note, now()),
    )


def credit(user_id, amount, kind, note=""):
    db = get_db()
    db.execute("UPDATE users SET balance = round(balance + ?, 2) WHERE id=?", (amount, user_id))
    add_txn(user_id, amount, kind, note)


# ------------------------------------------------------------ auth / csrf


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    if "user" not in g:
        g.user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return g.user


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if u["is_blocked"]:
            session.clear()
            flash("Your account is blocked. Contact admin.", "error")
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*a, **kw):
        if not current_user()["is_admin"]:
            abort(403)
        return f(*a, **kw)
    return wrapper


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Invalid or missing CSRF token. Go back and refresh the page.")


@app.context_processor
def inject():
    names = {i: setting("cat%d_name" % i) for i in range(1, 5)}
    return dict(
        csrf=lambda: Markup('<input type="hidden" name="csrf" value="%s">' % csrf_token()),
        me=current_user(),
        cat_names=names,
        site_name=setting("site_name"),
    )


def parse_amount(text):
    try:
        v = round(float(text), 2)
    except (TypeError, ValueError):
        return None
    return v if v > 0 and v < 10_000_000 else None


# ------------------------------------------------------------ public pages


@app.route("/")
def index():
    return redirect(url_for("dashboard" if current_user() else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        name = request.form.get("name", "").strip()
        pw = request.form.get("password", "")
        upi = request.form.get("upi_id", "").strip()
        if len(username) < 4 or not username.replace("_", "").replace(".", "").isalnum():
            flash("Username must be 4+ characters (letters, numbers, _ or .).", "error")
        elif not name:
            flash("Please enter your name.", "error")
        elif len(pw) < 6:
            flash("Password must be at least 6 characters.", "error")
        else:
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO users(username,name,password_hash,upi_id,created_at) VALUES(?,?,?,?,?)",
                    (username, name, generate_password_hash(pw), upi, now()),
                )
                db.commit()
            except sqlite3.IntegrityError:
                flash("That username is taken.", "error")
            else:
                flash("Account created. Please log in.", "ok")
                return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        pw = request.form.get("password", "")
        u = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not u or not check_password_hash(u["password_hash"], pw):
            flash("Wrong username or password.", "error")
        elif u["is_blocked"]:
            flash("Your account is blocked. Contact admin.", "error")
        else:
            session.clear()
            session["uid"] = u["id"]
            return redirect(url_for("admin_home" if u["is_admin"] else "dashboard"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# -------------------------------------------------------------- user side


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    u = current_user()
    stats = {}
    for c in range(1, 5):
        stats[c] = db.execute(
            """SELECT COUNT(*) n FROM tasks WHERE category=? AND active=1
               AND (assigned_to IS NULL OR assigned_to=?)
               AND id NOT IN (SELECT task_id FROM submissions WHERE user_id=? AND status IN ('pending','approved'))""",
            (c, u["id"], u["id"]),
        ).fetchone()["n"]
    txns = db.execute(
        "SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 8", (u["id"],)
    ).fetchall()
    earned = db.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE user_id=? AND amount>0 AND kind IN ('captcha','task')",
        (u["id"],),
    ).fetchone()["s"]
    return render_template("dashboard.html", stats=stats, txns=txns, earned=earned)


# --- captcha generation (Work 1)

CAPTCHA_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _font(size):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def make_captcha_image(text):
    w, h = 200, 70
    img = Image.new("RGB", (w, h), (245, 245, 250))
    d = ImageDraw.Draw(img)
    for _ in range(6):
        d.line([(random.randint(0, w), random.randint(0, h)),
                (random.randint(0, w), random.randint(0, h))],
               fill=(random.randint(120, 200),) * 3, width=2)
    x = 12
    for ch in text:
        layer = Image.new("RGBA", (44, 56), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((6, 4), ch, font=_font(36),
                                   fill=(random.randint(10, 90), random.randint(10, 90), random.randint(60, 140), 255))
        layer = layer.rotate(random.randint(-25, 25), expand=True, resample=Image.BICUBIC)
        img.paste(layer, (x, random.randint(2, 12)), layer)
        x += 31
    for _ in range(120):
        d.point((random.randint(0, w - 1), random.randint(0, h - 1)), fill=(100, 100, 100))
    return img.filter(ImageFilter.SMOOTH_MORE)


@app.route("/captcha.png")
@login_required
def captcha_png():
    text = "".join(random.choice(CAPTCHA_CHARS) for _ in range(5))
    session["captcha"] = text
    buf = io.BytesIO()
    make_captcha_image(text).save(buf, "PNG")
    buf.seek(0)
    resp = send_file(buf, mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def captchas_today(user_id):
    return get_db().execute(
        "SELECT COUNT(*) n FROM transactions WHERE user_id=? AND kind='captcha' AND created_at LIKE ?",
        (user_id, date.today().isoformat() + "%"),
    ).fetchone()["n"]


@app.route("/work/1", methods=["GET", "POST"])
@login_required
def work_captcha():
    u = current_user()
    rate = float(setting("captcha_rate"))
    limit = int(float(setting("captcha_daily_limit")))
    done = captchas_today(u["id"])
    if request.method == "POST":
        expected = session.pop("captcha", None)
        answer = request.form.get("answer", "").strip().upper()
        if done >= limit:
            flash("Daily captcha limit reached. Come back tomorrow.", "error")
        elif not expected:
            flash("Captcha expired, please try the new one.", "error")
        elif answer == expected:
            credit(u["id"], rate, "captcha", "Captcha solved")
            get_db().commit()
            flash("Correct! %s added to your wallet." % money(rate), "ok")
        else:
            flash("Wrong answer. Try the new captcha.", "error")
        return redirect(url_for("work_captcha"))
    return render_template("work_captcha.html", rate=rate, limit=limit, done=done)


# --- task categories (Work 2 / 3 / 4)


@app.route("/work/<int:cat>")
@login_required
def work_tasks(cat):
    if cat == 1:
        return redirect(url_for("work_captcha"))
    if cat not in (2, 3, 4):
        abort(404)
    db = get_db()
    u = current_user()
    tasks = db.execute(
        """SELECT * FROM tasks WHERE category=? AND active=1
           AND (assigned_to IS NULL OR assigned_to=?)
           AND id NOT IN (SELECT task_id FROM submissions WHERE user_id=? AND status IN ('pending','approved'))
           ORDER BY id DESC""",
        (cat, u["id"], u["id"]),
    ).fetchall()
    subs = db.execute(
        """SELECT s.*, t.title, t.reward FROM submissions s JOIN tasks t ON t.id=s.task_id
           WHERE s.user_id=? AND t.category=? ORDER BY s.id DESC LIMIT 20""",
        (u["id"], cat),
    ).fetchall()
    return render_template("work_tasks.html", cat=cat, tasks=tasks, subs=subs)


@app.route("/task/<int:task_id>", methods=["GET", "POST"])
@login_required
def task_view(task_id):
    db = get_db()
    u = current_user()
    t = db.execute("SELECT * FROM tasks WHERE id=? AND active=1", (task_id,)).fetchone()
    if not t or (t["assigned_to"] and t["assigned_to"] != u["id"]):
        abort(404)
    existing = db.execute(
        "SELECT 1 FROM submissions WHERE task_id=? AND user_id=? AND status IN ('pending','approved')",
        (task_id, u["id"]),
    ).fetchone()
    if request.method == "POST":
        answer = request.form.get("answer", "").strip()
        if existing:
            flash("You already submitted this task.", "error")
        elif not answer:
            flash("Please write your answer / work.", "error")
        else:
            db.execute(
                "INSERT INTO submissions(task_id,user_id,answer,created_at) VALUES(?,?,?,?)",
                (task_id, u["id"], answer[:5000], now()),
            )
            db.commit()
            flash("Submitted! Admin will review it and credit your wallet.", "ok")
            return redirect(url_for("work_tasks", cat=t["category"]))
    return render_template("task.html", t=t, existing=existing)


# --- wallet / withdrawal


@app.route("/wallet", methods=["GET", "POST"])
@login_required
def wallet():
    db = get_db()
    u = current_user()
    min_wd = float(setting("min_withdrawal"))
    if request.method == "POST":
        amount = parse_amount(request.form.get("amount"))
        upi = request.form.get("upi_id", "").strip()
        if amount is None:
            flash("Enter a valid amount.", "error")
        elif amount < min_wd:
            flash("Minimum withdrawal is %s." % money(min_wd), "error")
        elif "@" not in upi or len(upi) < 5:
            flash("Enter a valid UPI ID (like name@bank).", "error")
        else:
            cur = db.execute(
                "UPDATE users SET balance = round(balance - ?, 2), upi_id=? WHERE id=? AND balance >= ?",
                (amount, upi, u["id"], amount),
            )
            if cur.rowcount != 1:
                flash("Not enough balance.", "error")
            else:
                db.execute(
                    "INSERT INTO withdrawals(user_id,amount,upi_id,created_at) VALUES(?,?,?,?)",
                    (u["id"], amount, upi, now()),
                )
                add_txn(u["id"], -amount, "withdraw", "Withdrawal requested")
                db.commit()
                flash("Withdrawal request sent. Admin will pay to your UPI.", "ok")
        return redirect(url_for("wallet"))
    wds = db.execute("SELECT * FROM withdrawals WHERE user_id=? ORDER BY id DESC LIMIT 20", (u["id"],)).fetchall()
    txns = db.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 30", (u["id"],)).fetchall()
    return render_template("wallet.html", wds=wds, txns=txns, min_wd=min_wd)


# ------------------------------------------------------------ admin panel


@app.route("/admin")
@admin_required
def admin_home():
    db = get_db()
    q = lambda sql: db.execute(sql).fetchone()[0]
    stats = {
        "users": q("SELECT COUNT(*) FROM users WHERE is_admin=0"),
        "balance": q("SELECT COALESCE(SUM(balance),0) FROM users WHERE is_admin=0"),
        "pending_subs": q("SELECT COUNT(*) FROM submissions WHERE status='pending'"),
        "pending_wd": q("SELECT COUNT(*) FROM withdrawals WHERE status='pending'"),
        "pending_wd_amt": q("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='pending'"),
        "paid": q("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='paid'"),
    }
    return render_template("admin_home.html", stats=stats)


@app.route("/admin/users")
@admin_required
def admin_users():
    search = request.args.get("q", "").strip().lower()
    rows = get_db().execute(
        "SELECT * FROM users WHERE is_admin=0 AND (username LIKE ? OR lower(name) LIKE ?) ORDER BY id DESC",
        ("%" + search + "%", "%" + search + "%"),
    ).fetchall()
    return render_template("admin_users.html", users=rows, search=search)


@app.route("/admin/user/<int:uid>", methods=["GET", "POST"])
@admin_required
def admin_user(uid):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=? AND is_admin=0", (uid,)).fetchone()
    if not u:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "block":
            db.execute("UPDATE users SET is_blocked = 1 - is_blocked WHERE id=?", (uid,))
            flash("User status changed.", "ok")
        elif action == "adjust":
            amt = parse_amount(request.form.get("amount"))
            sign = 1 if request.form.get("sign") == "add" else -1
            note = request.form.get("note", "").strip() or "Admin adjustment"
            if amt is None:
                flash("Enter a valid amount.", "error")
            elif sign < 0 and amt > u["balance"]:
                flash("User balance is lower than that.", "error")
            else:
                credit(uid, sign * amt, "admin", note)
                flash("Balance updated.", "ok")
        elif action == "password":
            pw = request.form.get("password", "")
            if len(pw) < 6:
                flash("Password must be at least 6 characters.", "error")
            else:
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), uid))
                flash("Password reset.", "ok")
        db.commit()
        return redirect(url_for("admin_user", uid=uid))
    txns = db.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 30", (uid,)).fetchall()
    subs = db.execute(
        "SELECT s.*, t.title FROM submissions s JOIN tasks t ON t.id=s.task_id WHERE s.user_id=? ORDER BY s.id DESC LIMIT 20",
        (uid,),
    ).fetchall()
    return render_template("admin_user.html", u=u, txns=txns, subs=subs)


@app.route("/admin/tasks", methods=["GET", "POST"])
@admin_required
def admin_tasks():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            title = request.form.get("title", "").strip()
            desc = request.form.get("description", "").strip()
            reward = parse_amount(request.form.get("reward"))
            cat = request.form.get("category", type=int)
            assign = request.form.get("assigned_to", "").strip().lower()
            copies = max(1, min(request.form.get("copies", 1, type=int) or 1, 200))
            assigned_id = None
            if cat not in (2, 3, 4):
                flash("Pick Work 2, 3 or 4 (Work 1 is the auto captcha).", "error")
            elif not title or not desc or reward is None:
                flash("Title, description and a valid reward are required.", "error")
            else:
                ok = True
                if assign:
                    row = db.execute("SELECT id FROM users WHERE username=? AND is_admin=0", (assign,)).fetchone()
                    if not row:
                        flash("No user with username '%s'." % assign, "error")
                        ok = False
                    else:
                        assigned_id = row["id"]
                if ok:
                    for _ in range(copies if not assigned_id else 1):
                        db.execute(
                            "INSERT INTO tasks(category,title,description,reward,assigned_to,created_at) VALUES(?,?,?,?,?,?)",
                            (cat, title, desc, reward, assigned_id, now()),
                        )
                    flash("Task created%s." % (" for " + assign if assigned_id else " for all users"), "ok")
        elif action == "toggle":
            db.execute("UPDATE tasks SET active = 1 - active WHERE id=?", (request.form.get("id", type=int),))
        db.commit()
        return redirect(url_for("admin_tasks"))
    tasks = db.execute(
        """SELECT t.*, u.username assignee,
           (SELECT COUNT(*) FROM submissions s WHERE s.task_id=t.id) subs
           FROM tasks t LEFT JOIN users u ON u.id=t.assigned_to ORDER BY t.id DESC LIMIT 200"""
    ).fetchall()
    return render_template("admin_tasks.html", tasks=tasks)


@app.route("/admin/submissions", methods=["GET", "POST"])
@admin_required
def admin_submissions():
    db = get_db()
    if request.method == "POST":
        sid = request.form.get("id", type=int)
        decision = request.form.get("decision")
        sub = db.execute(
            "SELECT s.*, t.reward, t.title FROM submissions s JOIN tasks t ON t.id=s.task_id WHERE s.id=?",
            (sid,),
        ).fetchone()
        if sub and decision in ("approved", "rejected"):
            cur = db.execute("UPDATE submissions SET status=? WHERE id=? AND status='pending'", (decision, sid))
            if cur.rowcount == 1 and decision == "approved":
                credit(sub["user_id"], sub["reward"], "task", "Task approved: " + sub["title"])
                flash("Approved and credited.", "ok")
            elif cur.rowcount == 1:
                flash("Rejected.", "ok")
            db.commit()
        return redirect(url_for("admin_submissions"))
    rows = db.execute(
        """SELECT s.*, t.title, t.reward, t.category, u.username FROM submissions s
           JOIN tasks t ON t.id=s.task_id JOIN users u ON u.id=s.user_id
           WHERE s.status='pending' ORDER BY s.id""",
    ).fetchall()
    return render_template("admin_subs.html", subs=rows)


@app.route("/admin/withdrawals", methods=["GET", "POST"])
@admin_required
def admin_withdrawals():
    db = get_db()
    if request.method == "POST":
        wid = request.form.get("id", type=int)
        decision = request.form.get("decision")
        note = request.form.get("note", "").strip()
        wd = db.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)).fetchone()
        if wd and decision in ("paid", "rejected"):
            cur = db.execute(
                "UPDATE withdrawals SET status=?, note=? WHERE id=? AND status='pending'", (decision, note, wid)
            )
            if cur.rowcount == 1:
                if decision == "rejected":
                    credit(wd["user_id"], wd["amount"], "refund", "Withdrawal rejected - refunded")
                    flash("Rejected and amount refunded to user.", "ok")
                else:
                    flash("Marked as paid.", "ok")
            db.commit()
        return redirect(url_for("admin_withdrawals", show=request.args.get("show", "pending")))
    show = request.args.get("show", "pending")
    where = "WHERE w.status='pending'" if show == "pending" else ""
    rows = db.execute(
        "SELECT w.*, u.username, u.name FROM withdrawals w JOIN users u ON u.id=w.user_id %s ORDER BY w.id DESC LIMIT 200"
        % where
    ).fetchall()
    return render_template("admin_wd.html", wds=rows, show=show)


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    db = get_db()
    if request.method == "POST":
        for k in DEFAULT_SETTINGS:
            v = request.form.get(k, "").strip()
            if not v:
                continue
            if k in ("captcha_rate", "captcha_daily_limit", "min_withdrawal"):
                try:
                    if float(v) < 0:
                        raise ValueError
                except ValueError:
                    flash("Invalid number for %s." % k, "error")
                    return redirect(url_for("admin_settings"))
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))
        db.commit()
        flash("Settings saved.", "ok")
        return redirect(url_for("admin_settings"))
    vals = {k: setting(k) for k in DEFAULT_SETTINGS}
    return render_template("admin_settings.html", vals=vals)


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
