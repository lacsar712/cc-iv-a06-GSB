import os
from datetime import datetime, time, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from litestar import Litestar, Request, delete, get, post, put
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect
from rules import judge

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
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="观察员只读，不能改积雪窗或提交扫描")
    return user


def parse_clock(raw, field: str) -> time:
    text = (str(raw) if raw is not None else "").strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            pass
    raise HTTPException(status_code=400, detail=f"{field}格式应为 HH:MM")


def parse_window_payload(data: dict) -> tuple[str, float, time, time]:
    array_code = (data.get("array_code") or "").strip()
    if not array_code:
        raise HTTPException(status_code=400, detail="阵列不能为空")
    try:
        cover_min = float(data.get("cover_min"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="覆盖下限必须是数字")
    if not 0 <= cover_min <= 1:
        raise HTTPException(status_code=400, detail="覆盖下限应在 0 到 1 之间")
    start_t = parse_clock(data.get("start_time"), "起始钟点")
    end_t = parse_clock(data.get("end_time"), "终止钟点")
    if start_t == end_t:
        raise HTTPException(status_code=400, detail="起止钟点不能相同，跨午夜请让窗跨过 00:00")
    return array_code, cover_min, start_t, end_t


WINDOW_SELECT = """
    SELECT id, array_code, cover_min, start_time, end_time,
           created_by, created_at, updated_at,
           CASE WHEN start_time < end_time
                THEN LOCALTIME BETWEEN start_time AND end_time
                ELSE LOCALTIME >= start_time OR LOCALTIME < end_time
           END AS active_now
    FROM snow_windows
"""


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

    with connect() as conn:
        # 是否落在覆盖期只由数据库后台钟点 LOCALTIME 判定，不接受浏览端本机钟。
        hit = conn.execute(
            """
            SELECT id, array_code, cover_min, start_time, end_time FROM snow_windows
            WHERE %s = array_code OR %s LIKE array_code || '-%%'
            ORDER BY char_length(array_code) DESC, id
            LIMIT 1
            """,
            (code, code),
        ).fetchone()
        if hit is not None:
            in_window = conn.execute(
                """SELECT CASE WHEN %s::time < %s::time
                               THEN LOCALTIME BETWEEN %s::time AND %s::time
                               ELSE LOCALTIME >= %s::time OR LOCALTIME < %s::time
                          END AS hit""",
                (
                    hit["start_time"], hit["end_time"],
                    hit["start_time"], hit["end_time"],
                    hit["start_time"], hit["end_time"],
                ),
            ).fetchone()["hit"]
            if in_window:
                reason = (
                    f"阵列{hit['array_code']}处于积雪覆盖封锁期"
                    f"（覆盖下限 {hit['cover_min']:.0%}，每日 "
                    f"{hit['start_time']:%H:%M}–{hit['end_time']:%H:%M}），"
                    f"等积雪消完再收"
                )
                # 真实拒收与封锁痕迹同一批落下：先写痕迹并提交，再回拒收；
                # 痕迹写不进去就抛错，扫描单也不会被放行（少一边即作废）。
                conn.execute(
                    """INSERT INTO snow_blocks
                       (window_id, array_code, string_code, voc_v, isc_a, fill_factor,
                        reason, blocked_by, blocked_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())""",
                    (
                        hit["id"], hit["array_code"], code, voc, isc, ff,
                        reason, user["username"],
                    ),
                )
                conn.commit()
                raise HTTPException(status_code=423, detail=reason)

        now = datetime.now(timezone.utc)
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, status, created_by, created_at)
               VALUES (%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                         created_by, created_at, processed_at""",
            (code, voc, isc, ff, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/snow/clock")
async def snow_clock(request: Request) -> dict:
    need_login(request)
    with connect() as conn:
        row = conn.execute(
            """SELECT to_char(LOCALTIME, 'HH24:MI:SS') AS clock,
                      to_char(CURRENT_DATE, 'YYYY-MM-DD') AS day,
                      current_setting('TIMEZONE') AS tz"""
        ).fetchone()
        return dict(row)


@get("/api/snow/windows")
async def list_windows(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(WINDOW_SELECT + " ORDER BY id DESC").fetchall()
        return [dump(r) for r in rows]


@post("/api/snow/windows", status_code=201)
async def create_window(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    array_code, cover_min, start_t, end_t = parse_window_payload(data)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO snow_windows
               (array_code, cover_min, start_time, end_time,
                created_by, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s, now(), now())
               RETURNING id""",
            (array_code, cover_min, start_t, end_t, user["username"]),
        ).fetchone()
        conn.commit()
        win_id = row["id"]
    with connect() as conn:
        row = conn.execute(WINDOW_SELECT + " WHERE id = %s", (win_id,)).fetchone()
        return dump(row)


@put("/api/snow/windows/{win_id:int}")
async def update_window(request: Request, win_id: int) -> dict:
    need_writer(request)
    data = await request.json()
    array_code, cover_min, start_t, end_t = parse_window_payload(data)
    with connect() as conn:
        row = conn.execute(
            """UPDATE snow_windows
               SET array_code=%s, cover_min=%s, start_time=%s, end_time=%s, updated_at=now()
               WHERE id=%s
               RETURNING id""",
            (array_code, cover_min, start_t, end_t, win_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="积雪窗不存在")
        conn.commit()
    with connect() as conn:
        row = conn.execute(WINDOW_SELECT + " WHERE id = %s", (win_id,)).fetchone()
        return dump(row)


@delete("/api/snow/windows/{win_id:int}", status_code=204)
async def delete_window(request: Request, win_id: int) -> None:
    need_writer(request)
    with connect() as conn:
        row = conn.execute("DELETE FROM snow_windows WHERE id=%s RETURNING id", (win_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="积雪窗不存在")
        conn.commit()


@get("/api/snow/blocks")
async def list_blocks(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, window_id, array_code, string_code, voc_v, isc_a, fill_factor,
                      reason, blocked_by, blocked_at
               FROM snow_blocks ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


app = Litestar(
    route_handlers=[
        health, login, list_logs, create_log,
        snow_clock, list_windows, create_window, update_window, delete_window, list_blocks,
    ]
)
