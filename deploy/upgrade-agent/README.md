# Upgrade agent: upgrade ZK Push from the site

With the agent installed, a System Manager upgrades zk_push from the page **ZK Push Update** (search
for it, or the link on About ZK Push): pick a release, click **Upgrade**. The page only writes a
request file into the site folder; within a minute the agent, run by cron as the bench user, backs up
and migrates every site with zk_push, restarts the bench, and shows the result and the log on the page.
If the install or a migrate fails it goes back to the previous version. The site itself never runs
commands or sudo.

All sites on a bench share the app code: an upgrade asked for on one site upgrades them all.

## Install (once, by an administrator, as root or with sudo)

The commands are for the bench at `/home/nss/frappe-bench`, user `nss`. Change both if yours differ.

1. Copy the agent out of the app folder (an upgrade replaces the app folder, and an older release may
   not have the agent):

       sudo -u nss cp /home/nss/frappe-bench/apps/zk_push/deploy/upgrade-agent/zk-push-upgrade.sh /home/nss/zk-push-upgrade.sh
       sudo chmod 755 /home/nss/zk-push-upgrade.sh

2. Let the bench user restart the bench, and nothing else (check the path with `which supervisorctl`;
   the colons must be escaped in sudoers):

       echo 'nss ALL=(root) NOPASSWD: /usr/bin/supervisorctl restart frappe-bench-web\: frappe-bench-workers\:' | sudo tee /etc/sudoers.d/zk-push-upgrade
       sudo chmod 440 /etc/sudoers.d/zk-push-upgrade
       sudo visudo -cf /etc/sudoers.d/zk-push-upgrade

3. Run it every minute from the bench user's crontab (keeps the lines already there):

       (sudo -u nss crontab -l 2>/dev/null; echo '* * * * * /home/nss/zk-push-upgrade.sh') | sudo -u nss crontab -

4. Within a minute, ZK Push Update shows **Upgrade agent: running**.

The agent refuses to upgrade if `apps/zk_push` has local changes. Its log of the last upgrade is shown on
the page (and kept in `sites/<site>/zk_push_upgrade.log`). If a release changes the agent, its notes say
so: repeat step 1.

## Remove

    (sudo -u nss crontab -l | grep -v zk-push-upgrade.sh) | sudo -u nss crontab -
    sudo rm /etc/sudoers.d/zk-push-upgrade /home/nss/zk-push-upgrade.sh
