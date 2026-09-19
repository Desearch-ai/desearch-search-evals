import { Search, X } from "lucide-react";

export function Filters({
  search,
  setSearch,
  totalCount,
  visibleCount,
}: {
  search: string;
  setSearch: (value: string) => void;
  totalCount: number;
  visibleCount: number;
}) {
  return (
    <div className="question-toolbar">
      <label className="search-field">
        <span className="sr-only">Search questions</span>
        <Search size={17} />
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search questions…"
        />
        {search && (
          <button
            type="button"
            onClick={() => setSearch("")}
            aria-label="Clear search"
          >
            <X size={15} />
          </button>
        )}
      </label>
      <span className="question-count" role="status">
        {visibleCount.toLocaleString()} of {totalCount.toLocaleString()}{" "}
        questions
      </span>
    </div>
  );
}
