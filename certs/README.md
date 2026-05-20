# TLS certificates for EWS sync

| File | In git | Purpose |
|------|--------|---------|
| `mts-extra.pem` | yes | MTS / corp root CAs (from macOS System keychain) |
| `combined-ca-bundle.pem` | no (generated) | certifi + `mts-extra.pem` for VPN on/off |

Regenerate corp CAs:

```bash
../scripts/export_mts_cas.sh
```

`mail_sync.sh` rebuilds `combined-ca-bundle.pem` when `mts-extra.pem` or certifi bundle is newer.
