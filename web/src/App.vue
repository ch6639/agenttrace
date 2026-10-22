<script setup>
import { computed, defineAsyncComponent, h, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

const FailureChart = defineAsyncComponent({
  loader: () => import('./FailureChart.vue'),
  errorComponent: { render: () => h('p', { role: 'alert' }, '图表加载失败，请刷新页面重试。') },
})
const route = ref(window.location.pathname)
const filters = ref({ status: '', range: [], order: 'start_time', dir: 'desc' })
const page = ref(1)
const pageSize = 20
const list = ref({ total: 0, items: [] })
const stats = ref(null)
const detail = ref(null)
const selected = ref(null)
const loading = ref(false)
const loadTimeMs = ref(null)
const viewReadyMs = ref(null)
const error = ref('')
let controller
const isStats = computed(() => route.value === '/stats')
const isDetail = computed(() => route.value.startsWith('/traces/'))
const title = computed(() => isDetail.value ? '运行时间线' : isStats.value ? '统计总览' : '运行记录')
const statusText = { running: '进行中', success: '成功', failed: '失败' }
const statusType = { running: 'warning', success: 'success', failed: 'danger' }
const number = value => new Intl.NumberFormat('zh-CN').format(value || 0)
const date = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const pretty = value => value == null ? '未捕获' : JSON.stringify(value, null, 2)
const windowParams = () => {
  const params = new URLSearchParams()
  if (filters.value.range?.length === 2) {
    params.set('t_from', new Date(filters.value.range[0]).toISOString())
    params.set('t_to', new Date(filters.value.range[1]).toISOString())
  }
  return params
}
const rows = computed(() => {
  if (!detail.value) return []
  const byId = new Map(detail.value.spans.map(span => [span.id, span]))
  const start = Date.parse(detail.value.trace.start_time)
  const duration = Math.max(1, detail.value.trace.latency_ms || 0,
    ...detail.value.spans.map(s => (Date.parse(s.end_time || s.start_time) - start)))
  return detail.value.spans.map(span => {
    let depth = 0, parent = span.parent_id
    const seen = new Set([span.id])
    while (parent && byId.has(parent) && !seen.has(parent)) {
      seen.add(parent); depth++; parent = byId.get(parent).parent_id
    }
    return { ...span, depth,
      left: Math.max(0, (Date.parse(span.start_time) - start) / duration * 100),
      width: Math.max(0.8, (span.duration_ms || 0) / duration * 100),
    }
  })
})
const suspected = span => span.status === 'running' &&
  Date.now() - Date.parse(span.start_time) > 60_000
const navigate = path => {
  if (route.value === path) return
  window.history.pushState({}, '', path)
  route.value = path
}
const popstate = () => { route.value = window.location.pathname }
async function get(url, signal) {
  const response = await fetch(url, { signal })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `请求失败（${response.status}）`)
  }
  return response.json()
}
async function load() {
  const started = performance.now()
  controller?.abort()
  const active = new AbortController()
  controller = active
  loading.value = true
  loadTimeMs.value = null
  viewReadyMs.value = null
  error.value = ''
  detail.value = null
  selected.value = null
  try {
    if (isDetail.value) {
      const data = await get(`/api/traces/${encodeURIComponent(route.value.split('/')[2])}`, active.signal)
      if (active.signal.aborted) return
      detail.value = data
      selected.value = data.spans.find(s => s.status === 'failed') || data.spans[0] || null
    } else if (isStats.value) {
      const data = await get(`/api/stats?${windowParams()}`, active.signal)
      if (active.signal.aborted) return
      stats.value = data
    } else {
      const params = windowParams()
      if (filters.value.status) params.set('status', filters.value.status)
      params.set('order', filters.value.order)
      params.set('dir', filters.value.dir)
      params.set('limit', pageSize)
      params.set('offset', (page.value - 1) * pageSize)
      const data = await get(`/api/traces?${params}`, active.signal)
      if (active.signal.aborted) return
      list.value = data
    }
  } catch (cause) {
    if (cause.name !== 'AbortError') error.value = cause.message
  } finally {
    if (!active.signal.aborted) {
      loading.value = false
      await nextTick()
      if (!active.signal.aborted) {
        loadTimeMs.value = Math.round(performance.now() - started)
        viewReadyMs.value = Math.round(performance.now())
      }
    }
  }
}
function applyFilters() { page.value = 1; load() }
watch(route, load)
onMounted(() => {
  window.addEventListener('popstate', popstate)
  load()
})
onBeforeUnmount(() => {
  controller?.abort()
  window.removeEventListener('popstate', popstate)
})
</script>

