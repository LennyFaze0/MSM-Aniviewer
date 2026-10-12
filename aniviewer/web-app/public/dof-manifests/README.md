# DOF Manifest Snapshots

This directory contains bundle names and byte sizes from the public Dawn of Fire
`bundles.json` manifests. It does not contain game bundles or extracted assets.

Snapshots currently cover:

- iOS build `00018241-2807c732a3da`
- Android build `00018345-082b08f909da`

The web asset grabber constructs each download URL against the original
`https://dlc2.bbbgame.net/monster_babies/dlc` endpoint. These snapshots exist because
that endpoint does not provide the CORS response header required for a GitHub Pages
application to read arbitrary manifests directly.
