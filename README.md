# WorkPortal

User login + 4 work dashboards + wallet + UPI withdrawal requests + full admin panel.

## Run (Windows / Mac / Linux)

```
pip install -r requirements.txt
set ADMIN_PASSWORD=YourStrongPassword      (Windows)
export ADMIN_PASSWORD=YourStrongPassword   (Mac/Linux)
python app.py
```

Open http://localhost:5000  
Admin login: username `admin`, password = what you set in ADMIN_PASSWORD
(if you don't set it, a random one is printed in the terminal on first run).

## Dashboards

| Dashboard | What it is |
|-----------|-----------|
| Work 1 | Auto captcha. User solves, wallet is credited instantly. Rate and daily limit are set in Admin > Settings. |
| Work 2 / 3 / 4 | Tasks you create in Admin > Tasks (assignments etc.). User submits, you approve or reject, approve pays the reward. |

Rename the dashboards any time in Admin > Settings.

## Admin panel (/admin)

- **Tasks**: create work for everyone or assign to one username, set reward, make multiple copies, enable/disable
- **Submissions**: read the user's work, Approve & pay / Reject
- **Withdrawals**: shows amount + UPI ID. Pay from your own UPI app, then "Mark paid" (add UTR in note). "Reject" refunds the user's wallet.
- **Users**: search, add/deduct balance, block/unblock, reset password
- **Settings**: captcha rate, daily limit, minimum withdrawal, dashboard names

## Going live (for real users)

1. Use HTTPS (put it behind nginx/Caddy, or host on Render / Railway / a VPS).
2. Run with gunicorn: `gunicorn -w 2 -b 0.0.0.0:8000 app:app`
3. Set `ADMIN_PASSWORD` and `SECRET_KEY` as environment variables.
4. Back up `data.db` regularly (it holds all balances).

## Rules built in

- No deposit or joining fee from users, wallet only holds money earned from work.
- Withdrawal amount is held immediately and refunded if rejected, so balance can't be spent twice.
- Approvals/payments are protected against double-clicks (no double payment).
- CSRF protection on all forms, hashed passwords.

Note: captcha payouts are calculated from your rate, so make sure your rate x volume is affordable before inviting many users.
