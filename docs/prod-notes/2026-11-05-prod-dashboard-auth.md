# Dashboard login on private routes: what you need to do

PROD's dashboard listens on the network (`web.host` = `0.0.0.0`, port 8888), so any device
that can reach the machine could open these without logging in:

- `/api/accounts`: the real E*TRADE accounts (holdings, balances);
- `/plan/intc`: the personal INTC plan page (`Data/INTC_Income_Plan.html`);
- `/api/pipeline/logs`, `/api/logs/aether`, `/api/logs/aether/json`: logs (machine paths,
  auth events, trade decisions);
- `/api/chat`: it took a login header but never checked it, so anyone could spend the AI
  provider's quota.

After this deploy they all need the dashboard login (top right). Everything else is
unchanged.

## 1. Before deploying

Make sure you can log in to the dashboard (an `admins` entry in `Data\config.json` under
`web`). Without a working login you'll lose the Accounts tab, the System logs, chat and the
plan link until it's fixed.

## 2. Deploy

Merge the PR, pull, restart the dashboard:

```powershell
venv_new\Scripts\python.exe server.py restart
```

(or however you normally restart it).

## 3. Check

- Logged out: Accounts says "🔒 Log in (top right) to see this."; chat replies "🔒 Log in…";
  the INTC plan link says to log in first.
- Logged in: Accounts, logs, chat and the plan link work as before. The plan opens in a new
  tab.
- From another device without logging in, `http://<prod>:8888/api/accounts` returns 401.

## Optional, not in this PR

If nobody else needs the dashboard, setting `web.host` to `127.0.0.1` in `Data\config.json`
limits it to the PROD machine itself. That's a config choice for you.

## Roll back

Revert the PR and restart the dashboard.
