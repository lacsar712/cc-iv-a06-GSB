import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post, patch, delete
from litestar.exceptions import HTTPException
from litestar.status_codes import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
)
from passlib.context import CryptContext

from db import SCHEMA, connect
from rules import block_reason, judge, window_active

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            for code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, voc, isc, ff, verdict, reason, now, now),
                )
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="观察员只能查看，不能修改")
    return user


def parse_hour(value, label: str) -> int:
    try:
        hour = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{label}必须是 0-23 的整点")
    if not 0 <= hour <= 23:
        raise HTTPException(status_code=400, detail=f"{label}必须是 0-23 的整点")
    return hour


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                      created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")

    # 是否处于覆盖期只认后台数据库时刻（NOW()），不用浏览端本机钟。
    # 拒收单（iv_scans.status='rejected'）与封锁痕迹（snow_blocks）在同一事务内
    # 同批落库，任何一边失败整体回滚，缺一边本题作废。
    with connect() as conn:
        clock = conn.execute(
            "SELECT NOW() AS now, EXTRACT(HOUR FROM NOW())::int AS hour"
        ).fetchone()
        server_now = clock["now"]
        server_hour = clock["hour"]

        window = conn.execute(
            """SELECT id, array_code, coverage_min, start_hour, end_hour
               FROM snow_windows
               WHERE deleted_at IS NULL
                 AND (%s = array_code OR %s LIKE array_code || '-%%')
               ORDER BY id DESC""",
            (code, code),
        ).fetchone()

        if window is not None and window_active(
            server_hour, window["start_hour"], window["end_hour"]
        ):
            reason = block_reason(
                window["array_code"],
                float(window["coverage_min"]),
                window["start_hour"],
                window["end_hour"],
            )
            with conn.transaction():
                scan = conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at)
                       VALUES (%s,%s,%s,%s,'rejected',NULL,%s,%s,%s)
                       RETURNING id""",
                    (code, voc, isc, ff, reason, user["username"], server_now),
                ).fetchone()
                conn.execute(
                    """INSERT INTO snow_blocks
                       (window_id, scan_id, string_code, blocked_by, blocked_at)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (window["id"], scan["id"], code, user["username"], server_now),
                )
            conn.commit()
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=reason)

        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, status, created_by, created_at)
               VALUES (%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                         created_by, created_at, processed_at""",
            (code, voc, isc, ff, user["username"], server_now),
        ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/snow/windows")
async def list_windows(request: Request) -> dict:
    need_login(request)
    with connect() as conn:
        clock = conn.execute(
            "SELECT NOW() AS now, EXTRACT(HOUR FROM NOW())::int AS hour"
        ).fetchone()
        rows = conn.execute(
            """SELECT id, array_code, coverage_min, start_hour, end_hour,
                      created_by, created_at
               FROM snow_windows
               WHERE deleted_at IS NULL
               ORDER BY id DESC"""
        ).fetchall()
        windows = []
        for r in rows:
            item = dump(r)
            item["active"] = window_active(clock["hour"], r["start_hour"], r["end_hour"])
            windows.append(item)
        return {
            "server_time": clock["now"].isoformat(),
            "server_hour": clock["hour"],
            "windows": windows,
        }


@post("/api/snow/windows", status_code=201)
async def create_window(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    array_code = (data.get("array_code") or "").strip()
    if not array_code:
        raise HTTPException(status_code=400, detail="阵列不能为空")
    try:
        coverage_min = float(data.get("coverage_min"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="覆盖下限必须是数字")
    if not 0 <= coverage_min <= 100:
        raise HTTPException(status_code=400, detail="覆盖下限必须在 0-100 之间")
    start_hour = parse_hour(data.get("start_hour"), "起始钟点")
    end_hour = parse_hour(data.get("end_hour"), "结束钟点")
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO snow_windows
               (array_code, coverage_min, start_hour, end_hour, created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,%s)
               RETURNING id, array_code, coverage_min, start_hour, end_hour,
                         created_by, created_at""",
            (array_code, coverage_min, start_hour, end_hour,
             user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        conn.commit()
        return dump(row)


@patch("/api/snow/windows/{win_id:int}")
async def update_window(request: Request, win_id: int) -> dict:
    user = need_writer(request)
    data = await request.json()
    fields, params = [], []
    if "coverage_min" in data:
        try:
            coverage_min = float(data.get("coverage_min"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="覆盖下限必须是数字")
        if not 0 <= coverage_min <= 100:
            raise HTTPException(status_code=400, detail="覆盖下限必须在 0-100 之间")
        fields.append("coverage_min = %s")
        params.append(coverage_min)
    if "start_hour" in data:
        fields.append("start_hour = %s")
        params.append(parse_hour(data.get("start_hour"), "起始钟点"))
    if "end_hour" in data:
        fields.append("end_hour = %s")
        params.append(parse_hour(data.get("end_hour"), "结束钟点"))
    if not fields:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    params.append(win_id)
    with connect() as conn:
        row = conn.execute(
            f"""UPDATE snow_windows SET {', '.join(fields)}
                WHERE id = %s AND deleted_at IS NULL
                RETURNING id, array_code, coverage_min, start_hour, end_hour,
                          created_by, created_at""",
            params,
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="积雪窗不存在")
        conn.commit()
        return dump(row)


@delete("/api/snow/windows/{win_id:int}", status_code=200)
async def delete_window(request: Request, win_id: int) -> dict:
    user = need_writer(request)
    with connect() as conn:
        row = conn.execute(
            """UPDATE snow_windows SET deleted_at = NOW()
               WHERE id = %s AND deleted_at IS NULL
               RETURNING id""",
            (win_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="积雪窗不存在")
        conn.commit()
        return {"ok": True, "id": win_id}


@get("/api/snow/blocks")
async def list_blocks(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT b.id, b.window_id, b.scan_id, b.string_code,
                      b.blocked_by, b.blocked_at,
                      w.array_code, w.coverage_min, w.start_hour, w.end_hour
               FROM snow_blocks b
               JOIN snow_windows w ON w.id = b.window_id
               ORDER BY b.id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


app = Litestar(route_handlers=[
    health,
    login,
    list_logs,
    create_log,
    list_windows,
    create_window,
    update_window,
    delete_window,
    list_blocks,
])
