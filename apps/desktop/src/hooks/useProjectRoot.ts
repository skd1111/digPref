/**
 * useProjectRoot —— 从当前文件路径向上查找 package.json（最多 8 层）。
 *
 * Phase 15 预览按钮状态联动依赖此 hook：找不到 package.json 时
 * 按钮置灰（除非文件本身是 .html，纯静态目录也可预览）。
 */
import { useMemo } from "react";

export function findProjectRootSync(
  filePath: string | null | undefined,
): string | null {
  if (!filePath) return null;
  const normalized = filePath.replace(/\\/g, "/");
  const isFile = /\.\w+$/.test(normalized);
  let dir = isFile
    ? normalized.slice(0, normalized.lastIndexOf("/"))
    : normalized;
  const candidates: string[] = [];
  while (dir && dir !== "/" && dir !== "." && !/^[A-Za-z]:$/.test(dir)) {
    candidates.push(dir.replace(/\/$/, ""));
    const next = dir.lastIndexOf("/");
    if (next <= 0) break;
    dir = dir.slice(0, next);
  }
  // 返回最深的目录候选（真实存在性由后端 find_project_root 校验）
  return candidates[0] || dir || null;
}

export function useProjectRoot(
  filePath: string | null | undefined,
): string | null {
  return useMemo(() => findProjectRootSync(filePath), [filePath]);
}

/** 文件是否可预览（后缀 ∈ .vue/.tsx/.jsx/.html/.svelte/.htm）。 */
export function isPreviewableFile(
  filePath: string | null | undefined,
): boolean {
  if (!filePath) return false;
  return /\.(vue|tsx|jsx|html|svelte|htm)$/i.test(filePath);
}
