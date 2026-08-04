/**
 * SkillImportDialog —— 用户上传 JSON 文件导入 skill。
 * V0: 单文件 JSON 导入（V1 改为真实 YAML 解析）。
 */
import { useState } from 'react';
import { useSkillsStore } from '@/store/skillsStore';
import type { Skill } from '@/types/skill';

interface ValidationError {
  field: string;
  message: string;
}

export function SkillImportDialog(): JSX.Element | null {
  const open = useSkillsStore((s) => s.importDialogOpen);
  const close = useSkillsStore((s) => s.closeImportDialog);
  const importSkill = useSkillsStore((s) => s.importSkill);

  const [content, setContent] = useState('');
  const [errors, setErrors] = useState<ValidationError[]>([]);

  if (!open) return null;

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    setContent(text);
    setErrors([]);
  };

  const handleConfirm = (): void => {
    try {
      const data = JSON.parse(content);
      const errs: ValidationError[] = [];
      if (!data.schema_version) errs.push({ field: 'schema_version', message: '必填' });
      if (!data.id) errs.push({ field: 'id', message: '必填' });
      if (!data.name) errs.push({ field: 'name', message: '必填' });
      // DSN 简单检测
      const str = JSON.stringify(data);
      if (/jdbc:\/\/|mysql:\/\/|postgres(?:ql)?:\/\//i.test(str)) {
        errs.push({ field: '<global>', message: '包含 DSN 形态字符串' });
      }
      if (errs.length > 0) {
        setErrors(errs);
        return;
      }
      importSkill({
        ...(data as Skill),
        source_path: '',
        loaded_at: Date.now(),
        validation_errors: [],
      });
      setContent('');
      setErrors([]);
    } catch (e) {
      setErrors([{ field: '<parse>', message: String(e) }]);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        className="rounded p-4 shadow-2xl"
        style={{ backgroundColor: '#ffffff', minWidth: 480, maxWidth: 720 }}
      >
        <h3 className="mb-3 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
          📥 导入 Skill
        </h3>
        <p className="mb-3 text-2xs" style={{ color: '#616161' }}>
          选择 .json 文件（V0 单文件 JSON 导入；V1 接后端真实 YAML 解析）
        </p>
        <input
          type="file"
          accept=".json"
          onChange={handleFileChange}
          className="mb-3 w-full text-ui"
          style={{ color: '#1f1f1f' }}
        />
        {content && (
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={6}
            className="mb-3 w-full rounded border px-2 py-1 font-mono text-2xs"
            style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
          />
        )}
        {errors.length > 0 && (
          <div
            className="mb-3 rounded p-2 text-2xs"
            style={{ backgroundColor: '#f4877120', border: '1px solid #f48771', color: '#cd3131' }}
          >
            {errors.map((e, i) => (
              <div key={i}>
                {e.field}: {e.message}
              </div>
            ))}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={close}
            className="rounded px-3 py-1 text-ui"
            style={{ backgroundColor: '#ececec', color: '#1f1f1f' }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!content}
            className="rounded px-3 py-1 text-ui font-semibold"
            style={{
              backgroundColor: content ? '#0e639c' : '#ececec',
              color: content ? '#ffffff' : '#616161',
              cursor: content ? 'pointer' : 'not-allowed',
            }}
          >
            确认导入
          </button>
        </div>
      </div>
    </div>
  );
}
