<template>
  <div>
    <header class="topbar">
      <span class="brand">❄ 光伏组串IV扫描台</span>
      <nav>
        <button :class="{ active: view === 'scan' }" @click="view = 'scan'">IV扫描</button>
        <button :class="{ active: view === 'snow' }" @click="goSnow">积雪窗</button>
      </nav>
    </header>

    <main>
      <div v-if="!session">
        <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。观察员 watcher / watch123456 只能看不能改。</p>
        <section>
          <label>用户名</label><input v-model="loginUser" autocomplete="off" />
          <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
          <button :disabled="loading" @click="login">登录</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
      </div>

      <!-- ============ IV 扫描视图 ============ -->
      <div v-else-if="view === 'scan'">
        <p class="sub">已登录：{{ session.username }}（{{ isWriter ? "可提交" : "只读" }})。积雪覆盖期内对应阵列的提交会被挡回，须等积雪消完再收。</p>
        <section v-if="isWriter">
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" list="array-tips" />
          <datalist id="array-tips">
            <option value="阵列A-串01"></option>
            <option value="阵列A-串03"></option>
            <option value="阵列B-串11"></option>
            <option value="阵列C-串05"></option>
          </datalist>
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">⛔ {{ error }}</p>
        </section>
        <section>
          <h3>扫描单</h3>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论/原因</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id" :class="{ rejected: row.status === 'rejected' }">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td>
                  <span class="tag" :class="statusClass(row.status)">{{ statusText(row.status) }}</span>
                </td>
                <td>
                  <span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span>
                  <span v-else-if="row.status === 'rejected'" class="reason">{{ row.reason }}</span>
                  <span v-else>—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <!-- ============ 积雪窗视图 ============ -->
      <div v-else-if="view === 'snow'">
        <p class="sub">
          已登录：{{ session.username }}（{{ isWriter ? "可配置" : "观察员，只读" }}）
        </p>
        <section class="clockcard">
          <strong>后台时钟（判窗唯一依据）：</strong>
          <span class="clock">{{ snow.server_time || "—" }}</span>
          ｜后台钟点 <b>{{ snow.server_hour ?? "—" }}</b> 时
          <p class="hint">是否落在覆盖期一律以后台数据库记下的时刻对窗；浏览器本机钟不参与判定。</p>
        </section>

        <section v-if="isWriter">
          <h3>新增积雪覆盖窗</h3>
          <div class="formgrid">
            <div>
              <label>阵列</label>
              <input v-model="form.array_code" list="array-list" placeholder="选择或输入阵列，如 阵列A" />
              <datalist id="array-list">
                <option value="阵列A"></option>
                <option value="阵列B"></option>
                <option value="阵列C"></option>
              </datalist>
            </div>
            <div>
              <label>覆盖下限（%）</label>
              <input type="number" min="0" max="100" step="1" v-model="form.coverage_min" />
            </div>
            <div>
              <label>起始钟点（0-23）</label>
              <select v-model="form.start_hour"><option v-for="h in 24" :key="'s'+h" :value="h-1">{{ String(h-1).padStart(2,'0') }} 时</option></select>
            </div>
            <div>
              <label>结束钟点（0-23，可跨零点）</label>
              <select v-model="form.end_hour"><option v-for="h in 24" :key="'e'+h" :value="h-1">{{ String(h-1).padStart(2,'0') }} 时</option></select>
            </div>
          </div>
          <button :disabled="loading" @click="saveWindow">配上封锁窗</button>
          <p v-if="snowError" class="err">{{ snowError }}</p>
          <p class="hint">起止钟点相同表示全天封锁；起始大于结束表示跨零点。该阵列下所有组串在窗内送来的单一律挡回。</p>
        </section>

        <section>
          <h3>积雪窗（生效状态取自后台）</h3>
          <table>
            <thead>
              <tr><th>ID</th><th>阵列</th><th>覆盖下限</th><th>钟点</th><th>后台判定</th><th>配置人</th><th v-if="isWriter">挪窗 / 删除</th></tr>
            </thead>
            <tbody>
              <tr v-for="w in snow.windows" :key="w.id">
                <td>{{ w.id }}</td>
                <td>{{ w.array_code }}</td>
                <td>{{ w.coverage_min }}%</td>
                <td>
                  <template v-if="isWriter">
                    <select class="hsel" v-model.number="editHour[w.id].start">
                      <option v-for="h in 24" :key="'ws'+w.id+h" :value="h-1">{{ String(h-1).padStart(2,'0') }}</option>
                    </select>
                    -
                    <select class="hsel" v-model.number="editHour[w.id].end">
                      <option v-for="h in 24" :key="'we'+w.id+h" :value="h-1">{{ String(h-1).padStart(2,'0') }}</option>
                    </select>
                  </template>
                  <template v-else>{{ String(w.start_hour).padStart(2,'0') }}-{{ String(w.end_hour).padStart(2,'0') }}</template>
                </td>
                <td><span class="tag" :class="w.active ? 'block' : 'ok'">{{ w.active ? "覆盖期中（拒收）" : "窗外" }}</span></td>
                <td>{{ w.created_by }}</td>
                <td v-if="isWriter">
                  <button class="mini" @click="moveWindow(w)">挪到该钟点</button>
                  <button class="mini danger" @click="removeWindow(w)">删除</button>
                </td>
              </tr>
              <tr v-if="snow.windows.length === 0"><td colspan="7" class="empty">还没有积雪窗</td></tr>
            </tbody>
          </table>
        </section>

        <section>
          <h3>封锁痕迹</h3>
          <p class="hint">每条痕迹都与一张真实拒收单同批落库；有拒收必有痕迹，缺一作废。</p>
          <table>
            <thead>
              <tr><th>痕迹ID</th><th>拒收单ID</th><th>阵列/组串</th><th>钟点窗</th><th>覆盖下限</th><th>挡回人</th><th>后台记下时刻</th></tr>
            </thead>
            <tbody>
              <tr v-for="b in blocks" :key="b.id" class="rejected">
                <td>{{ b.id }}</td>
                <td>{{ b.scan_id }}</td>
                <td>{{ b.array_code }} / {{ b.string_code }}</td>
                <td>{{ String(b.start_hour).padStart(2,'0') }}-{{ String(b.end_hour).padStart(2,'0') }}</td>
                <td>{{ b.coverage_min }}%</td>
                <td>{{ b.blocked_by }}</td>
                <td>{{ b.blocked_at }}</td>
              </tr>
              <tr v-if="blocks.length === 0"><td colspan="7" class="empty">尚无封锁痕迹</td></tr>
            </tbody>
          </table>
        </section>
      </div>
    </main>

    <footer v-if="session" class="logoutbar">
      <button class="secondary" @click="refreshAll">刷新</button>
      <button class="secondary" @click="logout">退出</button>
    </footer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