<template>
  <el-config-provider :locale="zhCn">
  <div class="shell">
    <aside class="sidebar">
      <a class="brand" href="/" @click.prevent="navigate('/')"><span class="brand-icon">a<span>t</span></span>AgentTrace</a>
      <div class="workspace-label">开发者工作台</div>
      <nav aria-label="主导航">
        <a href="/" :class="{ active: !isStats }" @click.prevent="navigate('/')"><span>◎</span> 运行记录</a>
        <a href="/stats" :class="{ active: isStats }" @click.prevent="navigate('/stats')"><span>▥</span> 统计总览</a>
      </nav>
      <div class="local-note"><i></i> 本地工作空间<small>SQLite · 数据保存在本机</small></div>
    </aside>
    <main :data-last-load-ms="loadTimeMs" :data-view-ready-ms="viewReadyMs">
      <header><div><div class="eyebrow">AGENT OBSERVABILITY</div><h1>{{ title }}</h1>
        <p>{{ isDetail ? '按时间检查每一步调用、输入输出与异常。' : isStats ? '从历史运行中了解成本与执行质量。' : '让 Agent 的每一步运行都有迹可循。' }}</p></div>
        <el-button @click="load" :loading="loading">刷新记录</el-button>
      </header>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="error-alert" />

      <template v-if="!isDetail">
        <section class="filter-panel">
          <el-select v-if="!isStats" v-model="filters.status" placeholder="全部状态" clearable aria-label="状态过滤" style="width:140px">
            <el-option v-for="(label, value) in statusText" :key="value" :label="label" :value="value" />
          </el-select>
          <el-date-picker v-model="filters.range" type="datetimerange" start-placeholder="开始时间" end-placeholder="结束时间" range-separator="至" aria-label="时间范围" />
          <el-select v-if="!isStats" v-model="filters.order" aria-label="排序字段" style="width:140px">
            <el-option label="按开始时间" value="start_time" /><el-option label="按耗时" value="latency" /><el-option label="按 Token" value="tokens" />
          </el-select>
          <el-select v-if="!isStats" v-model="filters.dir" aria-label="排序方向" style="width:100px">
            <el-option label="降序" value="desc" /><el-option label="升序" value="asc" />
          </el-select>
          <el-button type="primary" @click="applyFilters">应用</el-button>
        </section>
      </template>

      <section v-if="!isDetail && !isStats" class="panel" v-loading="loading">
        <div class="panel-heading"><h2>Trace 列表 <span class="count">{{ number(list.total) }}</span></h2><span>每条记录对应一次任务运行</span></div>
        <el-table :data="list.items" @row-click="row => navigate('/traces/' + row.id)" row-class-name="clickable-row">
          <el-table-column label="任务名称" min-width="210"><template #default="{ row }"><a :href="'/traces/' + row.id" @click.stop.prevent="navigate('/traces/' + row.id)" class="trace-link">{{ row.name }}</a><small class="trace-id">{{ row.id.slice(0, 8) }}</small></template></el-table-column>
          <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="statusType[row.status]" effect="light">{{ statusText[row.status] }}</el-tag></template></el-table-column>
          <el-table-column label="开始时间" min-width="180"><template #default="{ row }">{{ date(row.start_time) }}</template></el-table-column>
          <el-table-column prop="span_count" label="事件数" width="90" align="right" />
          <el-table-column label="Token" width="110" align="right"><template #default="{ row }">{{ number(row.total_tokens) }}</template></el-table-column>
          <el-table-column label="耗时" width="110" align="right"><template #default="{ row }">{{ row.latency_ms == null ? '—' : number(row.latency_ms) + ' ms' }}</template></el-table-column>
          <template #empty><el-empty description="还没有运行记录"><p>先运行离线示例，或生成合成数据：</p><code>agenttrace seed --n 1000</code></el-empty></template>
        </el-table>
        <div class="pagination"><el-pagination v-model:current-page="page" :page-size="pageSize" :total="list.total" layout="total, prev, pager, next" @current-change="load" /></div>
      </section>

      <template v-if="isDetail && detail && !error">
        <a class="back-link" href="/" @click.prevent="navigate('/')">← 返回运行记录</a>
        <section class="trace-summary panel"><div><h2>{{ detail.trace.name }}</h2><small>{{ detail.trace.id }}</small></div>
          <el-tag :type="statusType[detail.trace.status]">{{ statusText[detail.trace.status] }}</el-tag>
          <div>{{ number(detail.trace.total_tokens) }} <small>Token</small></div><div>{{ detail.trace.latency_ms ?? '—' }} <small>ms</small></div>
        </section>
        <el-alert v-if="detail.trace.error" :title="detail.trace.error" type="error" :closable="false" show-icon />
        <section class="panel timeline"><div class="panel-heading"><h2>调用瀑布图</h2><span>{{ date(detail.trace.start_time) }} · {{ rows.length }} 个事件</span></div>
          <div class="timeline-axis"><span>事件 / 调用层级</span><div><span>任务开始</span><span>任务结束</span></div></div>
          <button v-for="span in rows" :key="span.id" class="timeline-row" :class="{ selected: selected?.id === span.id, failed: span.status === 'failed' }" @click="selected = span">
            <div class="span-label" :style="{ paddingLeft: (span.depth * 18 + 12) + 'px' }"><span class="kind" :class="span.type">{{ span.type }}</span><span>{{ span.name }}</span></div>
            <div class="track"><span class="waterfall" :class="span.status" :style="{ left: Math.min(span.left, 99) + '%', width: Math.min(span.width, 100 - span.left) + '%' }"></span></div>
            <span class="span-time">{{ span.duration_ms == null ? '瞬时' : span.duration_ms + ' ms' }}</span>
          </button>
        </section>
        <section v-if="selected" class="panel event-detail"><div class="panel-heading"><h2>{{ selected.name }}</h2><div><el-tag :type="statusType[selected.status]">{{ statusText[selected.status] }}</el-tag><el-tag v-if="selected.is_stream" class="stream-tag">流式聚合</el-tag></div></div>
          <div class="event-meta"><span>模型：{{ selected.model || '—' }}</span><span>输入 Token：{{ selected.token_in }}</span><span>输出 Token：{{ selected.token_out }}</span><span>耗时：{{ selected.duration_ms ?? '—' }} ms</span></div>
          <el-alert v-if="suspected(selected)" title="长时间未完成：可能仍在执行或已中断，请结合 Agent 进程状态判断。" type="warning" :closable="false" />
          <el-alert v-if="selected.status_message" :title="selected.status_message" :type="selected.status === 'failed' ? 'error' : 'info'" :closable="false" />
          <div class="io-grid"><div><h3>输入</h3><pre>{{ pretty(selected.input) }}</pre></div><div><h3>输出</h3><pre>{{ pretty(selected.output) }}</pre></div></div>
          <small>parent_id：{{ selected.parent_id || '根事件' }} · 超过 64 KiB 的字段显示 __truncated__ 标记。</small>
        </section>
      </template>
      <div v-else-if="isDetail && loading" class="loading-detail" v-loading="true"></div>

      <template v-if="isStats && stats && !error">
        <div class="metric-grid" v-loading="loading">
          <div class="metric"><span>运行任务总数</span><strong>{{ number(stats.total_traces) }}</strong><small>时间范围内全部任务</small></div>
          <div class="metric"><span>Token 消耗</span><strong>{{ number(stats.total_tokens) }}</strong><small>按任务聚合的调用用量</small></div>
          <div class="metric"><span>任务成功率</span><strong>{{ (stats.success_rate * 100).toFixed(1) }}<em>%</em></strong><small>成功 / 已结束任务</small></div>
          <div class="metric"><span>平均事件数</span><strong>{{ stats.avg_steps.toFixed(1) }}</strong><small>包含任务根事件与调用步骤</small></div>
        </div>
        <section class="panel stats-panel"><div class="panel-heading"><h2>失败类型分布</h2><span>按失败任务的异常类型计数</span></div>
          <el-empty v-if="!stats.total_traces" description="还没有统计数据，先运行示例 Agent" />
          <el-empty v-else-if="!Object.keys(stats.failure_types).length" description="当前时间范围内没有失败任务" />
          <FailureChart v-else :failures="stats.failure_types" />
        </section>
      </template>
      <footer>AgentTrace · 单机运行观测 <span>输入输出已做基础脱敏；敏感业务数据请关闭捕获。</span></footer>
    </main>
  </div>
  </el-config-provider>
</template>
