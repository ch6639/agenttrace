<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer])
const props = defineProps({ failures: { type: Object, required: true } })
const element = ref(null)
const label = computed(() => '失败类型分布：' + Object.entries(props.failures)
  .map(([name, count]) => `${name} ${count} 次`).join('，'))
let chart
function draw() {
  if (!element.value) return
  chart ??= echarts.init(element.value)
  chart.setOption({
    color: ['#c75a50', '#dfa25b', '#8d7ba2', '#579899'],
    tooltip: { trigger: 'item' }, legend: { bottom: 0 },
    series: [{ type: 'pie', radius: ['48%', '72%'], center: ['50%', '44%'],
      label: { formatter: '{b}: {c}' },
      data: Object.entries(props.failures).map(([name, value]) => ({ name, value })) }],
  }, { notMerge: true })
}
function resize() { chart?.resize() }
watch(() => props.failures, draw, { deep: true })
onMounted(() => { draw(); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { chart?.dispose(); window.removeEventListener('resize', resize) })
</script>

<template><div ref="element" class="failure-chart" role="img" :aria-label="label"></div></template>
