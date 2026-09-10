import { useId } from 'react';
import * as Select from '@radix-ui/react-select';

const valuePrefix = 'play-option:';
export default function PlaySelect({ label, value, options, placeholder = '请选择', disabled, onChange, presentation = 'dropdown' }: {
  label: string; value: string; options: { value: string; label: string }[];
  presentation?: 'dropdown' | 'choices';
  placeholder?: string; disabled?: boolean; onChange: (value: string) => void;
}) {
  const groupId = useId();
  if (presentation === 'choices') return <span role="radiogroup" aria-label={label} className="mt-2 flex flex-wrap gap-2">
    {options.map(option => <label key={option.value} className="relative min-w-24 cursor-pointer">
      <input type="radio" name={groupId} value={option.value} checked={value === option.value} disabled={disabled}
        onChange={() => { if (!disabled) onChange(option.value); }} className="peer sr-only" />
      <span className="flex min-h-11 items-center justify-center gap-2 rounded-lg border border-line bg-ink px-4 py-3 text-sm text-paper transition-colors hover:border-brass/60 peer-checked:border-brass peer-checked:bg-brass/15 peer-checked:text-brass peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brass peer-disabled:cursor-not-allowed peer-disabled:opacity-40">
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-brass/10 text-brass" aria-hidden="true">{option.label.slice(0, 1)}</span>
        {option.label}<span aria-hidden="true" className={value === option.value ? 'visible' : 'invisible'}>✓</span>
      </span>
    </label>)}
    {!options.length && <span className="text-sm text-mist">暂无可选择的角色。</span>}
  </span>;
  return <Select.Root value={valuePrefix + value} disabled={disabled} onValueChange={v => onChange(v.slice(valuePrefix.length))}>
    <Select.Trigger aria-label={label} className="mt-2 flex w-full min-w-0 items-center justify-between gap-3 rounded-xl border border-line bg-ink px-4 py-3 text-left text-sm text-paper outline-none transition focus:border-brass focus:ring-2 focus:ring-brass/20 disabled:cursor-not-allowed disabled:opacity-50">
      <span className="min-w-0 truncate"><Select.Value>{options.find(o => o.value === value)?.label || placeholder}</Select.Value></span><Select.Icon className="text-brass">⌄</Select.Icon>
    </Select.Trigger>
    <Select.Portal><Select.Content position="popper" sideOffset={6} className="z-[100] max-h-[min(18rem,var(--radix-select-content-available-height))] w-[var(--radix-select-trigger-width)] overflow-hidden rounded-xl border border-brass/40 bg-panel p-1.5 text-paper shadow-2xl">
      <Select.ScrollUpButton className="text-center text-brass">⌃</Select.ScrollUpButton>
      <Select.Viewport>{(!options.some(o => o.value === '') ? [{ value: '', label: placeholder }, ...options] : options).map(option => <Select.Item key={option.value} value={valuePrefix + option.value} className="relative flex min-h-11 cursor-pointer select-none items-center rounded-lg py-2 pl-9 pr-3 text-sm outline-none data-[highlighted]:bg-brass/15 data-[highlighted]:text-brass data-[state=checked]:bg-brass/10">
        <Select.ItemIndicator className="absolute left-3 text-brass">✓</Select.ItemIndicator><Select.ItemText>{option.label}</Select.ItemText>
      </Select.Item>)}</Select.Viewport>
      <Select.ScrollDownButton className="text-center text-brass">⌄</Select.ScrollDownButton>
    </Select.Content></Select.Portal>
  </Select.Root>;
}
