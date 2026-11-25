# Upgrade agent: upgrade ZK Push from the site

With the agent installed, a System Manager upgrades zk_push from the page **ZK Push Update** (search
for it, or the link on About ZK Push): pick a release, click **Upgrade**. The page only writes a
request file into the site folder; within a minute the agent, run by cron as the bench user, backs up
and migrates every site with zk_push, restarts the bench, and shows the result and the log on the page.
If the install or a migrate fails it goes back to the previous version, and restores the database of
every site it had already migrated (from a backup taken just before). The site itself never runs
commands or sudo.

All sites on a bench share the app code: an upgrade asked for on one site upgrades them all. So a
System Manager of any site can change the code every site runs; limit that with `ZK_SITES` (step 3).

It only installs releases from v0.3.7 on (`ZK_MIN_TAG`): older ones lack its checks or security fixes.
It installs only a release tag signed with the zk_push release key (`ZK_SIGNERS`), so a GitHub account
that is compromised, and publishes a tag without the key, cannot push code to the server.
It refuses a release tag that was moved on GitHub after it was first fetched.

## Install (once, by an administrator, as root or with sudo)

The commands are for the bench at `/home/nss/frappe-bench`, user `nss`. Change both if yours differ.

1. Copy the agent out of the app folder (an upgrade replaces the app folder, and an older release may
   not have the agent):

       sudo -u nss cp /home/nss/frappe-bench/apps/zk_push/deploy/upgrade-agent/zk-push-upgrade.sh /home/nss/zk-push-upgrade.sh
       sudo chmod 755 /home/nss/zk-push-upgrade.sh

1b. Copy the release signing key, the list of signers the agent trusts (from this repository, at the
   release you install; it is not a secret):

       sudo -u nss cp /home/nss/frappe-bench/apps/zk_push/deploy/upgrade-agent/release-signers /home/nss/zk-push-release-signers

   It is also attached to each release as `zk-push-release-signers`. Check the key before you trust it:
   `cut -d' ' -f3- /home/nss/zk-push-release-signers | ssh-keygen -lf -` must show
   `SHA256:tCx1NyI1o9PtFE22JR11d5GzhJ3gIe4XZeNJGJCEcRA`.
   Without this file the agent installs nothing and says so in the log.

2. Let the bench user restart the bench, and nothing else (check the path with `which supervisorctl`;
   the colons must be escaped in sudoers):

       echo 'nss ALL=(root) NOPASSWD: /usr/bin/supervisorctl restart frappe-bench-web\: frappe-bench-workers\:' | sudo tee /etc/sudoers.d/zk-push-upgrade
       sudo chmod 440 /etc/sudoers.d/zk-push-upgrade
       sudo visudo -cf /etc/sudoers.d/zk-push-upgrade

3. Run it every minute from the bench user's crontab (keeps the lines already there). `ZK_SITES` names
   the sites whose System Managers may start an upgrade; leave it out to allow every site. A request
   from another site is dropped, and that site's page says so.

       (sudo -u nss crontab -l 2>/dev/null; echo '* * * * * ZK_SITES=test.nss.sa /home/nss/zk-push-upgrade.sh') | sudo -u nss crontab -

4. Within a minute, ZK Push Update shows **Upgrade agent: running**.

If the server restarts during an upgrade, the next run marks it Failed (interrupted): check the log
and the site, then start it again. The agent refuses to upgrade if `apps/zk_push` has local changes. Its log of the last upgrade is shown on
the page (and kept in `sites/<site>/zk_push_upgrade.log`). If a release changes the agent, its notes say
so: repeat step 1.

## Remove

    (sudo -u nss crontab -l | grep -v zk-push-upgrade.sh) | sudo -u nss crontab -
    sudo rm /etc/sudoers.d/zk-push-upgrade /home/nss/zk-push-upgrade.sh
