/**
 * mockFilter —— 识别并过滤 mock 占位数据。
 *
 * 后端 MockLLMClient 的所有用户可见输出统一以 `（mock）` 开头；
 * orchestrator 无 router 时的兜底以 `[mock:` 开头。前端在渲染层
 * （聊天消息 / 控制台条目 / 历史会话）按标记过滤，确保 UI 不显示任何 mock 数据。
 */

const MOCK_PATTERNS: RegExp[] = [
  /^（mock/,
  /^\[mock:/,
  /（mock 后端/,
  /（mock 模式/,
  /当前在 Mock 模式/,
];

export function isMockText(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  return MOCK_PATTERNS.some((re) => re.test(value));
}

export function isMockSource(value: unknown): boolean {
  return value === 'mock';
}
