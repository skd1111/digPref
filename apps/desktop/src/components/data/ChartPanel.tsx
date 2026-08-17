/**
 * ChartPanel —— 数据专家右栏（中）：ECharts 可视化图表 + 图表类型切换。
 *
 * V1 实现：集成 echarts + echarts-for-react，AI 按数据特征自动推荐图表类型
 * （design §4.3，走本地 chart_reco 任务，_LOCAL_ONLY_TASKS 强制本地）。
 * 支持：柱状/折线/饼/散点 + 数据缩放 + tooltip + 暗色主题。
 * 缩放（BUGFIX #105）：zoomed=true 时轴/图例/标签字号放大（放大视图用）。
 */
import { useMemo } from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts';
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  TitleComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { useDataStore, cellFor, type ChartType, type QueryResult } from '@/store/dataStore';
import { PanelHeader } from './DataGrid';

// 注册 ECharts 组件（按需引入，减小 bundle）
echarts.use([
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  TitleComponent,
  CanvasRenderer,
]);

const CHART_TABS: Array<{ id: ChartType; label: string }> = [
  { id: 'bar', label: '柱状图' },
  { id: 'line', label: '折线图' },
  { id: 'pie', label: '饼图' },
  { id: 'scatter', label: '散点图' },
];

const DARK_THEME = {
  backgroundColor: 'transparent',
  textStyle: { color: '#333333' },
};

export function ChartPanel({
  zoomed = false,
  onZoom,
}: {
  zoomed?: boolean;
  onZoom?: () => void;
}): JSX.Element {
  const result = useDataStore((s) => s.result);
  const chartType = useDataStore((s) => s.chartType);
  const setChartType = useDataStore((s) => s.setChartType);
  const recommended = result?.recommendedChart;

  const option = useMemo(() => {
    if (!result || result.rowCount === 0) return null;
    return buildOption(chartType, result, result.chartXIndex, result.chartYIndex, zoomed);
  }, [result, chartType, zoomed]);

  return (
    <div className="flex h-full flex-col overflow-hidden" style={{ backgroundColor: '#ffffff' }}>
      <PanelHeader
        title="📈 可视化图表"
        right={
          <div className="flex items-center gap-1">
            {CHART_TABS.map((c) => {
              const active = c.id === chartType;
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => setChartType(c.id)}
                  className="rounded px-2 py-0.5 text-2xs transition-all"
                  style={{
                    color: active ? '#1e1e1e' : '#6e6e6e',
                    backgroundColor: active ? '#059669' : 'transparent',
                    border: c.id === recommended && !active ? '1px solid #4ec9b0' : '1px solid transparent',
                  }}
                  title={c.id === recommended ? 'AI 推荐' : undefined}
                >
                  {c.label}
                  {c.id === recommended ? ' ★' : ''}
                </button>
              );
            })}
            {onZoom ? (
              <button
                type="button"
                onClick={onZoom}
                className="rounded px-1.5 text-2xs transition-all hover:brightness-95"
                style={{ backgroundColor: '#ececec', color: '#333333' }}
                title={zoomed ? '退出放大' : '放大查看'}
              >
                ⛶
              </button>
            ) : null}
          </div>
        }
      />
      <div className="flex-1 overflow-hidden p-2">
        {!option ? (
          <div className="flex h-full items-center justify-center text-ui" style={{ color: '#616161' }}>
            执行查询后自动推荐图表
          </div>
        ) : (
          <ReactEChartsCore
            echarts={echarts}
            option={option}
            theme={DARK_THEME}
            style={{ width: '100%', height: '100%' }}
            notMerge
            lazyUpdate
          />
        )}
      </div>
    </div>
  );
}

// ---- ECharts option 构建 ---------------------------------------------------

function buildOption(
  type: ChartType,
  result: QueryResult,
  xIdx: number,
  yIdx: number,
  zoomed: boolean,
): Record<string, unknown> {
  const { columns, rowCount } = result;
  // 放大视图：轴标签/图例/数据标签字号整体提升（看得清）
  const fAxis = zoomed ? 14 : 10;
  const fLabel = zoomed ? 14 : 11;
  // 图表最多取前 5000 行（可视化不需要全量，防卡顿）
  const n = Math.min(rowCount, 5000);
  const xData: string[] = [];
  const yData: number[] = [];
  for (let i = 0; i < n; i += 1) {
    xData.push(String(cellFor(result, i, xIdx) ?? ''));
    yData.push(Number(cellFor(result, i, yIdx)) || 0);
  }
  const yName = columns[yIdx] || '';
  const xName = columns[xIdx] || '';

  const base = {
    tooltip: { trigger: type === 'pie' ? 'item' : 'axis' },
    grid: { left: 50, right: 20, top: 30, bottom: type === 'scatter' ? 50 : 40 },
  };

  if (type === 'pie') {
    return {
      ...base,
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: {
        orient: 'vertical' as const,
        right: 10,
        top: 'center',
        textStyle: { color: '#333333', fontSize: fLabel },
      },
      series: [
        {
          type: 'pie',
          radius: ['30%', '65%'],
          center: ['40%', '50%'],
          data: xData.map((label, i) => ({ name: label, value: yData[i] })),
          emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.5)' } },
          label: { color: '#333333', fontSize: fLabel },
        },
      ],
    };
  }

  if (type === 'scatter') {
    const scatterData = xData.map((x, i) => [Number(x) || 0, yData[i]]);
    return {
      ...base,
      xAxis: { type: 'value', name: xName, nameTextStyle: { color: '#616161', fontSize: fLabel }, axisLabel: { color: '#6e6e6e', fontSize: fAxis }, splitLine: { lineStyle: { color: '#2a2a2a' } } },
      yAxis: { type: 'value', name: yName, nameTextStyle: { color: '#616161', fontSize: fLabel }, axisLabel: { color: '#6e6e6e', fontSize: fAxis }, splitLine: { lineStyle: { color: '#2a2a2a' } } },
      series: [{ type: 'scatter', data: scatterData, symbolSize: 8, itemStyle: { color: '#059669' } }],
    };
  }

  // bar / line 共享 xAxis + dataZoom
  const seriesType = type === 'bar' ? 'bar' : 'line';
  return {
    ...base,
    xAxis: {
      type: 'category',
      data: xData,
      axisLabel: { color: '#6e6e6e', fontSize: fAxis, rotate: xData.length > 12 ? 30 : 0 },
      axisLine: { lineStyle: { color: '#1f1f1f' } },
    },
    yAxis: {
      type: 'value',
      name: yName,
      nameTextStyle: { color: '#616161', fontSize: fLabel },
      axisLabel: { color: '#6e6e6e', fontSize: fAxis },
      splitLine: { lineStyle: { color: '#2a2a2a' } },
    },
    dataZoom: xData.length > 30 ? [{ type: 'inside', start: 0, end: 30 }] : undefined,
    series: [
      {
        type: seriesType,
        data: yData,
        itemStyle: { color: '#059669' },
        ...(type === 'line' ? { smooth: true, areaStyle: { opacity: 0.15 } } : {}),
        ...(type === 'bar' ? { barMaxWidth: 40 } : {}),
      },
    ],
  };
}
