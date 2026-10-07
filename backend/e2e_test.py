"""积雪覆盖窗端到端验证：真实 PostgreSQL + Litestar ASGI in-process。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pgserver
import psycopg

TMP = tempfile.mkdtemp(prefix="pgiv-e2e-")
server = pgserver.get_server(TMP, cleanup_mode="delete")
admin_uri = server.get_uri()
print("admin uri:", admin_uri)

with psycopg.connect(admin_uri, autocommit=True) as conn:
    conn.execute("CREATE ROLE app LOGIN PASSWORD 'app' SUPERUSER")
    conn.execute("CREATE DATABASE pvivscan OWNER app")

# pgserver 走 Unix socket：postgresql://postgres:@/postgres?host=<sockdir>
import urllib.parse as up
q = up.parse_qs(up.urlparse(admin_uri).query)
sockdir = q.get("host", [""])[0]
if sockdir:
    os.environ["DATABASE_URL"] = f"postgresql://app:app@/pvivscan?host={up.quote(sockdir)}"
else:
    u = up.urlparse(admin_uri)
    os.environ["DATABASE_URL"] = f"postgresql://app:app@{u.hostname}:{u.port}/pvivscan"

import api  # noqa: E402  种子在此执行
from db import connect  # noqa: E402
from litestar.testing import TestClient  # noqa: E402

client = TestClient(app=api.app)
fails = []


def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", name, extra)
    if not cond:
        fails.append(name)


# ---------- 登录 ----------
r = client.post("/api/auth/login", json={"username": "scanner", "password": "scan123456"})
check("scanner 登录 200", r.status_code == 200)
tok = r.json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

r = client.post("/api/auth/login", json={"username": "watcher", "password": "watch123456"})
check("watcher 登录 200", r.status_code == 200)
wH = {"Authorization": f"Bearer {r.json()['access_token']}"}

# ---------- 后台钟点 ----------
with connect() as conn:
    hour = conn.execute("SELECT EXTRACT(HOUR FROM NOW())::int h").fetchone()["h"]

r = client.get("/api/snow/windows", headers=H)
check("窗列表 200", r.status_code == 200 and r.json()["server_hour"] == hour,
      f"(server_hour={r.json().get('server_hour')}, db={hour})")

# ---------- 配一个"此刻生效"的积雪窗：阵列A ----------
start = hour
end = (hour + 2) % 24  # 与 start 不相等，跨零点也覆盖此刻
r = client.post("/api/snow/windows", headers=H, json={
    "array_code": "阵列A", "coverage_min": 80,
    "start_hour": start, "end_hour": end,
})
check("创建积雪窗 201", r.status_code == 201, r.text)
wid = r.json()["id"]

r = client.get("/api/snow/windows", headers=H)
w = next(x for x in r.json()["windows"] if x["id"] == wid)
check("后台判定此刻 active=True", w["active"] is True)
check("不回传任何本机钟字段（判窗只在后台）",
      "server_time" in r.json() and "browser_hour" not in r.json())

# ---------- 窗内提交：必须 409，拒收单+封锁痕迹同批 ----------
with connect() as conn:
    before_scans = conn.execute("SELECT COUNT(*) c FROM iv_scans WHERE status='rejected'").fetchone()["c"]
    before_blocks = conn.execute("SELECT COUNT(*) c FROM snow_blocks").fetchone()["c"]

r = client.post("/api/logs", headers=H, json={
    "string_code": "阵列A-串09", "voc_v": 40.1, "isc_a": 9.0, "fill_factor": 0.77,
})
check("窗内提交 409", r.status_code == 409, f"got {r.status_code} {r.text}")
check("提示等积雪消完再收", "等积雪消完再收" in r.json().get("detail", ""), r.json().get("detail", ""))

with connect() as conn:
    after_scans = conn.execute("SELECT COUNT(*) c FROM iv_scans WHERE status='rejected'").fetchone()["c"]
    after_blocks = conn.execute("SELECT COUNT(*) c FROM snow_blocks").fetchone()["c"]
    pair = conn.execute(
        """SELECT s.id sid, b.id bid, s.reason, b.blocked_at, s.created_at
           FROM iv_scans s JOIN snow_blocks b ON b.scan_id = s.id
           WHERE s.string_code='阵列A-串09' AND s.status='rejected'"""
    ).fetchone()
check("拒收单 +1", after_scans == before_scans + 1)
check("封锁痕迹 +1", after_blocks == before_blocks + 1)
check("拒收单与痕迹 scan_id 同批对应", pair is not None)
check("痕迹与拒收时刻一致（同事务记下）",
      pair is not None and pair["blocked_at"] == pair["created_at"])

r = client.get("/api/snow/blocks", headers=H)
check("封锁痕迹可查", r.status_code == 200 and any(
    b["scan_id"] == pair["sid"] and b["array_code"] == "阵列A" for b in r.json()))

# 同阵列再挡一单
r = client.post("/api/logs", headers=H, json={
    "string_code": "阵列A-串10", "voc_v": 39.5, "isc_a": 8.8, "fill_factor": 0.75})
check("窗内第二单仍 409", r.status_code == 409)
with connect() as conn:
    n_scan = conn.execute("SELECT COUNT(*) c FROM iv_scans WHERE status='rejected' AND string_code LIKE '阵列A-%'").fetchone()["c"]
    n_blk = conn.execute("SELECT COUNT(*) c FROM snow_blocks b JOIN iv_scans s ON s.id=b.scan_id WHERE s.string_code LIKE '阵列A-%'").fetchone()["c"]
check("每单必留痕（2 拒收 / 2 痕迹）", n_scan == 2 and n_blk == 2, f"{n_scan}/{n_blk}")

# 别的阵列不受影响
r = client.post("/api/logs", headers=H, json={
    "string_code": "阵列B-串11", "voc_v": 38.0, "isc_a": 8.4, "fill_factor": 0.61})
check("非封锁阵列 201", r.status_code == 201, f"{r.status_code} {r.text}")

# ---------- 故障注入：痕迹写不进时拒收单必须一起回滚（缺一边作废） ----------
with connect() as conn:
    conn.execute("""CREATE OR REPLACE FUNCTION _raise_always() RETURNS trigger LANGUAGE plpgsql
                    AS $$ BEGIN RAISE EXCEPTION 'injected snow_blocks fault'; END; $$""")
    conn.execute("""CREATE TRIGGER trg_block_fail BEFORE INSERT ON snow_blocks
                    EXECUTE FUNCTION _raise_always()""")
    conn.commit()
    c_scan = conn.execute("SELECT COUNT(*) c FROM iv_scans WHERE status='rejected'").fetchone()["c"]
    c_blk = conn.execute("SELECT COUNT(*) c FROM snow_blocks").fetchone()["c"]

r = client.post("/api/logs", headers=H, json={
    "string_code": "阵列A-串12", "voc_v": 40.0, "isc_a": 9.0, "fill_factor": 0.8})
check("痕迹写入失败 → 整单 500", r.status_code == 500, f"got {r.status_code}")
with connect() as conn:
    d_scan = conn.execute("SELECT COUNT(*) c FROM iv_scans WHERE status='rejected'").fetchone()["c"]
    d_blk = conn.execute("SELECT COUNT(*) c FROM snow_blocks").fetchone()["c"]
    stray = conn.execute("SELECT COUNT(*) c FROM iv_scans WHERE string_code='阵列A-串12'").fetchone()["c"]
check("拒收单随痕迹一起回滚", d_scan == c_scan and d_blk == c_blk)
check("没有残留的无痕迹拒收单", stray == 0)
with connect() as conn:
    conn.execute("DROP TRIGGER trg_block_fail ON snow_blocks")
    conn.execute("DROP FUNCTION _raise_always()")
    conn.commit()

# ---------- 工人：rejected 不被处理，pending 正常出结论 ----------
from worker import drain  # noqa: E402
with connect() as conn:
    drain(conn)
    conn.commit()
    rej = conn.execute("SELECT status, verdict FROM iv_scans WHERE string_code='阵列A-串09'").fetchone()
    done = conn.execute("SELECT status, verdict FROM iv_scans WHERE status='done' AND string_code='阵列B-串11' ORDER BY id DESC LIMIT 1").fetchone()
check("拒收单不被工人处理（仍 rejected、无结论）", rej["status"] == "rejected" and rej["verdict"] is None)
check("窗外正常单被处理为 done", done is not None and done["status"] == "done")

# ---------- 把覆盖期挪开后再送：应交成功 ----------
new_start = (hour + 5) % 24
new_end = (hour + 7) % 24
r = client.patch(f"/api/snow/windows/{wid}", headers=H,
                 json={"start_hour": new_start, "end_hour": new_end})
check("挪窗 PATCH 200", r.status_code == 200, r.text)
r = client.get("/api/snow/windows", headers=H)
w = next(x for x in r.json()["windows"] if x["id"] == wid)
check("挪开后后台判定 active=False", w["active"] is False, f"hours db={hour} win={new_start}-{new_end}")
r = client.post("/api/logs", headers=H, json={
    "string_code": "阵列A-串09", "voc_v": 40.2, "isc_a": 9.1, "fill_factor": 0.79})
check("窗外再送 201（应交成功）", r.status_code == 201, f"{r.status_code} {r.text}")

# ---------- 删除（软删）后彻底放行 ----------
r = client.delete(f"/api/snow/windows/{wid}", headers=H)
check("删除窗 200", r.status_code == 200)
r = client.post("/api/logs", headers=H, json={
    "string_code": "阵列A-串55", "voc_v": 41.0, "isc_a": 9.2, "fill_factor": 0.8})
check("删窗后 201", r.status_code == 201, f"{r.status_code} {r.text}")

# ---------- 观察员能看不能改 ----------
check("watcher 可看窗", client.get("/api/snow/windows", headers=wH).status_code == 200)
check("watcher 可看痕迹", client.get("/api/snow/blocks", headers=wH).status_code == 200)
check("watcher 可看扫描单", client.get("/api/logs", headers=wH).status_code == 200)
r = client.post("/api/snow/windows", headers=wH, json={
    "array_code": "阵列C", "coverage_min": 50, "start_hour": 0, "end_hour": 1})
check("watcher 配窗 403", r.status_code == 403, f"got {r.status_code}")
r = client.post("/api/logs", headers=wH, json={
    "string_code": "阵列C-串01", "voc_v": 1, "isc_a": 1, "fill_factor": 0.9})
check("watcher 提交 403", r.status_code == 403, f"got {r.status_code}")
r = client.patch(f"/api/snow/windows/{wid}", headers=wH, json={"end_hour": 3})
check("watcher 挪窗 403（窗已删也越权）", r.status_code == 403)

# ---------- 未登录 ----------
check("未登录 401", client.get("/api/snow/windows").status_code == 401)

print()
if fails:
    print(f"{len(fails)} 项失败：{fails}")
    sys.exit(1)
print("ALL E2E PASS")
