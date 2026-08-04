/**
 * AssetConfigDialog —— 系统资产配置弹窗（新增 / 编辑）。
 *
 * 按资产类型渲染不同表单：
 *   - database: 支持主流 + 国产/信创数据库，智能配置 + 测试连接
 *   - rest: base_url / headers / timeout
 *   - ssh: host / port / username / password / private_key_ref
 *   - rpa: url / username / password / description
 *
 * 安全红线：密码字段提示使用 Keyring 引用（__KEYRING_REF:xxx），禁明文存储。
 */
import { useState, useCallback } from 'react';
import { useAssetStore, type AssetNode } from '@/store/assetStore';
import { ipc } from '@/ipc/invoke';

interface Props {
  /** 编辑模式传入现有节点；新增模式传 null */
  node: AssetNode | null;
  /** 新增时的默认类型 */
  defaultType?: AssetNode['type'];
  onClose: () => void;
}

const TYPE_OPTIONS: Array<{ value: AssetNode['type']; label: string }> = [
  { value: 'database', label: '🗄 数据库' },
  { value: 'rest', label: '🌐 REST API' },
  { value: 'ssh', label: '🔐 SSH' },
  { value: 'rpa', label: '🤖 RPA' },
];

// ---- 数据库类型注册表（主流 + 国产/信创）------------------------------------------

interface DbTypeInfo {
  value: string;
  label: string;
  defaultPort: number;
  category: 'mainstream' | 'xinchuang' | 'file';
  placeholderDb?: string;
  placeholderUser?: string;
}

const DB_TYPE_OPTIONS: DbTypeInfo[] = [
  // 主流
  { value: 'mysql',      label: 'MySQL',           defaultPort: 3306,  category: 'mainstream', placeholderDb: 'my_database', placeholderUser: 'readonly' },
  { value: 'postgresql', label: 'PostgreSQL',      defaultPort: 5432,  category: 'mainstream', placeholderDb: 'postgres',    placeholderUser: 'postgres' },
  { value: 'oracle',     label: 'Oracle',          defaultPort: 1521,  category: 'mainstream', placeholderDb: 'ORCL',        placeholderUser: 'system' },
  { value: 'sqlserver',  label: 'SQL Server',      defaultPort: 1433,  category: 'mainstream', placeholderDb: 'master',      placeholderUser: 'sa' },
  { value: 'clickhouse', label: 'ClickHouse',      defaultPort: 8123,  category: 'mainstream', placeholderDb: 'default',     placeholderUser: 'default' },
  { value: 'sqlite',     label: 'SQLite',          defaultPort: 0,     category: 'file',       placeholderDb: '',            placeholderUser: '' },
  // 国产/信创
  { value: 'dm',         label: '达梦 (DM)',        defaultPort: 5236,  category: 'xinchuang', placeholderDb: 'DMDB',       placeholderUser: 'SYSDBA' },
  { value: 'kingbase',   label: '人大金仓 (KingbaseES)', defaultPort: 54321, category: 'xinchuang', placeholderDb: 'test',  placeholderUser: 'system' },
  { value: 'gbase',      label: '南大通用 (GBase)',  defaultPort: 5258,  category: 'xinchuang', placeholderDb: 'gbase_db',  placeholderUser: 'gbase' },
  { value: 'oceanbase',  label: 'OceanBase',       defaultPort: 2881,  category: 'xinchuang', placeholderDb: 'oceanbase',   placeholderUser: 'root' },
  { value: 'tidb',       label: 'TiDB',            defaultPort: 4000,  category: 'xinchuang', placeholderDb: 'test',        placeholderUser: 'root' },
  { value: 'gaussdb',    label: '华为 GaussDB',     defaultPort: 5432,  category: 'xinchuang', placeholderDb: 'postgres',    placeholderUser: 'gaussdb' },
  { value: 'opengauss',  label: 'openGauss',       defaultPort: 5432,  category: 'xinchuang', placeholderDb: 'postgres',    placeholderUser: 'gaussdb' },
  { value: 'highgo',     label: '瀚高 (HighGo)',    defaultPort: 5866,  category: 'xinchuang', placeholderDb: 'highgo',      placeholderUser: 'sysdba' },
  // 文件
  { value: 'csv',        label: 'CSV 文件',         defaultPort: 0,     category: 'file' },
  { value: 'excel',      label: 'Excel 文件',       defaultPort: 0,     category: 'file' },
];

