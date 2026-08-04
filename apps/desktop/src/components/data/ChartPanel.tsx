/**
 * ChartPanel —— 数据专家右栏（中）：ECharts 可视化图表 + 图表类型切换。
 *
 * V1 实现：集成 echarts + echarts-for-react，AI 按数据特征自动推荐图表类型
 * （design §4.3，走本地 chart_reco 任务，_LOCAL_ONLY_TASKS 强制本地）。
 * 支持：柱状/折线/饼/散点 + 数据缩放 + tooltip + 暗色主题。
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
import { useDataStore, type ChartType } from '@/store/dataStore';
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

export function ChartPanel(): JSX.Element {
  const result = useDataStore((s) => s.result);
  const chartType = useDataStore((s) => s.chartType);
  const setChartType = useDataStore((s) => s.setChartType);
  const recommended = result?.recommendedChart;

  const option = useMemo(() => {
    if (!result || result.rows.length === 0) return null;
    return buildOption(chartType, result.columns, result.rows, result.chartXIndex, result.chartYIndex);
  }, [result, chartType]);

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
  columns: string[],
  rows: Array<Array<string | number>>,
  xIdx: number,
  yIdx: number,
): Record<string, unknown> {
  const xData = rows.map((r) => String(r[xIdx] ?? ''));
  const yData = rows.map((r) => Number(r[yIdx]) || 0);
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
        textStyle: { color: '#333333', fontSize: 11 },
      },
      series: [
        {
          type: 'pie',
          radius: ['30%', '65%'],
          center: ['40%', '50%'],
          data: xData.map((label, i) => ({ name: label, value: yData[i] })),
          emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.5)' } },
          label: { color: '#333333', fontSize: 11 },
        },
      ],
    };
  }

  if (type === 'scatter') {
    const scatterData = rows.map((r) => [Number(r[xIdx]) || 0, Number(r[yIdx]) || 0]);
    return {
      ...base,
      xAxis: { type: 'value', name: xName, nameTextStyle: { color: '#616161' }, axisLabel: { color: '#6e6e6e' }, splitLine: { lineStyle: { color: '#2a2a2a' } } },
      yAxis: { type: 'value', name: yName, nameTextStyle: { color: '#616161' }, axisLabel: { color: '#6e6e6e' }, splitLine: { lineStyle: { color: '#2a2a2a' } } },
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
      axisLabel: { color: '#6e6e6e', fontSize: 10, rotate: xData.length > 12 ? 30 : 0 },
      axisLine: { lineStyle: { color: '#1f1f1f' } },
    },
    yAxis: {
      type: 'value',
      name: yName,
      nameTextStyle: { color: '#616161' },
      axisLabel: { color: '#6e6e6e' },
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
