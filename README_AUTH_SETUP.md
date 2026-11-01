# Step 4 — Generate a Garmin Authentication Token :key:

This is part of the [Getting Started](README.md#getting-started-dart) guide. You'll generate a token that proves to
Garmin you are logged in. The workflow will reuse it automatically on every run without ever prompting for credentials
again.

## Prerequisites :hammer_and_wrench:

* [`uv`](https://docs.astral.sh/uv/getting-started/installation/) installed on your machine

## Generate the token :computer:

Open a terminal and run the following command:

```shell
uv run https://raw.githubusercontent.com/chloevoyer/garmin-to-notion/main/scripts/generate-garmin-token.py
```

You will be prompted for:

1. Your Garmin email
2. Your Garmin password (hidden while typing)
3. Your 2FA code, if applicable (from your authenticator app or SMS)

Once complete, the script will print your token between two lines of `=` signs.
**Copy the entire JSON block** — everything from the opening `{` to the closing `}`, inclusive.

> **Getting a 429 error?** Garmin blocks certain IP ranges (VPNs, corporate networks, some ISPs) on their login
> endpoint. If this happens, try
> * disabling your VPN.
> * running the command while connected to your phone's mobile hotspot.
>
> Once the token is generated, this no longer matters — the daily sync never re-authenticates from scratch.

## Add the token to GitHub :lock:

* Go to your fork of this repository on GitHub
* Click the **Settings** tab
* In the left sidebar, go to **Secrets and variables → Actions**
* Click **New repository secret**
  * **Name:** `GARMIN_AUTH_TOKEN`
  * **Value:** paste the token you copied above
* Click **Add secret**

Once done, continue to [Step 5 — Set Environment Secrets](README.md#5-set-environment-secrets).

## Token expiry :hourglass:

The token refreshes itself automatically on each run. You should never need to repeat this process unless you change
your Garmin password or explicitly revoke access from your Garmin account settings.

If the workflow suddenly fails with a **401 error**, this means Garmin has invalidated your session — most likely
because you changed your Garmin password or Garmin detected unusual activity on your account. Simply repeat the steps
above to generate a new token and update the secret.
