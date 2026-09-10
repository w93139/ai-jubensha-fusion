import { useEffect, useRef, useState } from 'react';

export default function AuthoringRulePlanInput({ disabled, plan, onPlan, preserveText = false }: {
  disabled: boolean; plan: Record<string, unknown> | null;
  onPlan: (plan: Record<string, unknown> | null) => void;
  preserveText?: boolean;
}) {
  const active = useRef(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => () => { active.current += 1; }, []);
  return <div className="mt-3 min-w-0 rounded border border-line p-3">
    <p className="text-sm leading-6">{preserveText ? '规则和正文均保持草案原样。AI 只检查内容，发现问题会停下来，不会重抄或改写正文。草案仍需人工审核。' : '读取准备好的规则草案后，AI 只能摘录指定原文，不能修改调查消耗、前置条件或权限。草案仍需人工审核。'}</p>
    <label className="mt-3 block text-sm">规则草案文件
      <input type="file" accept=".json,application/json" aria-label="规则草案文件" disabled={disabled}
        className="mt-2 block w-full min-w-0 max-w-full text-xs" onChange={async event => {
          const file = event.target.files?.[0];
          const generation = ++active.current;
          onPlan(null); setError(''); setLoading(false);
          if (!file) return;
          if (file.size > 256 * 1024) { setError('草案超过 256 KiB，请拆分准备范围。'); return; }
          setLoading(true);
          try {
            const parsed: unknown = JSON.parse(await file.text());
            if (generation !== active.current) return;
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)
              || (parsed as Record<string, unknown>).schema_version !== 'script-package/1.2') throw new Error();
            onPlan(parsed as Record<string, unknown>);
          } catch {
            if (generation === active.current) setError('无法读取 1.2 规则草案，请检查文件。');
          } finally {
            if (generation === active.current) setLoading(false);
          }
        }} />
    </label>
    {loading && <p role="status" className="mt-2 text-xs">正在读取草案…</p>}
    {error && <p role="alert" className="mt-2 text-xs text-amber-100">{error}</p>}
    {plan && <p className="mt-2 break-all text-xs text-mist">草案已读取。标题、版本、人数与来源必须和本次选择一致；提交时由服务器检查完整规则及引用。</p>}
  </div>;
}
