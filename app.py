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

app = Flask(__name__, static_folder=None)


# ---- all HTML pages and CSS live inside this file (no templates/static folders needed) ----
TEMPLATES = {
    'admin_home.html': '''{% extends "base.html" %}
{% block title %}Admin{% endblock %}
{% block body %}
<h1>Admin panel</h1>
<div class="grid">
  <div class="card stat"><div class="n">{{ stats.users }}</div><div class="t">Users</div></div>
  <div class="card stat"><div class="n">{{ stats.balance|money }}</div><div class="t">Total user balances (you owe)</div></div>
  <a class="card stat work" href="{{ url_for('admin_submissions') }}"><div class="n">{{ stats.pending_subs }}</div><div class="t">Submissions to review</div></a>
  <a class="card stat work" href="{{ url_for('admin_withdrawals') }}"><div class="n">{{ stats.pending_wd }} ({{ stats.pending_wd_amt|money }})</div><div class="t">Pending withdrawals</div></a>
  <div class="card stat"><div class="n">{{ stats.paid|money }}</div><div class="t">Total paid out</div></div>
</div>
<h2>Quick actions</h2>
<div class="row">
  <a class="btn" href="{{ url_for('admin_tasks') }}">Create / assign work</a>
  <a class="btn sec" href="{{ url_for('admin_users') }}">Manage users</a>
  <a class="btn sec" href="{{ url_for('admin_settings') }}">Rates &amp; names</a>
</div>
{% endblock %}
''',
    'admin_settings.html': '''{% extends "base.html" %}
{% block title %}Settings{% endblock %}
{% block body %}
<h1>Settings</h1>
<div class="card">
<form method="post">{{ csrf() }}
  <label>Site name</label><input name="site_name" value="{{ vals.site_name }}">
  <div class="row">
    <div><label>Work 1 name</label><input name="cat1_name" value="{{ vals.cat1_name }}"></div>
    <div><label>Work 2 name</label><input name="cat2_name" value="{{ vals.cat2_name }}"></div>
    <div><label>Work 3 name</label><input name="cat3_name" value="{{ vals.cat3_name }}"></div>
    <div><label>Work 4 name</label><input name="cat4_name" value="{{ vals.cat4_name }}"></div>
  </div>
  <div class="row">
    <div><label>Pay per correct captcha (₹)</label><input name="captcha_rate" type="number" step="0.01" min="0" value="{{ vals.captcha_rate }}"></div>
    <div><label>Max captchas per user per day</label><input name="captcha_daily_limit" type="number" min="0" value="{{ vals.captcha_daily_limit }}"></div>
    <div><label>Minimum withdrawal (₹)</label><input name="min_withdrawal" type="number" step="1" min="1" value="{{ vals.min_withdrawal }}"></div>
  </div>
  <p><button>Save settings</button></p>
</form>
</div>
{% endblock %}
''',
    'admin_subs.html': '''{% extends "base.html" %}
{% block title %}Review submissions{% endblock %}
{% block body %}
<h1>Submissions to review</h1>
{% for s in subs %}
<div class="card">
  <div class="row" style="align-items:center">
    <div><b>{{ s['title'] }}</b><br><span class="mut">{{ cat_names[s['category']] }} &middot; by {{ s['username'] }} &middot; {{ s['created_at'] }}</span></div>
    <div><b>Reward:</b> {{ s['reward']|money }}</div>
  </div>
  <p><pre>{{ s['answer'] }}</pre></p>
  <div class="row" style="max-width:340px">
    <form method="post">{{ csrf() }}<input type="hidden" name="id" value="{{ s['id'] }}"><input type="hidden" name="decision" value="approved"><button class="btn ok">Approve &amp; pay</button></form>
    <form method="post">{{ csrf() }}<input type="hidden" name="id" value="{{ s['id'] }}"><input type="hidden" name="decision" value="rejected"><button class="btn bad">Reject</button></form>
  </div>
</div>
{% else %}
<div class="card"><p class="mut">Nothing pending. All caught up.</p></div>
{% endfor %}
{% endblock %}
''',
    'admin_tasks.html': '''{% extends "base.html" %}
{% block title %}Tasks{% endblock %}
{% block body %}
<h1>Create &amp; assign work</h1>
<div class="card">
  <form method="post">{{ csrf() }}<input type="hidden" name="action" value="create">
    <div class="row">
      <div><label>Dashboard</label>
        <select name="category">{% for i in (2,3,4) %}<option value="{{ i }}">{{ cat_names[i] }}</option>{% endfor %}</select></div>
      <div><label>Reward (₹)</label><input name="reward" type="number" step="0.01" min="0.01" required></div>
      <div><label>Assign to username (blank = everyone)</label><input name="assigned_to" placeholder="e.g. rahul123"></div>
      <div><label>Copies (for everyone)</label><input name="copies" type="number" min="1" max="200" value="1"></div>
    </div>
    <label>Title</label><input name="title" required>
    <label>Instructions</label><textarea name="description" required></textarea>
    <p><button>Create task</button></p>
  </form>
  <p class="mut">Work 1 is the automatic captcha dashboard; its rate is set in Settings. "Copies" lets many users each get their own copy of an everyone-task.</p>
</div>

<h2>All tasks</h2>
<div class="card"><table>
<tr><th>#</th><th>Dashboard</th><th>Title</th><th>Reward</th><th>For</th><th>Subs</th><th></th></tr>
{% for t in tasks %}<tr>
<td>{{ t['id'] }}</td><td>{{ cat_names[t['category']] }}</td><td>{{ t['title'] }}</td><td>{{ t['reward']|money }}</td>
<td>{{ t['assignee'] or 'Everyone' }}</td><td>{{ t['subs'] }}</td>
<td><form method="post">{{ csrf() }}<input type="hidden" name="action" value="toggle"><input type="hidden" name="id" value="{{ t['id'] }}">
<button class="btn sm {{ 'sec' if t['active'] else 'ok' }}">{{ 'Disable' if t['active'] else 'Enable' }}</button></form></td></tr>
{% else %}<tr><td colspan="7" class="mut">No tasks yet.</td></tr>{% endfor %}
</table></div>
{% endblock %}
''',
    'admin_user.html': '''{% extends "base.html" %}
{% block title %}{{ u['username'] }}{% endblock %}
{% block body %}
<h1>{{ u['name'] }} <span class="mut">({{ u['username'] }})</span></h1>
<div class="grid">
  <div class="card stat"><div class="n">{{ u['balance']|money }}</div><div class="t">Balance &middot; UPI: {{ u['upi_id'] or 'not set' }}</div></div>
  <div class="card">
    <b>Adjust balance</b>
    <form method="post">{{ csrf() }}<input type="hidden" name="action" value="adjust">
      <div class="row"><select name="sign"><option value="add">Add</option><option value="sub">Deduct</option></select>
      <input name="amount" type="number" step="0.01" min="0.01" placeholder="Amount" required></div>
      <input name="note" placeholder="Note (optional)" style="margin-top:8px">
      <p><button>Apply</button></p>
    </form>
  </div>
  <div class="card">
    <b>Account</b>
    <form method="post" style="margin-bottom:12px">{{ csrf() }}<input type="hidden" name="action" value="block">
      <p><button class="btn {{ 'ok' if u['is_blocked'] else 'bad' }}">{{ 'Unblock user' if u['is_blocked'] else 'Block user' }}</button></p></form>
    <form method="post">{{ csrf() }}<input type="hidden" name="action" value="password">
      <input name="password" type="text" placeholder="New password (min 6)" required>
      <p><button class="btn sec">Reset password</button></p></form>
  </div>
</div>

<h2>Recent transactions</h2>
<div class="card"><table><tr><th>When</th><th>Type</th><th>Note</th><th>Amount</th></tr>
{% for t in txns %}<tr><td class="mut">{{ t['created_at'] }}</td><td>{{ t['kind'] }}</td><td>{{ t['note'] }}</td>
<td class="{{ 'pos' if t['amount']>0 else 'neg' }}">{{ t['amount']|money }}</td></tr>
{% else %}<tr><td colspan="4" class="mut">None.</td></tr>{% endfor %}</table></div>

<h2>Recent submissions</h2>
<div class="card"><table><tr><th>Task</th><th>Status</th><th>When</th></tr>
{% for s in subs %}<tr><td>{{ s['title'] }}</td><td><span class="pill {{ s['status'] }}">{{ s['status'] }}</span></td><td class="mut">{{ s['created_at'] }}</td></tr>
{% else %}<tr><td colspan="3" class="mut">None.</td></tr>{% endfor %}</table></div>
<p><a href="{{ url_for('admin_users') }}">&larr; All users</a></p>
{% endblock %}
''',
    'admin_users.html': '''{% extends "base.html" %}
{% block title %}Users{% endblock %}
{% block body %}
<h1>Users</h1>
<form method="get" class="row" style="margin-bottom:14px"><input name="q" value="{{ search }}" placeholder="Search username or name"><button>Search</button></form>
<div class="card">
<table><tr><th>Username</th><th>Name</th><th>UPI</th><th>Balance</th><th>Status</th><th></th></tr>
{% for u in users %}<tr>
<td>{{ u['username'] }}</td><td>{{ u['name'] }}</td><td>{{ u['upi_id'] }}</td><td>{{ u['balance']|money }}</td>
<td>{% if u['is_blocked'] %}<span class="pill rejected">blocked</span>{% else %}<span class="pill approved">active</span>{% endif %}</td>
<td><a class="btn sm sec" href="{{ url_for('admin_user', uid=u['id']) }}">Manage</a></td></tr>
{% else %}<tr><td colspan="6" class="mut">No users found.</td></tr>{% endfor %}
</table>
</div>
{% endblock %}
''',
    'admin_wd.html': '''{% extends "base.html" %}
{% block title %}Withdrawals{% endblock %}
{% block body %}
<h1>Withdrawals</h1>
<p><a class="btn sm {{ '' if show=='pending' else 'sec' }}" href="?show=pending">Pending</a>
   <a class="btn sm {{ '' if show=='all' else 'sec' }}" href="?show=all">All</a></p>
<p class="mut">Pay the UPI ID shown from your own UPI app first, then click "Mark paid". "Reject" refunds the amount to the user's wallet.</p>
{% for w in wds %}
<div class="card">
  <div class="row" style="align-items:center">
    <div><b>{{ w['amount']|money }}</b> to <b>{{ w['upi_id'] }}</b><br>
      <span class="mut">{{ w['name'] }} ({{ w['username'] }}) &middot; {{ w['created_at'] }}</span></div>
    <div><span class="pill {{ w['status'] }}">{{ w['status'] }}</span> {{ w['note'] }}</div>
  </div>
  {% if w['status'] == 'pending' %}
  <form method="post" action="{{ url_for('admin_withdrawals', show=show) }}" style="margin-top:10px">
    {{ csrf() }}<input type="hidden" name="id" value="{{ w['id'] }}">
    <div class="row"><input name="note" placeholder="Note / UTR number (optional)">
    <button name="decision" value="paid" class="btn ok">Mark paid</button>
    <button name="decision" value="rejected" class="btn bad">Reject &amp; refund</button></div>
  </form>
  {% endif %}
</div>
{% else %}
<div class="card"><p class="mut">No withdrawals to show.</p></div>
{% endfor %}
{% endblock %}
''',
    'base.html': '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}{{ site_name }}{% endblock %}</title>
<link rel="stylesheet" href="{{ url_for('css') }}">
</head>
<body>
<div class="nav">
  <a class="brand" href="/">{{ site_name }}</a>
  {% if me and not me['is_admin'] %}
    <a class="l" href="{{ url_for('dashboard') }}">Dashboard</a>
    {% for i in range(1,5) %}<a class="l" href="{{ url_for('work_tasks', cat=i) }}">{{ cat_names[i] }}</a>{% endfor %}
    <a class="l" href="{{ url_for('wallet') }}">Wallet</a>
  {% elif me and me['is_admin'] %}
    <a class="l" href="{{ url_for('admin_home') }}">Admin Home</a>
    <a class="l" href="{{ url_for('admin_users') }}">Users</a>
    <a class="l" href="{{ url_for('admin_tasks') }}">Tasks</a>
    <a class="l" href="{{ url_for('admin_submissions') }}">Submissions</a>
    <a class="l" href="{{ url_for('admin_withdrawals') }}">Withdrawals</a>
    <a class="l" href="{{ url_for('admin_settings') }}">Settings</a>
  {% endif %}
  <span class="sp"></span>
  {% if me %}
    {% if not me['is_admin'] %}<span class="mut">{{ me['balance']|money }}</span>{% endif %}
    <form method="post" action="{{ url_for('logout') }}">{{ csrf() }}<button class="btn sec sm">Logout ({{ me['username'] }})</button></form>
  {% endif %}
</div>
<div class="wrap">
  {% with msgs = get_flashed_messages(with_categories=true) %}
    {% for cat, m in msgs %}<div class="flash {{ cat }}">{{ m }}</div>{% endfor %}
  {% endwith %}
  {% block body %}{% endblock %}
</div>
</body>
</html>
''',
    'dashboard.html': '''{% extends "base.html" %}
{% block title %}Dashboard{% endblock %}
{% block body %}
<h1>Hi, {{ me['name'] }}</h1>
<div class="grid">
  <div class="card stat"><div class="n">{{ me['balance']|money }}</div><div class="t">Wallet balance</div></div>
  <div class="card stat"><div class="n">{{ earned|money }}</div><div class="t">Total earned</div></div>
  <a class="card stat work" href="{{ url_for('wallet') }}"><div class="n">Withdraw &rarr;</div><div class="t">Send money to your UPI</div></a>
</div>

<h2>Your work dashboards</h2>
<div class="grid">
  <a class="card work" href="{{ url_for('work_captcha') }}">
    <div class="tt">{{ cat_names[1] }}</div><div class="mut">Solve captchas, instant credit</div><span class="bd">Always available</span>
  </a>
  {% for i in (2,3,4) %}
  <a class="card work" href="{{ url_for('work_tasks', cat=i) }}">
    <div class="tt">{{ cat_names[i] }}</div><div class="mut">Tasks assigned by admin</div><span class="bd">{{ stats[i] }} open</span>
  </a>
  {% endfor %}
</div>

<h2>Recent activity</h2>
<div class="card">
{% if txns %}
<table><tr><th>When</th><th>Type</th><th>Note</th><th>Amount</th></tr>
{% for t in txns %}<tr><td class="mut">{{ t['created_at'] }}</td><td>{{ t['kind'] }}</td><td>{{ t['note'] }}</td>
<td class="{{ 'pos' if t['amount']>0 else 'neg' }}">{{ t['amount']|money }}</td></tr>{% endfor %}
</table>
{% else %}<p class="mut">No activity yet. Start with {{ cat_names[1] }}.</p>{% endif %}
</div>
{% endblock %}
''',
    'login.html': '''{% extends "base.html" %}
{% block title %}Login{% endblock %}
{% block body %}
<div class="auth card">
  <h1>Login</h1>
  <form method="post">
    {{ csrf() }}
    <label>Username</label><input name="username" required autofocus autocomplete="username">
    <label>Password</label><input name="password" type="password" required autocomplete="current-password">
    <p><button>Login</button></p>
  </form>
  <p class="mut">New here? <a href="{{ url_for('register') }}">Create an account</a> (free, no fee).</p>
</div>
{% endblock %}
''',
    'register.html': '''{% extends "base.html" %}
{% block title %}Sign up{% endblock %}
{% block body %}
<div class="auth card">
  <h1>Create account</h1>
  <form method="post">
    {{ csrf() }}
    <label>Full name</label><input name="name" required>
    <label>Username</label><input name="username" required autocomplete="username">
    <label>Password (min 6 characters)</label><input name="password" type="password" required autocomplete="new-password">
    <label>UPI ID (optional, can add later)</label><input name="upi_id" placeholder="name@bank">
    <p><button>Sign up</button></p>
  </form>
  <p class="mut">No deposit or joining fee is ever required. <a href="{{ url_for('login') }}">Back to login</a></p>
</div>
{% endblock %}
''',
    'task.html': '''{% extends "base.html" %}
{% block title %}{{ t['title'] }}{% endblock %}
{% block body %}
<h1>{{ t['title'] }}</h1>
<div class="card">
  <p><b>Reward:</b> {{ t['reward']|money }}</p>
  <pre>{{ t['description'] }}</pre>
</div>
<div class="card">
  {% if existing %}
    <p class="mut">You already submitted this task.</p>
  {% else %}
  <form method="post">
    {{ csrf() }}
    <label>Your answer / completed work</label>
    <textarea name="answer" required maxlength="5000"></textarea>
    <p><button>Submit for review</button></p>
  </form>
  {% endif %}
  <p><a href="{{ url_for('work_tasks', cat=t['category']) }}">&larr; Back</a></p>
</div>
{% endblock %}
''',
    'wallet.html': '''{% extends "base.html" %}
{% block title %}Wallet{% endblock %}
{% block body %}
<h1>Wallet</h1>
<div class="grid">
  <div class="card stat"><div class="n">{{ me['balance']|money }}</div><div class="t">Available balance</div></div>
  <div class="card">
    <b>Withdraw to UPI</b>
    <form method="post">
      {{ csrf() }}
      <label>Amount (min {{ min_wd|money }})</label><input name="amount" type="number" step="0.01" min="1" required>
      <label>UPI ID</label><input name="upi_id" value="{{ me['upi_id'] }}" placeholder="name@bank" required>
      <p><button>Request withdrawal</button></p>
    </form>
  </div>
</div>

<h2>Withdrawal requests</h2>
<div class="card">
{% if wds %}
<table><tr><th>When</th><th>Amount</th><th>UPI</th><th>Status</th><th>Note</th></tr>
{% for w in wds %}<tr><td class="mut">{{ w['created_at'] }}</td><td>{{ w['amount']|money }}</td><td>{{ w['upi_id'] }}</td>
<td><span class="pill {{ w['status'] }}">{{ w['status'] }}</span></td><td>{{ w['note'] }}</td></tr>{% endfor %}
</table>
{% else %}<p class="mut">No withdrawals yet.</p>{% endif %}
</div>

<h2>Transaction history</h2>
<div class="card">
{% if txns %}
<table><tr><th>When</th><th>Type</th><th>Note</th><th>Amount</th></tr>
{% for t in txns %}<tr><td class="mut">{{ t['created_at'] }}</td><td>{{ t['kind'] }}</td><td>{{ t['note'] }}</td>
<td class="{{ 'pos' if t['amount']>0 else 'neg' }}">{{ t['amount']|money }}</td></tr>{% endfor %}
</table>
{% else %}<p class="mut">No transactions yet.</p>{% endif %}
</div>
{% endblock %}
''',
    'work_captcha.html': '''{% extends "base.html" %}
{% block title %}{{ cat_names[1] }}{% endblock %}
{% block body %}
<h1>{{ cat_names[1] }}</h1>
<div class="card">
  <p>Type the characters you see. Each correct answer pays <b>{{ rate|money }}</b>.
     Today: <b>{{ done }}</b> / {{ limit }} solved.</p>
  {% if done >= limit %}
    <p class="mut">Daily limit reached. Come back tomorrow.</p>
  {% else %}
  <form method="post" autocomplete="off">
    {{ csrf() }}
    <div class="cap">
      <img src="{{ url_for('captcha_png') }}?t={{ range(1, 10**9)|random }}" alt="captcha" width="200" height="70">
      <a href="{{ url_for('work_captcha') }}">Refresh</a>
    </div>
    <div class="row"><input name="answer" maxlength="8" required autofocus placeholder="Enter captcha"><button>Submit</button></div>
  </form>
  {% endif %}
</div>
{% endblock %}
''',
    'work_tasks.html': '''{% extends "base.html" %}
{% block title %}{{ cat_names[cat] }}{% endblock %}
{% block body %}
<h1>{{ cat_names[cat] }}</h1>
<h2>Open tasks</h2>
<div class="card">
{% if tasks %}
<table><tr><th>Task</th><th>Reward</th><th></th></tr>
{% for t in tasks %}<tr><td><b>{{ t['title'] }}</b>{% if t['assigned_to'] %} <span class="pill approved">for you</span>{% endif %}</td>
<td>{{ t['reward']|money }}</td><td><a class="btn sm" href="{{ url_for('task_view', task_id=t['id']) }}">Open</a></td></tr>{% endfor %}
</table>
{% else %}<p class="mut">No open tasks right now. Admin will assign new work soon.</p>{% endif %}
</div>

<h2>My submissions</h2>
<div class="card">
{% if subs %}
<table><tr><th>Task</th><th>Reward</th><th>Status</th><th>When</th></tr>
{% for s in subs %}<tr><td>{{ s['title'] }}</td><td>{{ s['reward']|money }}</td>
<td><span class="pill {{ s['status'] }}">{{ s['status'] }}</span></td><td class="mut">{{ s['created_at'] }}</td></tr>{% endfor %}
</table>
{% else %}<p class="mut">Nothing submitted yet.</p>{% endif %}
</div>
{% endblock %}
''',
}

CSS = ''':root{--bg:#0f172a;--card:#1e293b;--line:#334155;--text:#e2e8f0;--mut:#94a3b8;--acc:#6366f1;--ok:#22c55e;--err:#ef4444;--warn:#f59e0b}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.5}
a{color:#a5b4fc;text-decoration:none}a:hover{text-decoration:underline}
.nav{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:12px 18px;background:#0b1220;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
.nav .brand{font-weight:700;font-size:18px;color:#fff;margin-right:14px}
.nav a.l{padding:6px 10px;border-radius:8px;color:var(--text)}.nav a.l:hover{background:var(--card);text-decoration:none}
.nav .sp{flex:1}.nav form{margin:0}
.wrap{max-width:1000px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:24px;margin:6px 0 16px}h2{font-size:18px;margin:26px 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
.stat .n{font-size:26px;font-weight:700}.stat .t{color:var(--mut);font-size:13px}
.work{display:block;color:var(--text)}.work:hover{border-color:var(--acc);text-decoration:none}
.work .tt{font-weight:600;font-size:17px}.work .bd{margin-top:8px;display:inline-block;background:#312e81;color:#c7d2fe;padding:2px 10px;border-radius:999px;font-size:12px}
label{display:block;font-size:13px;color:var(--mut);margin:12px 0 4px}
input,select,textarea{width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:#0f172a;color:var(--text);font:inherit}
textarea{min-height:120px}
button,.btn{background:var(--acc);color:#fff;border:0;border-radius:10px;padding:10px 18px;font:inherit;font-weight:600;cursor:pointer;display:inline-block}
button:hover,.btn:hover{filter:brightness(1.1);text-decoration:none}
.btn.sec{background:#334155}.btn.ok{background:#16a34a}.btn.bad{background:#dc2626}.btn.sm{padding:5px 12px;font-size:13px}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase}
.flash{padding:10px 14px;border-radius:10px;margin-bottom:12px}.flash.ok{background:#14532d;color:#bbf7d0}.flash.error{background:#7f1d1d;color:#fecaca}
.pill{padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600}
.pill.pending{background:#78350f;color:#fde68a}.pill.approved,.pill.paid{background:#14532d;color:#bbf7d0}.pill.rejected{background:#7f1d1d;color:#fecaca}
.pos{color:var(--ok)}.neg{color:var(--err)}.mut{color:var(--mut)}
.auth{max-width:400px;margin:60px auto}
.cap{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:10px 0}.cap img{border-radius:8px;border:1px solid var(--line)}
.row{display:flex;gap:10px;flex-wrap:wrap}.row>*{flex:1;min-width:140px}
pre{white-space:pre-wrap;word-break:break-word;background:#0f172a;border-radius:8px;padding:10px;margin:0;font:inherit}
@media(max-width:600px){th:nth-child(n+5),td:nth-child(n+5){font-size:12px}}
'''

from jinja2 import DictLoader
app.jinja_env.loader = DictLoader(TEMPLATES)


@app.route('/style.css')
def css():
    from flask import Response
    r = Response(CSS, mimetype='text/css')
    r.headers['Cache-Control'] = 'public, max-age=3600'
    return r


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
