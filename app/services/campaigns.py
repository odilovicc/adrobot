"""Campaign list page: search, filters, sorting and pagination over Keitaro's campaign list.

Keitaro's ``GET /campaigns`` has no server-side search, so we filter the (cached) full list here.
Pure functions — no DB, no HTTP.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from math import ceil
from typing import Any
from urllib.parse import urlencode, urlsplit

SORTS = {"id", "name", "group", "state", "created"}
PER_PAGE = (25, 50, 100)
STATES = ("active", "disabled")


@dataclass(frozen=True)
class ListQuery:
    q: str = ""
    state: str = ""  # "" | active | disabled
    group: str = ""  # group name; "-" = without group
    mine: bool = False  # only campaigns opened in Adrobot
    sort: str = "id"
    desc: bool = True
    page: int = 1
    per: int = 25

    @classmethod
    def parse(cls, params: Any) -> "ListQuery":
        def num(name: str, default: int) -> int:
            try:
                return max(1, int(params.get(name) or default))
            except ValueError:
                return default

        sort = params.get("sort") or "id"
        sort = sort if sort in SORTS else "id"
        per = num("per", 25)
        state = params.get("state") or ""
        return cls(
            q=(params.get("q") or "").strip(),
            state=state if state in STATES else "",
            group=params.get("group") or "",
            mine=params.get("mine") == "1",
            sort=sort,
            desc=(params.get("dir") or ("desc" if sort in ("id", "created") else "asc")) == "desc",
            page=num("page", 1),
            per=per if per in PER_PAGE else 25,
        )

    @property
    def is_filtered(self) -> bool:
        return bool(self.q or self.state or self.group or self.mine)

    def url(self, **changes: Any) -> str:
        """Query string for links: keeps current filters, applies ``changes``, drops defaults."""
        merged = {"q": self.q, "state": self.state, "group": self.group, "mine": "1" if self.mine else "",
                  "sort": self.sort, "dir": "desc" if self.desc else "asc", "page": self.page, "per": self.per}  # fmt: skip
        if any(k in changes for k in ("q", "state", "group", "mine", "sort", "dir", "per")):
            merged["page"] = 1
        merged.update(changes)
        default_dir = "desc" if merged["sort"] in ("id", "created") else "asc"
        defaults = {"sort": "id", "dir": default_dir, "page": 1, "per": 25}
        clean = {k: v for k, v in merged.items() if v not in ("", None, False) and defaults.get(k) != v}
        return "?" + urlencode(clean) if clean else "?"

    def sort_url(self, column: str) -> str:
        if self.sort == column:
            return self.url(sort=column, dir="asc" if self.desc else "desc")
        return self.url(sort=column, dir="desc" if column in ("id", "created") else "asc")

    def aria_sort(self, column: str) -> str:
        if self.sort != column:
            return "none"
        return "descending" if self.desc else "ascending"


@dataclass
class Row:
    id: int
    name: str
    alias: str
    state: str
    group: str
    domain: str
    created: str
    opened: bool = False
    dirty_streams: int = 0


@dataclass
class ListPage:
    rows: list[Row]
    total: int  # all campaigns, before filters
    found: int  # after filters
    query: ListQuery
    state_counts: dict[str, int] = field(default_factory=dict)
    groups: list[tuple[str, int]] = field(default_factory=list)

    @property
    def pages(self) -> int:
        return max(1, ceil(self.found / self.query.per))

    @property
    def first(self) -> int:
        return 0 if not self.found else (self.query.page - 1) * self.query.per + 1

    @property
    def last(self) -> int:
        return min(self.found, self.query.page * self.query.per)

    def page_numbers(self) -> list[int | None]:
        """1 … 4 5 [6] 7 8 … 20 — ``None`` is a gap."""
        cur, last = self.query.page, self.pages
        wanted = {1, last, *range(cur - 2, cur + 3)}
        out: list[int | None] = []
        for n in sorted(p for p in wanted if 1 <= p <= last):
            if out and n - (out[-1] or 0) > 1:
                out.append(None)
            out.append(n)
        return out


def _host(domain: str) -> str:
    if not domain:
        return ""
    return urlsplit(domain if "//" in domain else f"//{domain}").hostname or domain


def to_row(c: dict[str, Any], opened: set[int], dirty: dict[int, int]) -> Row:
    cid = int(c["id"])
    return Row(id=cid, name=c.get("name") or f"#{cid}", alias=c.get("alias") or "", state=c.get("state") or "",
               group=c.get("group") or "", domain=_host(c.get("domain") or ""), created=c.get("created_at") or "",
               opened=cid in opened, dirty_streams=dirty.get(cid, 0))  # fmt: skip


def _matches(row: Row, q: ListQuery) -> bool:
    if q.state and row.state != q.state:
        return False
    if q.group and (row.group or "-") != q.group:
        return False
    if q.mine and not row.opened:
        return False
    if q.q:
        needle = q.q.lower()
        if needle.startswith("#") and needle[1:].isdigit():
            return row.id == int(needle[1:])
        hay = f"{row.id} {row.name} {row.alias}".lower()
        return all(part in hay for part in needle.split())
    return True


def _natural(text: str) -> list[Any]:
    """"Keto #4" < "Keto #13": digits compare as numbers."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", text.lower()) if part]


def _sort_key(column: str):
    return {
        "id": lambda r: r.id,
        "name": lambda r: _natural(r.name),
        "group": lambda r: (r.group == "", r.group.lower(), -r.id),
        "state": lambda r: (r.state, -r.id),
        "created": lambda r: (r.created, r.id),
    }[column]


def build_page(campaigns: list[dict[str, Any]], query: ListQuery, opened: set[int] | None = None,
               dirty: dict[int, int] | None = None) -> ListPage:  # fmt: skip
    rows = [to_row(c, opened or set(), dirty or {}) for c in campaigns if c.get("state") != "deleted"]
    # Facet counts ignore their own filter, so switching state/group always shows what you'd get.
    state_counts = Counter(r.state for r in rows if _matches(r, ListQuery(**{**query.__dict__, "state": ""})))
    group_counts = Counter(r.group or "-" for r in rows if _matches(r, ListQuery(**{**query.__dict__, "group": ""})))
    found = [r for r in rows if _matches(r, query)]
    found.sort(key=_sort_key(query.sort), reverse=query.desc)

    pages = max(1, ceil(len(found) / query.per))
    query = ListQuery(**{**query.__dict__, "page": min(query.page, pages)})
    start = (query.page - 1) * query.per
    return ListPage(
        rows=found[start : start + query.per], total=len(rows), found=len(found), query=query,
        state_counts={"": sum(state_counts.values()), **{s: state_counts.get(s, 0) for s in STATES}},
        groups=sorted(group_counts.items(), key=lambda kv: (kv[0] == "-", kv[0].lower())),
    )  # fmt: skip
