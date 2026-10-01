# patch-client-status

修复 **WorkBuddy 面板「客户端未登录」误报** 的补丁工具（2026-09-30）。

## 背景

助手每 5.6.2 版客户端之后读不了 `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`
（被客户端做了字段级加密 `$wbEncrypted`），Rust 侧解析报错、前端空 `catch` 静默吞掉，
状态灯就停在 store 初值 → 永远显示「客户端未登录」。

详细根因与完整证据链见同目录 `NOTES.md`；**源码级修复**见仓库 `src/store.ts` 的
`refreshWbClient`（改动语义与二进制补丁一致，重新构建后自带修复，不必再打补丁）。

## 用法

```bash
# 1) 只生成 .new 并校验，不动原文件
node patch_client_status.js "D:\路径\trae-work-assistant.exe"

# 2) 确认无误后替换（助手必须先完全退出）
node patch_client_status.js "D:\路径\trae-work-assistant.exe" --apply

# 不传路径时，默认取本脚本上一级目录的 trae-work-assistant.exe
node patch_client_status.js
```

如果助手正在运行不方便退出，也可以用改名法手动替换生成的 `.new`：

```powershell
Rename-Item trae-work-assistant.exe "trae-work-assistant.exe.running-$(Get-Date -f yyyyMMdd-HHmmss)"
Rename-Item trae-work-assistant.exe.new trae-work-assistant.exe
```

## 特性

- **幂等**：检测到已打过补丁会直接退出，不会重复施加
- **硬门禁**：锚点在文件里不唯一（说明前端结构变了）→ 拒绝写盘
- **硬门禁**：重新压缩后超过原槽位 → 拒绝写盘
- **回读校验**：写盘前把压缩流解回来逐字节比对
- **自动备份**：`--apply` 会先留 `*.bak-<时间戳>-preClientStatusPatch`

## 助手升级后怎么办

助手版本更新 → exe 换了 → 补丁失效，状态灯会再次误报。
重新跑一次本脚本即可；若脚本报「锚点出现 N 次」，说明前端 bundle 结构变了，
需要重新用同样的思路定位这两个锚点（解压 JS → 搜 `wbClientLoggedIn` → 看 `refreshWbClient`）。

## 回滚

把 `rollback.bat` 复制到 exe 同目录后双击运行（它按 `%~dp0` 定位 exe，并自动匹配
`*.bak-*-preClientStatusPatch`），或手动把
`trae-work-assistant.exe.bak-*-preClientStatusPatch` 改名回 `trae-work-assistant.exe`。

## 技术备注

- exe 内嵌前端是 brotli 压缩、按 `[资源路径][压缩数据]` 顺序拼接
- Rust 侧读取的资源长度 = **整个槽位**（本例 187,371 字节），而 brotli 流本身只有 173,174 字节，
  说明解码器会忽略尾部残留 —— 所以「新流短于原槽位」可以安全覆写
- 解码一律用 **Node 内置 `zlib`**；Python 的 brotli 包在本机会解不开（且对尾部残留的容忍度不同）