const session = ref(null);
const view = ref("scan");
const logs = ref([]);
const blocks = ref([]);
const snow = reactive({ windows: [], server_time: "", server_hour: null });
const editHour = reactive({});
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const form = reactive({ array_code: "", coverage_min: 80, start_hour: 0, end_hour: 6 });
const error = ref("");
const snowError = ref("");
const loading = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function statusText(s) {
  return s === "pending" ? "待处理" : s === "rejected" ? "已拒收" : "已完成";
}
function statusClass(s) {
  return s === "pending" ? "pending" : s === "rejected" ? "bad" : "ok";
}

async function refreshLogs() {
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
}
async function refreshSnow() {
  const res = await fetch("/api/snow/windows", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (!res.ok) return;
  const data = await res.json();
  snow.server_time = data.server_time;
  snow.server_hour = data.server_hour;
  snow.windows = data.windows;
  for (const w of data.windows) {
    if (!editHour[w.id]) editHour[w.id] = { start: w.start_hour, end: w.end_hour };
  }
  const bres = await fetch("/api/snow/blocks", { headers: headers() });
  if (bres.ok) blocks.value = await bres.json();
}
function refreshAll() {
  if (!session.value) return;
  refreshLogs();
  if (view.value === "snow") refreshSnow();
}
function goSnow() {
  view.value = "snow";
  refreshSnow();
}

async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refreshAll();
    timer = setInterval(refreshAll, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  blocks.value = [];
  localStorage.removeItem("pv_session");
}
async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      error.value = data.detail || "提交失败";
      await refreshLogs();
      if (res.status === 409 && view.value === "scan") {
        // 409 时已有拒收单与封锁痕迹同批记下，刷新积雪页可见痕迹。
      }
      return;
    }
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refreshLogs();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
async function saveWindow() {
  snowError.value = "";
  if (!form.array_code.trim()) { snowError.value = "阵列不能为空"; return; }
  loading.value = true;
  try {
    const res = await fetch("/api/snow/windows", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ ...form, array_code: form.array_code.trim() }),
    });
    const data = await res.json();
    if (!res.ok) { snowError.value = data.detail || "配置失败"; return; }
    form.array_code = "";
    await refreshSnow();
  } catch { snowError.value = "配置时网络异常"; }
  finally { loading.value = false; }
}
async function moveWindow(w) {
  snowError.value = "";
  const h = editHour[w.id];
  const res = await fetch(`/api/snow/windows/${w.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...headers() },
    body: JSON.stringify({ start_hour: h.start, end_hour: h.end }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    snowError.value = data.detail || "挪窗失败";
    return;
  }
  await refreshSnow();
}
async function removeWindow(w) {
  snowError.value = "";
  const res = await fetch(`/api/snow/windows/${w.id}`, { method: "DELETE", headers: headers() });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    snowError.value = data.detail || "删除失败";
    return;
  }
  await refreshSnow();
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refreshAll();
      timer = setInterval(refreshAll, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>

<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
.topbar { display: flex; align-items: center; gap: 1.5rem; background: #022c22; border-bottom: 2px solid #166534; padding: 0.75rem 1.5rem; }
.brand { font-weight: 700; color: #86efac; font-size: 1.05rem; }
.topbar nav button { background: transparent; color: #a7f3d0; border: 1px solid transparent; }
.topbar nav button.active { background: #14532d; border-color: #4ade80; color: #ecfdf5; }
main { max-width: 1040px; margin: 0 auto; padding: 1.25rem 1.5rem 4rem; }
.logoutbar { position: fixed; right: 1rem; bottom: 1rem; }
.sub { color: #a7f3d0; margin: 0.5rem 0 1rem; }
h3 { margin: 0 0 0.75rem; color: #86efac; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.clockcard { background: #083344; border-color: #155e75; }
.clock { font-variant-numeric: tabular-nums; color: #bae6fd; }
.hint { color: #99f6e4; font-size: 0.82rem; margin: 0.5rem 0 0; }
.formgrid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.75rem; margin-bottom: 0.5rem; }
@media (max-width: 820px) { .formgrid { grid-template-columns: 1fr 1fr; } }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input, select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.25rem; }
.hsel { width: auto; padding: 0.25rem 0.4rem; display: inline-block; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
button.mini { padding: 0.25rem 0.6rem; font-size: 0.8rem; font-weight: 500; }
button.danger { background: #991b1b; }
.err { color: #fecaca; }
.reason { color: #fecaca; font-size: 0.85rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; vertical-align: top; }
.empty { color: #a7f3d0; text-align: center; padding: 0.9rem; }
tr.rejected td { background: rgba(127, 29, 29, 0.22); }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; white-space: nowrap; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.block { background: #1e3a8a; color: #bfdbfe; }
</style>
