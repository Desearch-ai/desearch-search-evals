import { Search, ChevronsDown, ChevronsUp } from "lucide-react";

interface FiltersProps {
  search: string;
  setSearch: (s: string) => void;
  totalCount: number;
  visibleCount: number;
  onExpandAll?: () => void;
  onCollapseAll?: () => void;
}

export function Filters({
  search,
  setSearch,
  totalCount,
  visibleCount,
  onExpandAll,
  onCollapseAll,
}: FiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 mb-4">
      <div className="flex-1 min-w-64 relative">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-dim" />
        <input
          type="text"
          placeholder="Search questions…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full pl-9 pr-3 py-2 glass rounded-lg text-sm placeholder:text-text-dim focus:outline-none focus:border-brand/60"
        />
      </div>
      <div className="ml-auto flex items-center gap-2 text-xs">
        {onExpandAll && (
          <button type="button" onClick={onExpandAll} title="Expand all"
            className="px-2 py-1.5 rounded-md glass text-text-muted hover:text-text">
            <ChevronsDown size={14} />
          </button>
        )}
        {onCollapseAll && (
          <button type="button" onClick={onCollapseAll} title="Collapse all"
            className="px-2 py-1.5 rounded-md glass text-text-muted hover:text-text">
            <ChevronsUp size={14} />
          </button>
        )}
        <span className="text-text-dim tabular ml-1">{visibleCount} / {totalCount}</span>
      </div>
    </div>
  );
}
