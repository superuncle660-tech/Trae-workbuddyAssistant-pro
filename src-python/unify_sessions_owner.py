#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一 WorkBuddy 左侧任务栏的任务归属。

原理
----
左侧任务栏 = `workbuddy.db` 的 `sessions` 表，客户端按「当前登录账号的 uid」
过滤 `user_id` 后渲染。多账号切换时各账号只看得到自己的任务，于是左栏不统一。

本脚本把 `sessions` 里所有记录的 `user_id` 统一设为指定 uid ——
即「谁登录，谁就持有全部任务」，于是任何账号登录后左栏都是同一份完整列表。

安全性
------
- 默认 **dry-run**，只报告不写盘；必须显式 `--apply` 才改。
- 首次执行会做 sqlite 在线一致性备份，并记录每行的**原始归属**（baseline）。
- `--rollback` 按 baseline 精确还原（后进先出）。
- 客户端在运行时会**警告**（内存缓存可能覆盖写入），可用 `--force` 跳过。

用法
----
  python unify_sessions_owner.py --report
  python unify_sessions_owner.py --target-uid <uid>            # dry-run
  python unify_sessions_owner.py --target-uid <uid> --apply
  python unify_sessions_owner.py --rollback
  python unify_sessions_owner.py --target-uid <uid> --apply --json --quiet
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys

BS = os.sep
HOME = os.path.expanduser("~")
DEFAULT_DB = os.path.join(HOME, ".workbuddy", "workbuddy.db")
BASE = "D:" + BS + "Trae Work 助手" + BS
BACKUP_DIR = os.path.join(BASE, "workbuddy-db-backup")
STATE_PATH = os.path.join(BASE, "logs", "session-owner-unify.json")
LOG_PATH = os.path.join(BASE, "logs", "session-owner-unify.log")

TARGET_MARK = "__unified__"


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg, quiet=False):
    line = "[%s] %s" % (now(), msg)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if not quiet:
        print(msg)


