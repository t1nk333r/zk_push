# ZK pull agent

Reads a ZKTeco device's attendance log over the LAN every 2 minutes and sends new punches to an
ERPNext site running zk_push. For devices whose COMM. menu has no *Cloud Server Setting* (ADMS).

Run it on an always-on machine on the same network as the device. It needs to reach the device
(TCP 4370) and the site (HTTPS); nothing needs to reach it.

## Before you start

1. On the device, note the IP (Menu → COMM. → Ethernet) and make it static. Note the Comm Key
   (COMM. → PC Connection) if one is set.
2. On the site, the device appears under **ZK Device** after the agent's first run. Set
   *Ignore Punches Before*, then tick **Enabled**. Until then the agent logs `HTTP 403`.

## Docker package (`zk-pull-agent-<version>-amd64|arm64.tar.gz`)

Use `amd64` for a normal PC or server, `arm64` for a Raspberry Pi 4/5 (64-bit OS).

```bash
tar xzf zk-pull-agent-<version>-<arch>.tar.gz
cd zk-pull-agent-<version>-<arch>
docker load -i image.tar.gz
cp .env.example .env        # set DEVICE_IP, SITE_URL, COMM_KEY
docker compose up -d
docker compose logs -f
```

It restarts on boot. To update: load the new image, set `ZK_PULL_TAG` in `.env`, `docker compose up -d`.
The state (which punches were sent) is kept in the `state` volume across updates.

Other tools in the image:

```bash
docker compose run --rm --entrypoint python zk-pull /app/backfill_from_device.py <ip> <site> --dry-run
docker compose run --rm --entrypoint python zk-pull /app/export_device_users.py <ip> > users.csv
```

## Python package (`zk-pull-agent-<version>-python.zip`), e.g. Windows

Needs Python 3.10+.

```bash
pip install -r requirements.txt
python pull_from_device.py <device_ip> https://erp.example.com --comm-key <n>
```

On Windows, add a Task Scheduler task that runs every 5 minutes:
`python C:\zk-pull\pull_from_device.py <device_ip> https://erp.example.com --once`
(`--once` exits 1 if the pull failed, so failures show in the task history.)
