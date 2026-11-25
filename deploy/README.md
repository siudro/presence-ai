# Deployment notes — PRESENCE AI on Railway

This folder holds what the container needs and nothing else:

- `entrypoint.sh` — makes the data volume writable, then drops from root
- `railway-setup.md` — the checklist a first-time deployer follows

The app itself is standard library only, so there is no requirements.txt.
Presence of this file is asserted by the test suite, so the folder cannot
quietly disappear from the repository.
