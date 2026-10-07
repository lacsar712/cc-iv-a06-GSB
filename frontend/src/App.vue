<template>
  <main>
    <template v-if="!session">
      <h1>光伏组串IV扫描台</h1>
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </template>
    <template v-else>
      <header class="topbar">
        <div class="brand">光伏组串IV扫描台</div>
        <nav>
          <button :class="{ active: tab === 'scan' }" @click="tab = 'scan'">扫描台</button>
          <button :class="{ active: tab === 'snow' }" @click="tab = 'snow'">积雪窗</button>
        </nav>
        <div class="who">
          {{ session.username }}（{{ isWriter ? "可提交" : "观察员只读" }}）
          <button class="secondary" @click="logout">退出</button>
        </div>
      </header>

      <!-- ============ 扫描台 ============ -->
      <div v-show="tab === 'scan'">
        <section>
          <button class="secondary" @click="refreshAll">刷新列表</button>
        </section>
        <section v-if="isWriter">
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err" :class="{ blocked: wasBlocked }">{{ error }}</p>
        </section>
        <section>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <!-- ============ 积雪窗 ============ -->
      <div v-show="tab === 'snow'">
        <section class="clockcard">
          <div>
            后台当前钟点：<strong>{{ clock.clock || '—' }}</strong>
            <span class="tz">（{{ clock.day }} · 数据库时区 {{ clock.tz }}）</span>
          </div>
          <div class="hint">是否处在覆盖期只按这个后台钟点对窗判定；浏览端本机钟不参与，改本机钟也绕不过封锁。</div>
        </section>

        <section v-if="isWriter">
          <h3 class="block-title">{{ snowEditId ? "修改积雪窗" : "新建积雪窗" }}</h3>
          <div class="formgrid">
            <div>
              <label>阵列</label>
              <input v-model="snow.array_code" list="array-options" placeholder="选择或输入阵列，如 阵列A" />
              <datalist id="array-options">
                <option v-for="a in arrayCandidates" :key="a" :value="a"></option>
              </datalist>
            </div>
            <div>
              <label>覆盖下限（0–1，如 0.80 = 80%）</label>
              <input type="number" min="0" max="1" step="0.01" v-model="snow.cover_min" />
            </div>
            <div>
              <label>起始钟点</label>
              <input type="time" v-model="snow.start_time" />
            </div>
            <div>
              <label>终止钟点（早于起始即跨午夜）</label>
              <input type="time" v-model="snow.end_time" />
            </div>
          </div>
          <button :disabled="loading" @click="saveWindow">
            {{ snowEditId ? "保存修改" : "配封锁窗" }}
          </button>
          <button v-if="snowEditId" class="secondary" @click="resetSnowForm">放弃修改</button>
          <p v-if="snowError" class="err">{{ snowError }}</p>
        </section>
        <section v-else>
          <p class="hint">观察员能看不能改：积雪窗与封锁痕迹只读，配置与编辑请用扫描员账号登录。</p>
        </section>

        <section>
          <h3 class="block-title">封锁窗（{{ windows.length }}）</h3>
          <table>
            <thead>
              <tr><th>阵列</th><th>覆盖下限</th><th>每日钟点</th><th>后台判定</th><th>配置人</th><th>更新时刻</th><th v-if="isWriter">操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="w in windows" :key="w.id">
                <td>{{ w.array_code }}</td>
                <td>{{ pct(w.cover_min) }}</td>
                <td>{{ w.start_time.slice(0,5) }}–{{ w.end_time.slice(0,5) }}<span v-if="w.start_time > w.end_time" class="tz">（跨午夜）</span></td>
                <td><span class="tag" :class="w.active_now ? 'blocktag' : 'ok'">{{ w.active_now ? "覆盖中" : "未覆盖" }}</span></td>
                <td>{{ w.created_by }}</td>
                <td>{{ fmt(w.updated_at) }}</td>
                <td v-if="isWriter">
                  <button class="secondary mini" @click="editWindow(w)">改</button>
                  <button class="danger mini" @click="removeWindow(w)">删</button>
                </td>
              </tr>
              <tr v-if="!windows.length"><td colspan="7" class="tz">还没有封锁窗</td></tr>
            </tbody>
          </table>
        </section>

        <section>
          <h3 class="block-title">封锁痕迹（{{ blocks.length }}）</h3>
          <table>
            <thead>
              <tr><th>时刻</th><th>阵列</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>拒收原因</th><th>操作人</th></tr>
            </thead>
            <tbody>
              <tr v-for="b in blocks" :key="b.id">
                <td>{{ fmt(b.blocked_at) }}</td>
                <td>{{ b.array_code }}</td>
                <td>{{ b.string_code }}</td>
                <td>{{ b.voc_v }}</td>
                <td>{{ b.isc_a }}</td>
                <td>{{ b.fill_factor }}</td>
                <td class="reason">{{ b.reason }}</td>
                <td>{{ b.blocked_by }}</td>
              </tr>
              <tr v-if="!blocks.length"><td colspan="8" class="tz">还没有封锁痕迹</td></tr>
            </tbody>
          </table>
        </section>
      </div>
    </template>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const tab = ref("scan");
