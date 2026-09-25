<template>
  <section class="page" data-module="atp">
    <header class="page-head">
      <div>
        <h2>列车防护管理</h2>
        <p class="page-desc">维护防护设备，围绕设备编号、防护等级、覆盖区段、应答器数量做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记防护设备</button>
        <button class="btn" type="button" @click="exportRows">导出列车防护清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p v-if="noBaliseDevices.length" class="warn-bar">
      无应答器设备：
      <span v-for="item in noBaliseDevices" :key="item.设备编号" class="warn-item">
        {{ item.设备编号 }}（{{ item.原因 }}）
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="onSearch">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>
    <p v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <span
              v-if="column === '应答器数量' && row['无应答器原因']"
              class="tag warn"
              :title="String(row['无应答器原因'])"
            >无应答器</span>
            <span
              v-else-if="column === '备注' && !row[column]"
              class="hint-text"
            >待补：{{ missingFields(row) }}</span>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无列车防护数据，可先登记防护设备</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条列车防护记录</span>
      <span v-if="errorMessage" class="error-text">
        {{ errorMessage }}
        <button class="link" type="button" @click="reload">重试</button>
      </span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null | string[]>
type StatCard = { label: string; value: number }
type NoBaliseItem = { 设备编号: string; 原因: string }

const ENDPOINT = '/api/atp'
const columns = ["设备编号", "防护等级", "覆盖区段", "应答器数量", "所属线路", "版本号", "责任人", "防护状态", "备注"]
const actions = ["启用防护", "提交升级", "停用防护"]
const filterParamMap: Record<string, string> = { 设备编号: 'keyword', 防护等级: 'level', 覆盖区段: 'section' }

const stats = ref<StatCard[]>([
  { label: '在运防护设备', value: 0 },
  { label: '待升级版本', value: 0 },
  { label: '覆盖区段数', value: 0 },
  { label: '应答器总数', value: 0 },
])
const noBaliseDevices = ref<NoBaliseItem[]>([])
const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function missingFields(row: Row) {
  const fields = row['待补字段']
  return Array.isArray(fields) && fields.length ? fields.join('、') : '备注'
}

function buildQuery() {
  const params = new URLSearchParams()
  for (const [field, param] of Object.entries(filterParamMap)) {
    const value = (filters.value[field] ?? '').trim()
    if (value) {
      params.set(param, value)
    }
  }
  return params.toString()
}

function onSearch() {
  const hasCondition = filterFields.some((field) => (filters.value[field] ?? '').trim())
  noticeMessage.value = hasCondition
    ? ''
    : `查询条件为空，已显示全部记录；可按${filterFields.join('、')}补充条件`
  void reload()
}

function resetFilters() {
  filters.value = {}
  noticeMessage.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '防护设备登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('列车防护动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '列车防护操作失败'
  }
}

async function loadSummary() {
  const response = await request(`${ENDPOINT}/summary`)
  if (!response.ok) {
    throw new Error('列车防护统计读取失败')
  }
  const payload = await response.json()
  stats.value = [
    { label: '在运防护设备', value: payload['在运防护设备'] ?? 0 },
    { label: '待升级版本', value: payload['待升级版本'] ?? 0 },
    { label: '覆盖区段数', value: payload['覆盖区段数'] ?? 0 },
    { label: '应答器总数', value: payload['应答器总数'] ?? 0 },
  ]
  noBaliseDevices.value = payload['无应答器设备'] ?? []
}

async function reload() {
  errorMessage.value = ''
  try {
    const query = buildQuery()
    const response = await request(query ? `${ENDPOINT}?${query}` : ENDPOINT)
    if (!response.ok) {
      throw new Error('防护设备列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadSummary()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '列车防护列表读取失败'
  }
}

onMounted(reload)
</script>