def sha256(p, blk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(blk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def wb_running():
    """WorkBuddy.exe 是否在运行。tasklist 输出是 GBK，但进程名是 ASCII，按字节判定。"""
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq WorkBuddy.exe", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=20,
        )
        return b"WorkBuddy.exe" in r.stdout
    except Exception:
        return None


def connect(db, readonly=False):
    uri = "file:%s%s" % (db.replace(BS, "/"), "?mode=ro" if readonly else "")
    con = sqlite3.connect(uri, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {"baseline": {}, "baselineAt": None, "history": []}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def group_rows(con):
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM sessions")
    total = cur.fetchone()[0]
    cur.execute(
        "SELECT user_id, COUNT(*) c, SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END) vis "
        "FROM sessions GROUP BY user_id ORDER BY c DESC"
    )
    return total, [dict(r) for r in cur.fetchall()]


def make_backup(db):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, "workbuddy.db.%s.preUnify.bak" % ts)
    src = sqlite3.connect("file:%s?mode=ro" % db.replace(BS, "/"), uri=True)
    bak = sqlite3.connect(dst)
    with bak:
        src.backup(bak)
    bak.close()
    src.close()
    return dst


def report(db, target, quiet=False):
    con = connect(db, readonly=True)
    total, groups = group_rows(con)
    con.close()
    log("数据库: %s (%d B)" % (db, os.path.getsize(db)), quiet)
    log("sessions 共 %d 行，按 user_id 分组：" % total, quiet)
    for g in groups:
        tag = "  ← 目标" if g["user_id"] == target else ""
        log("   %s  %2d 行（可见 %s 条）%s" % (str(g["user_id"])[:8], g["c"], g["vis"], tag), quiet)
    need = sum(g["c"] for g in groups if g["user_id"] != target)
    log("需要改归属的行数: %d" % need, quiet)
    return total, groups, need


def apply_unify(db, target, quiet=False, force=False, no_backup=False):
    state = load_state()

    if not state["baseline"]:
        if no_backup:
            backup = None
        else:
            backup = make_backup(db)
            log("已备份: %s (%d B)" % (backup, os.path.getsize(backup)), quiet)
        state["baselinePath"] = backup
        state["baselineAt"] = now()

    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT id, user_id FROM sessions")
    rows = cur.fetchall()

    changed = []
    for r in rows:
        sid, uid = r["id"], r["user_id"]
        if sid not in state["baseline"]:
            state["baseline"][sid] = uid
        if uid != target:
            changed.append(sid)

    if not changed:
        con.close()
        log("已统一，无需改动。", quiet)
        return 0

    try:
        with con:
            cur.executemany(
                "UPDATE sessions SET user_id = ? WHERE id = ?",
                [(target, sid) for sid in changed],
            )
    except Exception:
        con.close()
        raise
    con.commit()

    cur.execute("SELECT COUNT(*) FROM sessions WHERE user_id != ?", (target,))
    left = cur.fetchone()[0]
    con.close()

    state["history"].append(
        {"at": now(), "targetUid": target, "changed": len(changed), "ids": changed}
    )
    save_state(state)

    log("已把 %d 行的 user_id 统一为 %s（剩余不一致 %d 行）" % (len(changed), str(target)[:8], left), quiet)
    log("baseline 记录在: %s" % STATE_PATH, quiet)
    return len(changed)


def rollback(db, quiet=False):
    state = load_state()
    base = state.get("baseline") or {}
    if not base:
        log("没有 baseline 记录，无法回滚。", quiet)
        return 1
    con = sqlite3.connect(db, timeout=30)
    cur = con.cursor()
    n = 0
    with con:
        for sid, uid in base.items():
            cur.execute("UPDATE sessions SET user_id = ? WHERE id = ?", (uid, sid))
            n += cur.rowcount
    con.commit()
    con.close()
    state["history"].append({"at": now(), "targetUid": None, "changed": n, "action": "rollback"})
    save_state(state)
    log("已按 baseline 还原 %d 行的 user_id" % n, quiet)
    return 0


def main():
    ap = argparse.ArgumentParser(description="统一 WorkBuddy 左栏任务归属")
    ap.add_argument("--target-uid", default=None, help="目标账号 uid（统一后所有任务都归它）")
    ap.add_argument("--db", default=DEFAULT_DB, help="workbuddy.db 路径")
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认 dry-run）")
    ap.add_argument("--report", action="store_true", help="只报告当前分布")
    ap.add_argument("--rollback", action="store_true", help="按 baseline 还原")
    ap.add_argument("--force", action="store_true", help="跳过「客户端在运行」检查")
    ap.add_argument("--no-backup", action="store_true", help="首次执行时不备份")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    ap.add_argument("--quiet", action="store_true", help="不打印过程，只写日志")
    ap.add_argument("--state", default=None, help="状态文件路径（测试隔离用）")
    ap.add_argument("--log", dest="logfile", default=None, help="日志文件路径（测试隔离用）")
    a = ap.parse_args()

    global STATE_PATH, LOG_PATH
    if a.state:
        STATE_PATH = a.state
    if a.logfile:
        LOG_PATH = a.logfile

    if not os.path.exists(a.db):
        print("找不到数据库: %s" % a.db, file=sys.stderr)
        return 1

    if a.rollback:
        return rollback(a.db, a.quiet)

    if a.report or not a.target_uid:
        total, groups, need = report(a.db, a.target_uid, a.quiet)
        if a.json:
            print(json.dumps(
                {"total": total, "groups": groups, "need": need, "target": a.target_uid},
                ensure_ascii=False))
        return 0

    run = wb_running()
    if run and not a.force:
        msg = "WorkBuddy 客户端正在运行：内存缓存可能覆盖本次写入，建议关闭后再执行（或用 --force）"
        if a.json:
            print(json.dumps({"ok": False, "error": "client_running", "message": msg},
                             ensure_ascii=False))
        else:
            log("⚠ " + msg, a.quiet)
        return 2

    total, groups, need = report(a.db, a.target_uid, a.quiet)

    if not a.apply:
        log("（dry-run）如需执行，加 --apply", a.quiet)
        if a.json:
            print(json.dumps({"ok": True, "dryRun": True, "need": need,
                              "target": a.target_uid, "total": total}, ensure_ascii=False))
        return 0

    n = apply_unify(a.db, a.target_uid, a.quiet, a.force, a.no_backup)
    if a.json:
        print(json.dumps({"ok": True, "changed": n, "target": a.target_uid,
                          "total": total}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
