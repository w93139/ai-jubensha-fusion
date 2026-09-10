import type { FrozenSource, SourceBundle, SourceBundleSummary, SourcePreview, SourceVerification } from '@/types/sourceBundle';
import Image from 'next/image';

const kindLabels: Record<FrozenSource['kind'], string> = {
  original: '原件', ocr: '图像配套文字', revised: '修订材料', supplement: '编辑补充', reference: '后台参考',
};

const issueLabels: Record<string, string> = {
  SOURCE_FILE_INVALID: '文件缺失、发生变化或格式不符合要求。',
  PACKAGE_INVALID: '候选剧本包未通过结构与规则检查。',
  SCRIPT_SCOPE_MISMATCH: '候选剧本与这份来源快照不对应。',
  SOURCE_MANIFEST_MISMATCH: '候选包声明的文件与来源快照不一致。',
  ORIGINAL_KIND_MISMATCH: '这份材料不能作为原件引用。',
  SUPPLEMENT_KIND_MISMATCH: '这份材料与来源快照中的编辑补充分类不一致。',
  EDITORIAL_PROVENANCE_UNSUPPORTED: '编辑补充需要单独标注来源后再审核。',
  ORIGINAL_LINK_MISMATCH: '修订文字缺少正确的原件关联。',
  MULTI_ORIGINAL_PROVENANCE_UNSUPPORTED: '这份文字对应多个原件，当前候选包尚不能完整记录。',
  SOURCE_LOCATION_UNVERIFIED: '引用的页码、行号或标题无法定位。',
};

export type SourceBundlePanelProps = {
  bundles: SourceBundleSummary[];
  bundle?: SourceBundle;
  selectedHash: string;
  query: string;
  loading: boolean;
  verifying: boolean;
  previewLoading: boolean;
  error: string;
  report?: SourceVerification;
  preview?: SourcePreview;
  onSelect: (hash: string) => void;
  onQuery: (query: string) => void;
  onPreview: (source: FrozenSource) => void;
  onVerify: () => void;
  onReload: () => void;
};

