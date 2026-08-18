<script setup lang="ts">
interface PositionItem {
  code: string;
  position: number;
  close: number;
  reversal: boolean;
  trade_date: string;
}

interface BoardItem {
  position_risk: number;
  position_bottom: number;
  reversal: boolean;
  codes: string[];
}

interface PositionsPayload {
  available: boolean;
  as_of?: string | null;
  regime: string;
  market: PositionItem[];
  boards: Record<string, BoardItem>;
  industries: PositionItem[];
}

defineProps<{
  positions: PositionsPayload | null;
  loading: boolean;
  error: string;
}>();

const emit = defineEmits<{ refresh: [] }>();

function positionClass(value: number): string {
  return value >= 85 ? "pos-high" : value <= 15 ? "pos-low" : "pos-mid";
}
</script>

<template>
  <section class="panel positions-panel">
    <div class="panel-title positions-heading">
      <div>
        <h2>市场位置与状态</h2>
        <p>{{ positions?.as_of ?? "最新交易日" }} 路 252 日分位</p>
      </div>
      <button type="button" class="icon-button" :disabled="loading" aria-label="刷新市场位置" @click="emit('refresh')">↻</button>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="loading && !positions" class="empty-cell">正在计算市场/板块/行业位置...</p>
    <template v-else-if="positions?.available">
      <div class="regime-line">
        <b :class="['regime-badge', positions.regime]">
          {{ positions.regime === "risk" ? "风险区" : positions.regime === "bottom" ? "抄底区" : "中性" }}
        </b>
        <span>高位 ≥85 提示风险；低位 ≤15 且反转确认提示抄底</span>
      </div>
      <div class="index-grid">
        <article v-for="item in positions.market" :key="item.code">
          <div><span>{{ item.code }}</span><b :class="positionClass(item.position)">{{ item.position.toFixed(1) }}</b></div>
          <small>{{ item.reversal ? "反转确认" : "无反转" }} 路 {{ item.trade_date }}</small>
        </article>
      </div>
      <div v-if="Object.keys(positions.boards).length" class="board-line">
        <span v-for="(board, name) in positions.boards" :key="name" class="board-pill">
          {{ name === "main" ? "主板" : name === "gem" ? "创业板" : name === "star" ? "科创板" : "北证" }}
          {{ board.position_risk.toFixed(0) }}
        </span>
      </div>
      <details class="positions-method">
        <summary>行业位置（{{ positions.industries.length }} 个申万 L1）</summary>
        <div class="industry-list">
          <span v-for="item in positions.industries" :key="item.code" :class="positionClass(item.position)">
            {{ item.code }} {{ item.position.toFixed(1) }}{{ item.reversal ? " 反转" : "" }}
          </span>
        </div>
      </details>
    </template>
    <p v-else class="empty-cell">暂无指数数据，请先运行指数抓取。</p>
  </section>
</template>

<style scoped>
.positions-panel {
  padding: 16px;
}

.positions-heading {
  align-items: flex-start;
  margin-bottom: 13px;
}

.positions-heading p {
  margin: 4px 0 0;
  color: var(--wb-muted);
  font-family: "IBM Plex Mono", monospace;
  font-size: 11px;
}

.icon-button {
  display: grid;
  place-items: center;
  width: 34px;
  min-height: 34px;
  padding: 0;
  border: 1px solid var(--wb-line);
  border-radius: var(--wb-radius-sm);
  background: var(--wb-surface);
  color: var(--wb-primary);
  box-shadow: none;
}

.icon-button:hover:not(:disabled) {
  border-color: var(--wb-line-strong);
  background: var(--wb-primary-soft);
  box-shadow: none;
}

.regime-line {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 10px;
}

.regime-line span {
  color: var(--wb-muted);
  font-size: 11px;
}

.regime-badge {
  padding: 3px 9px;
  border-radius: 999px;
  color: #fff;
  font-size: 12px;
}

.regime-badge.risk {
  background: #c43d3d;
}

.regime-badge.bottom {
  background: #087f78;
}

.regime-badge.neutral {
  background: #8aa1a5;
}

.index-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(118px, 1fr));
  gap: 7px;
}

.index-grid article {
  padding: 8px;
  border: 1px solid var(--wb-line);
  border-radius: var(--wb-radius-sm);
}

.index-grid article div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.index-grid article span {
  color: var(--wb-muted);
  font-family: "IBM Plex Mono", monospace;
  font-size: 11px;
}

.index-grid article small {
  color: var(--wb-muted);
  font-size: 10px;
}

.pos-high {
  color: #c43d3d;
  font-weight: 800;
}

.pos-low {
  color: #087f78;
  font-weight: 800;
}

.pos-mid {
  color: #405e63;
  font-weight: 700;
}

.board-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 9px;
}

.board-pill {
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--wb-primary-soft);
  color: #075e59;
  font-size: 11px;
  font-weight: 700;
}

.positions-method {
  margin-top: 10px;
  color: var(--wb-muted);
  font-size: 11px;
}

.positions-method summary {
  cursor: pointer;
  font-weight: 700;
}

.industry-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 9px;
  margin-top: 7px;
  max-height: 132px;
  overflow: auto;
}
</style>
