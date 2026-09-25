<template>
  <section class="page" data-module="atp">
    <header class="page-head">
      <div>
        <h2>列车防护管理</h2>
        <p class="page-desc">维护防护设备，围绕设备编号、防护等级、覆盖区段、应答器数量做登记、筛选与状态流转；其他入口补录按设备编号自动归并，不重复计数。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openSupplement">其他入口补录</button>
        <button class="btn" type="button" @click="exportRows">导出列车防护清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statsCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div v-if="noBaliseDevices.length" class="notice-bar">
      <span>以下 {{ noBaliseDevices.length }} 台设备未计入应答器总数，请核对：</span>
      <ul>
        <li v-for="device in noBaliseDevices" :key="device['设备编号']">
          <strong>{{ device['设备编号'] }}</strong>：{{ device['原因'] }}
        </li>
      </ul>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>设备编号</span>
        <input v-model="keyword" placeholder="按设备编号检索，留空查全部" />
      </label>
      <label class="filter-item">
        <span>防护状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div v-if="listError" class="retry-bar">
      <span class="error-text">{{ listError }}</span>
      <button class="btn" type="button" :disabled="loading" @click="reload">重试</button>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>数据来源</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id ?? row['设备编号'])">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '应答器数量'">
              <span :class="{ 'warn-text': row['应答器数量异常'] }">{{ row['应答器数量提示'] ?? '—' }}</span>
            </template>
            <template v-else-if="column === '防护状态'">
              {{ row['防护状态'] || row.status || '—' }}
            </template>
            <template v-else-if="column === '备注'">
              <span :class="{ 'muted-text': row['备注缺失'] }">{{ row['备注提示'] ?? '—' }}</span>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td><span :class="{ 'muted-text': !row['主档已登记'] }">{{ row['数据来源'] ?? '主档' }}</span></td>
          <td class="row-actions">
            <template v-if="row['可操作']">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <span v-else class="muted-text">主档未登记，暂不可执行动作</span>
          </td>
        </tr>
        <tr v-if="!rows.length && !listError">
          <td :colspan="columns.length + 2" class="empty-state">暂无符合条件的防护设备，可调整查询条件或通过“其他入口补录”补充数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条列车防护记录</span>
      <span v-if="actionMessage" :class="actionOk ? '' : 'error-text'">{{ actionMessage }}</span>
    </footer>

    <div v-if="supplementOpen" class="modal-mask" @click.self="closeSupplement">
      <form class="modal-card" @submit.prevent="submitSupplement">
        <h3>其他入口补录</h3>
        <p class="page-desc">按设备编号归并到既有设备，不会重复计算应答器数量；主档尚无此编号时会单独标为“主档未登记”。</p>
        <label v-for="field in supplementFields" :key="field.key" class="modal-field">
          <span>{{ field.label }}<em v-if="field.required">*</em></span>
          <input v-model="supplementForm[field.key]" :placeholder="field.placeholder" />
        </label>
        <p v-if="supplementError" class="error-text">{{ supplementError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeSupplement">取消</button>
          <button class="btn primary" type="submit" :disabled="submitting">{{ submitting ? '提交中…' : '提交补录' }}</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
interface NoBaliseDevice { '设备编号': string; '原因': string }
interface Stats {
  在运防护设备: number
  待升级版本: number
  覆盖区段数: number
  应答器总数: number
  无应答器设备: NoBaliseDevice[]
}

const ENDPOINT = '/api/atp'
const columns = ['设备编号', '防护等级', '覆盖区段', '应答器数量', '所属线路', '版本号', '责任人', '防护状态', '备注']
const actions = ['启用防护', '提交升级', '停用防护']
const statuses = ['待启用', '防护正常', '版本待升级', '已停用']

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref<Stats>({
  在运防护设备: 0,
  待升级版本: 0,
  覆盖区段数: 0,
  应答器总数: 0,
  无应答器设备: [],
})
const keyword = ref('')
const statusFilter = ref('')
const loading = ref(false)
const listError = ref('')
const actionMessage = ref('')
const actionOk = ref(true)

const statsCards = computed(() => [
  { label: '在运防护设备', value: stats.value.在运防护设备 },
  { label: '待升级版本', value: stats.value.待升级版本 },
  { label: '覆盖区段数', value: stats.value.覆盖区段数 },
  { label: '应答器总数（按设备去重）', value: stats.value.应答器总数 },
])
const noBaliseDevices = computed(() => stats.value.无应答器设备 ?? [])

function buildQuery(): string {
  const params = new URLSearchParams()
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  if (statusFilter.value) params.set('status', statusFilter.value)
  const query = params.toString()
  return query ? `?${query}` : ''
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export${buildQuery()}`, '_blank')
}

async function reload() {
  loading.value = true
  listError.value = ''
  actionMessage.value = ''
  const query = buildQuery()
  // 列表与统计并行取数：任一失败都保留旧数据并给出重试入口，不抛白屏。
  const [listResult, statsResult] = await Promise.allSettled([
    request(`${ENDPOINT}${query}`).then(async (response) => {
      if (!response.ok) throw new Error(`防护设备列表读取失败（HTTP ${response.status}），请重试`)
      return (await response.json()) as { items?: Row[]; total?: number }
    }),
    request(`${ENDPOINT}/stats${query}`).then(async (response) => {
      if (!response.ok) throw new Error(`覆盖区段统计读取失败（HTTP ${response.status}），请重试`)
      return (await response.json()) as Stats
    }),
  ])
  loading.value = false

  const failures: string[] = []
  if (listResult.status === 'fulfilled') {
    rows.value = listResult.value.items ?? []
    total.value = listResult.value.total ?? rows.value.length
  } else {
    failures.push(listResult.reason instanceof Error ? listResult.reason.message : '列表取数失败')
  }
  if (statsResult.status === 'fulfilled') {
    stats.value = statsResult.value
  } else {
    failures.push(statsResult.reason instanceof Error ? statsResult.reason.message : '统计取数失败')
  }
  listError.value = failures.join('；')
}

async function runAction(action: string, row: Row) {
  actionMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string }
    actionOk.value = payload.ok
    actionMessage.value = payload.message || (payload.ok ? '动作已执行' : '动作未生效')
    if (payload.ok) await reload()
  } catch (error) {
    actionOk.value = false
    actionMessage.value = error instanceof Error ? error.message : '列车防护动作未送达，请重试'
  }
}

// ---- 其他入口补录 ----------------------------------------------------------

const supplementFields = [
  { key: '设备编号', label: '设备编号', required: true, placeholder: '如 ATP-0002' },
  { key: '覆盖区段', label: '覆盖区段', required: false, placeholder: '多个区段用顿号分隔' },
  { key: '应答器数量', label: '应答器数量', required: false, placeholder: '留空表示暂未提供' },
  { key: '备注', label: '备注', required: true, placeholder: '必填：说明补录来源与情况' },
]
const emptySupplementForm = (): Record<string, string> => ({ 设备编号: '', 覆盖区段: '', 应答器数量: '', 备注: '' })
const supplementOpen = ref(false)
const supplementForm = ref<Record<string, string>>(emptySupplementForm())
const supplementError = ref('')
const submitting = ref(false)

function openSupplement() {
  supplementForm.value = emptySupplementForm()
  supplementError.value = ''
  supplementOpen.value = true
}

function closeSupplement() {
  supplementOpen.value = false
}

async function submitSupplement() {
  supplementError.value = ''
  submitting.value = true
  try {
    const values: Record<string, string> = {
      设备编号: supplementForm.value.设备编号.trim(),
      覆盖区段: supplementForm.value.覆盖区段.trim(),
      备注: supplementForm.value.备注.trim(),
    }
    const countText = supplementForm.value.应答器数量.trim()
    if (countText) values.应答器数量 = countText
    const response = await request(`${ENDPOINT}/supplements`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!payload.ok) {
      // 不报错关闭，只提示需要补哪些字段
      supplementError.value = payload.message
      return
    }
    supplementOpen.value = false
    actionOk.value = true
    actionMessage.value = payload.message
    await reload()
  } catch (error) {
    supplementError.value = error instanceof Error ? `${error.message}，请重试` : '补录提交失败，请重试'
  } finally {
    submitting.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.notice-bar {
  background: #fff7ed;
  border: 1px solid #fdba74;
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 13px;
  color: #9a3412;
}
.notice-bar ul { margin: 4px 0 0; padding-left: 18px; }
.retry-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 13px;
}
.warn-text { color: #b42318; font-weight: 600; }
.muted-text { color: var(--muted); }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal-card {
  width: 420px;
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
}
.modal-card h3 { margin: 0 0 4px; }
.modal-field { display: block; margin-top: 10px; }
.modal-field span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.modal-field em { color: #b42318; font-style: normal; margin-left: 2px; }
.modal-field input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
</style>