const logs = ref([]);
const windows = ref([]);
const blocks = ref([]);
const clock = ref({});
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const error = ref("");
const wasBlocked = ref(false);
const snowError = ref("");
const loading = ref(false);
const snowEditId = ref(null);
const snow = ref({ array_code: "", cover_min: "", start_time: "", end_time: "" });
let timer;
const isWriter = computed(() => session.value?.role === "writer");
const arrayCandidates = computed(() => {
  const set = new Set(["阵列A", "阵列B", "阵列C", "阵列D", "阵列E"]);
  for (const w of windows.value) set.add(w.array_code);
  for (const r of logs.value) {
    const m = /^(.+?)-串/.exec(r.string_code || "");
    if (m) set.add(m[1]);
  }
  return [...set].sort();
});
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(ts) {
  return ts ? ts.replace("T", " ").slice(0, 19) : "—";
}
function pct(v) {
  return (Number(v) * 100).toFixed(0) + "%";
}
async function api(path, options = {}) {
  return fetch("/api" + path, { headers: { ...headers(), ...(options.headers || {}) }, ...options });
}
async function refreshLogs() {
  const res = await api("/logs");
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
}
async function refreshSnow() {
  const [w, b, c] = await Promise.all([api("/snow/windows"), api("/snow/blocks"), api("/snow/clock")]);
  if (w.ok) windows.value = await w.json();
  if (b.ok) blocks.value = await b.json();
  if (c.ok) clock.value = await c.json();
}
async function refreshAll() {
  if (!session.value) return;
  await refreshLogs();
  await refreshSnow();
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
  windows.value = [];
  blocks.value = [];
  localStorage.removeItem("pv_session");
}
async function submit() {
  error.value = "";
  wasBlocked.value = false;
  loading.value = true;
  try {
    const res = await api("/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      // 是否被积雪窗挡住由后台判定，前端不拿本机钟做任何预判。
      wasBlocked.value = res.status === 423;
      error.value = data.detail || "提交失败";
      return;
    }
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refreshLogs();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
function resetSnowForm() {
  snowEditId.value = null;
  snow.value = { array_code: "", cover_min: "", start_time: "", end_time: "" };
  snowError.value = "";
}
function editWindow(w) {
  snowEditId.value = w.id;
  snow.value = {
    array_code: w.array_code,
    cover_min: w.cover_min,
    start_time: w.start_time.slice(0, 5),
    end_time: w.end_time.slice(0, 5),
  };
  snowError.value = "";
}
async function saveWindow() {
  snowError.value = "";
  loading.value = true;
  try {
    const path = snowEditId.value ? `/snow/windows/${snowEditId.value}` : "/snow/windows";
    const res = await api(path, {
      method: snowEditId.value ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        array_code: snow.value.array_code,
        cover_min: Number(snow.value.cover_min),
        start_time: snow.value.start_time,
        end_time: snow.value.end_time,
      }),
    });
    const data = await res.json();
    if (!res.ok) { snowError.value = data.detail || "保存失败"; return; }
    resetSnowForm();
    await refreshSnow();
  } catch { snowError.value = "保存时网络异常"; }
  finally { loading.value = false; }
}
async function removeWindow(w) {
  if (!window.confirm(`确认删除阵列 ${w.array_code} 的积雪封锁窗？历史封锁痕迹仍保留。`)) return;
  loading.value = true;
  try {
    const res = await api(`/snow/windows/${w.id}`, { method: "DELETE" });
    if (!res.ok) { snowError.value = "删除失败"; return; }
    if (snowEditId.value === w.id) resetSnowForm();
    await refreshSnow();
  } catch { snowError.value = "删除时网络异常"; }
  finally { loading.value = false; }
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
main { max-width: 1040px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.25rem; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
.topbar { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; flex-wrap: wrap; }
.topbar .brand { color: #86efac; font-weight: 700; font-size: 1.1rem; }
.topbar nav { display: flex; gap: 0.4rem; }
.topbar nav button { background: #14532d; }
.topbar nav button.active { background: #16a34a; }
.topbar .who { margin-left: auto; color: #a7f3d0; font-size: 0.9rem; display: flex; align-items: center; gap: 0.5rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.clockcard { background: #052e16; border-color: #4ade80; }
.block-title { margin: 0 0 0.75rem; color: #bbf7d0; font-size: 1rem; }
.formgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
button.danger { background: #991b1b; }
button.mini { padding: 0.2rem 0.6rem; font-size: 0.8rem; margin: 0; }
.err { color: #fecaca; }
.err.blocked { background: #7f1d1d; border: 1px solid #ef4444; border-radius: 6px; padding: 0.6rem 0.8rem; }
.hint, .tz { color: #a7f3d0; font-size: 0.85rem; }
.reason { color: #fecaca; font-size: 0.85rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; vertical-align: top; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; white-space: nowrap; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.blocktag { background: #1e3a8a; color: #bfdbfe; }
</style>
