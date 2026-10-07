FF_MIN = 0.72


def judge(fill_factor: float) -> tuple[str, str]:
    if fill_factor >= FF_MIN:
        return "合格", f"填充因子 {fill_factor} 不低于 {FF_MIN}"
    return "衰减", f"填充因子 {fill_factor} 低于 {FF_MIN}"


def window_active(server_hour: int, start_hour: int, end_hour: int) -> bool:
    """以后台数据库钟点（UTC 0-23）判定是否落在积雪窗内。

    起止相等视为全天封锁；起始大于结束表示跨零点。
    """
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= server_hour < end_hour
    return server_hour >= start_hour or server_hour < end_hour


def block_reason(array_code: str, coverage_min: float, start_hour: int, end_hour: int) -> str:
    return (
        f"阵列「{array_code}」处于积雪覆盖期（封锁钟点 {start_hour:02d}-{end_hour:02d} 时，"
        f"覆盖下限 {coverage_min:.0f}%），等积雪消完再收"
    )