export default function SourceBundlePanel(props: SourceBundlePanelProps) {
  const { bundle, preview } = props;
  const files = bundle?.sources.filter(source => source.relative_path.toLowerCase().includes(props.query.toLowerCase())) || [];
  const selected = bundle?.sources.find(source => source.id === preview?.sourceId);
  return <main className="mx-auto max-w-6xl px-4 pb-16 pt-20 text-paper">
    <p className="text-xs tracking-widest text-brass">管理员 · 剧本准备</p>
    <h1 className="mt-2 text-3xl font-bold">来源材料核验</h1>
    <p className="mt-3 text-sm leading-6 text-mist">核对这一版使用了哪些材料，查看原图与修订文字，再检查文件是否保持一致。这里的材料包含主持信息，仅管理员可见。</p>
    {props.error && <div role="alert" className="mt-5 rounded border border-red-400/50 bg-red-950/40 p-4 text-red-200">{props.error}</div>}
    <div className="mt-6 flex flex-wrap items-end gap-3">
      <label className="min-w-0 flex-1 text-sm">素材版本
        <select aria-label="素材版本" value={props.selectedHash} onChange={event => props.onSelect(event.target.value)} className="mt-2 w-full rounded border border-line bg-panel p-3">
          <option value="">请选择素材版本</option>
          {props.bundles.map(item => <option key={item.bundle_hash} value={item.bundle_hash} disabled={item.status === 'CORRUPT'}>
            {item.status === 'CORRUPT' ? '记录损坏，请检查来源存储' : `${item.edition} · ${item.file_count} 份文件`}
          </option>)}
        </select>
      </label>
      <button className="rounded border border-line px-4 py-3 text-sm" disabled={props.loading} onClick={props.onReload}>刷新列表</button>
      <button className="rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50" disabled={!bundle || props.loading || props.verifying} onClick={props.onVerify}>
        {props.verifying ? '正在检查文件…' : '检查文件一致性'}
      </button>
    </div>
    {props.loading && <p role="status" className="mt-5 text-mist">正在读取材料列表…</p>}
    {!props.loading && props.bundles.length === 0 && !props.error && <p className="mt-8 text-mist">还没有保存的来源快照。完成素材接收后，这里会显示可核对的版本。</p>}
    {bundle && <>
      <section aria-label="素材边界" className="mt-6 rounded border border-line bg-panel p-5">
        <h2 className="font-semibold">{bundle.edition}</h2>
        <p className="mt-2 text-sm text-mist">共 {bundle.file_count} 份文件。文件一致不代表 OCR、规则或剧情已经审核通过。</p>
        <ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-6 text-mist">{bundle.notes?.map((note, index) => <li key={index}>{note}</li>)}</ul>
      </section>
      {props.report && props.report.bundle_hash === bundle.bundle_hash && <section aria-label="核验结果" className={`mt-4 rounded border p-4 ${props.report.valid ? 'border-emerald-500/40' : 'border-red-400/60'}`}>
        <h2 className="font-semibold">{props.report.valid ? '文件一致性检查通过' : '文件核验未通过'}</h2>
        <p className="mt-2 text-sm text-mist">本次已核验 {props.report.checked_files} 份文件，记录 {props.report.issues.length} 项问题{props.report.issues_truncated ? '（问题列表已截断）' : ''}。检查结果已保存，尚未批准发布。</p>
        {!!props.report.issues.length && <p className="mt-2 text-sm">请先处理来源文件缺失、变化或引用不符，再重新核验。</p>}
        {!!props.report.issues.length && <ul aria-label="待处理问题" className="mt-3 max-h-64 space-y-3 overflow-auto text-sm">
          {props.report.issues.map((issue, index) => {
            const source = bundle.sources.find(item => item.id === issue.source_id);
            return <li key={index} className="rounded bg-red-950/30 p-3">
              {source && <button onClick={() => props.onPreview(source)} className="mb-1 block break-words text-left text-brass underline">{source.relative_path}</button>}
              <p>{issueLabels[issue.code] || '来源检查未通过，请查看核验记录。'}</p>
              {!source && issue.source_id && <p className="mt-1 break-all text-xs text-mist">来源标识：{issue.source_id}</p>}
            </li>;
          })}
        </ul>}
      </section>}
      <div className="mt-6 grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <section aria-label="来源列表" className="min-w-0 rounded border border-line bg-panel p-4">
          <label className="text-sm" htmlFor="source-filter">查找文件</label>
          <input id="source-filter" value={props.query} onChange={event => props.onQuery(event.target.value)} placeholder="输入文件名或目录名" className="mt-2 w-full rounded border border-line bg-ink p-3 text-sm" />
          <p className="my-3 text-xs text-mist">显示 {files.length} 份文件；点击后才会加载正文。</p>
          <ul className="max-h-[32rem] space-y-2 overflow-auto">
            {files.map(source => <li key={source.id}><button onClick={() => props.onPreview(source)} className={`w-full rounded border p-3 text-left ${preview?.sourceId === source.id ? 'border-brass' : 'border-line'}`}>
              <span className="block break-words text-sm">{source.relative_path}</span>
              <span className="mt-1 block text-xs text-mist">{kindLabels[source.kind]} · {(source.size_bytes / 1024).toFixed(1)} KB</span>
            </button></li>)}
          </ul>
          {files.length === 0 && <p className="py-8 text-sm text-mist">没有匹配的文件。</p>}
        </section>
        <section aria-label="材料预览" className="min-w-0 rounded border border-line bg-panel p-4">
          <h2 className="break-words font-semibold">{selected?.relative_path || '选择一份材料查看'}</h2>
          {props.previewLoading && <p role="status" className="mt-4 text-mist">正在核验并读取文件…</p>}
          {!props.previewLoading && preview?.text !== undefined && <pre className="mt-4 max-h-[36rem] overflow-auto whitespace-pre-wrap break-words text-sm leading-7">{preview.text}</pre>}
          {!props.previewLoading && preview?.imageUrl && <Image unoptimized src={preview.imageUrl} width={1600} height={2000} alt={selected?.relative_path || '来源原图'} className="mt-4 h-auto max-w-full" />}
          {!preview && !props.previewLoading && <p className="mt-4 text-sm text-mist">正文和原图按需加载。修订说明作为普通文字显示，不执行其中的脚本或链接。</p>}
          {!!selected?.original_paths.length && <div className="mt-4 border-t border-line pt-4 text-sm"><h3 className="font-semibold">关联原件</h3>
            {selected.original_paths.map(path => {
              const original = bundle.sources.find(source => source.relative_path === path);
              return original && <button key={path} onClick={() => props.onPreview(original)} className="mt-2 block break-words text-left text-brass underline">{path}</button>;
            })}
          </div>}
        </section>
      </div>
      <details className="mt-6 text-xs text-mist"><summary className="cursor-pointer">查看核验记录标识</summary>
        <p className="mt-3 break-all">来源快照：{bundle.bundle_hash}</p>
        {props.report?.bundle_hash === bundle.bundle_hash && <p className="mt-2 break-all">核验报告：{props.report.report_hash}</p>}
      </details>
    </>}
  </main>;
}
