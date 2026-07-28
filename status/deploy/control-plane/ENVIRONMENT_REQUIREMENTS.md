# OVH 受保护环境要求

环境值必须保存在 `/srv/linze/apps/status/.secrets/control-plane.env` 或既有受保护 Secret 管理机制中，文件权限为 `0600`，不得进入 Git。

必需键：

- `CF_TEAM_DOMAIN`
- `CF_ACCESS_AUD`
- `CF_ACCESS_ISSUER`
- `OWNER_EMAIL`
- `STATUS_PYTHON_IMAGE`：必须是带 `@sha256:` 的 Python 3.12 slim 镜像引用
- `STATUS_NGINX_IMAGE`：必须是带 `@sha256:` 的受支持 nginx 镜像引用
- `PRIVATE_DATABASE_WORKTREE`
- `LINZE_R2_REMOTE`
- `LINZE_OCI_REMOTE`
- `STATUS_BACKUP_ENCRYPTION_PROFILE`：固定为 `rclone-crypt`
- `LINZE_R2_REMOTE_IS_CRYPT`：固定为 `true`，表示已人工核验该 remote 为 crypt remote
- `LINZE_OCI_REMOTE_IS_CRYPT`：固定为 `true`，表示已人工核验该 remote 为独立 crypt remote

`LINZE_R2_REMOTE` 与 `LINZE_OCI_REMOTE` 都必须指向经过核验的 rclone crypt remote；前者底层为 R2，后者底层为独立 OCI Object Storage。不得在任务包或日志中写出 credential value。