function getDbTypeInfo(dbType: string): DbTypeInfo {
  return DB_TYPE_OPTIONS.find((d) => d.value === dbType) ?? DB_TYPE_OPTIONS[0];
}

export function AssetConfigDialog({ node, defaultType = 'database', onClose }: Props): JSX.Element {
  const addAsset = useAssetStore((s) => s.addAsset);
  const updateAsset = useAssetStore((s) => s.updateAsset);

  const [type, setType] = useState<AssetNode['type']>(node?.type ?? defaultType);
  const [label, setLabel] = useState(node?.label ?? '');
  const [meta, setMeta] = useState<Record<string, string>>(() => {
    const m = node?.meta ?? {};
    const result: Record<string, string> = {};
    for (const [k, v] of Object.entries(m)) {
      result[k] = String(v ?? '');
    }
    return result;
  });
  const [saving, setSaving] = useState(false);

  const setField = useCallback((key: string, value: string) => {
    setMeta((prev) => ({ ...prev, [key]: value }));
  }, []);

  const handleSave = useCallback(async () => {
    if (!label.trim()) return;
    setSaving(true);
    try {
      if (node) {
        await updateAsset(node.id, { label: label.trim(), meta });
      } else {
        await addAsset({ type, label: label.trim(), icon: type, meta });
      }
      onClose();
    } finally {
      setSaving(false);
    }
  }, [node, type, label, meta, addAsset, updateAsset, onClose]);

  return (
    <div className="fixed inset-0 z-[300] flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="w-[440px] max-h-[80vh] overflow-auto rounded-lg p-5 shadow-2xl"
        style={{ backgroundColor: '#f3f3f3', border: '1px solid #d0d0d0' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-sm font-semibold" style={{ color: '#333333' }}>
          {node ? '编辑资产' : '新增资产'}
        </h2>

        {/* 类型选择（新增时可切换） */}
        <div className="mb-3">
          <Label text="类型" />
          <div className="flex gap-2">
            {TYPE_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                disabled={!!node}
                onClick={() => setType(opt.value)}
                className="rounded px-2 py-1 text-2xs transition-all"
                style={{
                  backgroundColor: type === opt.value ? '#0e639c' : '#ececec',
                  color: type === opt.value ? '#fff' : '#6e6e6e',
                  opacity: node ? 0.7 : 1,
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* 名称 */}
        <div className="mb-3">
          <Label text="名称" />
          <Input value={label} onChange={setLabel} placeholder="例如：核心账务库" />
        </div>

        {/* 按类型渲染字段 */}
        {type === 'database' && (
          <DatabaseFields meta={meta} setField={setField} />
        )}
        {type === 'rest' && (
          <RestFields meta={meta} setField={setField} />
        )}
        {type === 'ssh' && (
          <SshFields meta={meta} setField={setField} />
        )}
        {type === 'rpa' && (
          <RpaFields meta={meta} setField={setField} />
        )}

        {/* 操作按钮 */}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded px-3 py-1.5 text-xs hover:bg-gray-200"
            style={{ color: '#333333' }}
          >
            取消
          </button>
          <button
            type="button"
            disabled={saving || !label.trim()}
            onClick={handleSave}
            className="rounded px-4 py-1.5 text-xs font-semibold text-white"
            style={{ backgroundColor: '#0e639c', opacity: saving ? 0.6 : 1 }}
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- 各类型字段表单 ----------------------------------------------------------

function DatabaseFields({ meta, setField }: FieldProps): JSX.Element {
  const dbType = meta.db_type || 'mysql';
  const info = getDbTypeInfo(dbType);
  const isFile = info.category === 'file';

  // 测试连接状态
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  const handleTypeChange = useCallback((newType: string) => {
    const newInfo = getDbTypeInfo(newType);
    setField('db_type', newType);
    // 智能填充默认端口
    if (newInfo.defaultPort > 0) {
      setField('port', String(newInfo.defaultPort));
    }
    // 智能填充默认用户名
    if (newInfo.placeholderUser && !meta.username) {
      setField('username', newInfo.placeholderUser);
    }
    setTestResult(null);
  }, [meta.username, setField]);

  const handleTestConnection = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await ipc.dataTestConnection({
        dbType,
        host: meta.host || '127.0.0.1',
        ...(meta.port ? { port: parseInt(meta.port, 10) } : {}),
        database: meta.database || '',
        username: meta.username || '',
        password: meta.password || '',
        path: meta.path || '',
      });
      setTestResult({ ok: result.ok, message: result.message });
    } catch (e: unknown) {
      setTestResult({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setTesting(false);
    }
  }, [dbType, meta]);

  // 文件类型（SQLite/CSV/Excel）只需文件路径
  if (isFile) {
    return (
      <div className="space-y-2">
        <div>
          <Label text="数据库类型" />
          <DbTypeSelect value={dbType} onChange={handleTypeChange} />
        </div>
        <div>
          <Label text="文件路径" />
          <Input value={meta.path ?? ''} onChange={(v) => setField('path', v)} placeholder={dbType === 'sqlite' ? 'C:/data/my.db' : 'C:/data/report.xlsx'} />
        </div>
        <TestConnectionButton testing={testing} testResult={testResult} onTest={handleTestConnection} />
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* 数据库类型下拉 */}
      <div>
        <Label text="数据库类型" />
        <DbTypeSelect value={dbType} onChange={handleTypeChange} />
      </div>

      {/* 主机 + 端口 */}
      <div className="grid grid-cols-3 gap-2">
        <div className="col-span-2">
          <Label text="Host" />
          <Input value={meta.host ?? ''} onChange={(v) => setField('host', v)} placeholder="127.0.0.1" />
        </div>
        <div>
          <Label text="Port" />
          <Input value={meta.port ?? ''} onChange={(v) => setField('port', v)} placeholder={String(info.defaultPort)} />
        </div>
      </div>

      {/* 数据库名 */}
      <div>
        <Label text="Database" />
        <Input value={meta.database ?? ''} onChange={(v) => setField('database', v)} placeholder={info.placeholderDb ?? 'my_database'} />
      </div>

      {/* 用户名 + 密码 */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <Label text="Username" />
          <Input value={meta.username ?? ''} onChange={(v) => setField('username', v)} placeholder={info.placeholderUser ?? 'readonly'} />
        </div>
        <div>
          <Label text="Password (Keyring)" />
          <Input value={meta.password ?? ''} onChange={(v) => setField('password', v)} placeholder="__KEYRING_REF:db_xxx" />
        </div>
      </div>

      {/* 只读开关 */}
      <div>
        <Label text="只读账号" />
        <select
          value={meta.read_only ?? 'true'}
          onChange={(e) => setField('read_only', e.target.value)}
          className="w-full rounded px-2 py-1.5 text-xs outline-none"
          style={{ backgroundColor: '#ececec', color: '#333333', border: '1px solid #c0c0c0' }}
        >
          <option value="true">是（推荐）</option>
          <option value="false">否</option>
        </select>
      </div>

      {/* 测试连接 */}
      <TestConnectionButton testing={testing} testResult={testResult} onTest={handleTestConnection} />
    </div>
  );
}

/** 数据库类型下拉选择器（分组：主流 / 国产信创 / 文件） */
function DbTypeSelect({ value, onChange }: { value: string; onChange: (v: string) => void }): JSX.Element {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded px-2 py-1.5 text-xs outline-none"
      style={{ backgroundColor: '#ececec', color: '#333333', border: '1px solid #c0c0c0' }}
    >
      <optgroup label="主流数据库">
        {DB_TYPE_OPTIONS.filter((d) => d.category === 'mainstream').map((d) => (
          <option key={d.value} value={d.value}>{d.label}</option>
        ))}
      </optgroup>
      <optgroup label="国产 / 信创">
        {DB_TYPE_OPTIONS.filter((d) => d.category === 'xinchuang').map((d) => (
          <option key={d.value} value={d.value}>{d.label}</option>
        ))}
      </optgroup>
      <optgroup label="文件数据源">
        {DB_TYPE_OPTIONS.filter((d) => d.category === 'file').map((d) => (
          <option key={d.value} value={d.value}>{d.label}</option>
        ))}
      </optgroup>
    </select>
  );
}

/** 测试连接按钮 + 结果展示 */
function TestConnectionButton({ testing, testResult, onTest }: {
  testing: boolean;
  testResult: { ok: boolean; message: string } | null;
  onTest: () => void;
}): JSX.Element {
  return (
    <div className="mt-2">
      <button
        type="button"
        disabled={testing}
        onClick={onTest}
        className="rounded px-3 py-1.5 text-xs font-medium transition-all"
        style={{
          backgroundColor: testing ? '#ececec' : '#0e639c',
          color: testing ? '#6e6e6e' : '#fff',
        }}
      >
        {testing ? '测试中…' : '🔌 测试连接'}
      </button>
      {testResult && (
        <span
          className="ml-2 text-2xs"
          style={{ color: testResult.ok ? '#059669' : '#f14c4c' }}
        >
          {testResult.ok ? '✅' : '❌'} {testResult.message}
        </span>
      )}
    </div>
  );
}

function RestFields({ meta, setField }: FieldProps): JSX.Element {
  return (
    <div className="space-y-2">
      <div>
        <Label text="Base URL" />
        <Input value={meta.base_url ?? ''} onChange={(v) => setField('base_url', v)} placeholder="https://api.example.com" />
      </div>
      <div>
        <Label text="Headers (JSON)" />
        <Input value={meta.headers ?? ''} onChange={(v) => setField('headers', v)} placeholder='{"Authorization": "Bearer ..."}' />
      </div>
      <div>
        <Label text="Timeout (秒)" />
        <Input value={meta.timeout ?? ''} onChange={(v) => setField('timeout', v)} placeholder="30" />
      </div>
    </div>
  );
}

function SshFields({ meta, setField }: FieldProps): JSX.Element {
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-3 gap-2">
        <div className="col-span-2">
          <Label text="Host" />
          <Input value={meta.host ?? ''} onChange={(v) => setField('host', v)} placeholder="10.0.0.1" />
        </div>
        <div>
          <Label text="Port" />
          <Input value={meta.port ?? ''} onChange={(v) => setField('port', v)} placeholder="22" />
        </div>
      </div>
      <div>
        <Label text="Username" />
        <Input value={meta.username ?? ''} onChange={(v) => setField('username', v)} placeholder="root" />
      </div>
      <div>
        <Label text="Password (Keyring)" />
        <Input value={meta.password ?? ''} onChange={(v) => setField('password', v)} placeholder="__KEYRING_REF:ssh_web1" />
      </div>
      <div>
        <Label text="Private Key Ref (可选)" />
        <Input value={meta.private_key_ref ?? ''} onChange={(v) => setField('private_key_ref', v)} placeholder="__KEYRING_REF:ssh_key_web1" />
      </div>
    </div>
  );
}

function RpaFields({ meta, setField }: FieldProps): JSX.Element {
  return (
    <div className="space-y-2">
      <div>
        <Label text="URL" />
        <Input value={meta.url ?? ''} onChange={(v) => setField('url', v)} placeholder="https://hr.example.com" />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <Label text="Username" />
          <Input value={meta.username ?? ''} onChange={(v) => setField('username', v)} placeholder="bot_user" />
        </div>
        <div>
          <Label text="Password (Keyring)" />
          <Input value={meta.password ?? ''} onChange={(v) => setField('password', v)} placeholder="__KEYRING_REF:rpa_hr" />
        </div>
      </div>
      <div>
        <Label text="描述" />
        <Input value={meta.description ?? ''} onChange={(v) => setField('description', v)} placeholder="HR 系统自动化" />
      </div>
    </div>
  );
}

// ---- 通用小组件 ---------------------------------------------------------------

interface FieldProps {
  meta: Record<string, string>;
  setField: (key: string, value: string) => void;
}

function Label({ text }: { text: string }): JSX.Element {
  return (
    <label className="mb-0.5 block text-2xs" style={{ color: '#6e6e6e' }}>
      {text}
    </label>
  );
}

function Input({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }): JSX.Element {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full rounded px-2 py-1.5 text-xs outline-none"
      style={{ backgroundColor: '#ececec', color: '#333333', border: '1px solid #c0c0c0' }}
    />
  );
}
