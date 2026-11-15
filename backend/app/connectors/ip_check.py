"""Does a saved Brightspace session still work from a different network?

Hosting TrojanClaw (sessions used by a server, not the student's laptop) only works if USC does not
tie the session to the network it was created on. Run from backend/:

  1. On your usual network:  python -m app.connectors.usc_login      (saves a fresh session)
                             python -m app.connectors.ip_check       (should print "works")
  2. Switch to another network (for example a phone hotspot), then:
                             python -m app.connectors.ip_check

If step 2 still prints "works", the session is not tied to your network. No cookie value is printed.
"""

import sys

import httpx
import keyring

BRIGHTSPACE = "https://brightspace.usc.edu"


def main() -> int:
    cookie = keyring.get_password("trojanclaw", "brightspace_cookie")
    if not cookie:
        print("No saved session. Run first: python -m app.connectors.usc_login")
        return 1
    public_ip = httpx.get("https://api.ipify.org", timeout=10).text
    with httpx.Client(base_url=BRIGHTSPACE, headers={"Cookie": cookie}, timeout=15, follow_redirects=False) as client:
        version = client.get("/d2l/api/lp/versions/").json()["LatestVersion"]
        response = client.get(f"/d2l/api/lp/{version}/users/whoami")
    if response.status_code == 200:
        print(f"works from {public_ip}: signed in as {response.json().get('FirstName')}")
        return 0
    print(f"does NOT work from {public_ip}: HTTP {response.status_code} (session expired, or tied to another network)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
