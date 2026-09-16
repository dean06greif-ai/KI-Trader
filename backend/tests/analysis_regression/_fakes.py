"""Minimale Fake-DB (Motor-ähnlich) für Offline-Solltests – kein Mongo nötig."""
from types import SimpleNamespace


def _get_path(doc, key):
    cur = doc
    for part in str(key).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def _match_cond(cur, cond, exists=True):
    for op, val in cond.items():
        if op == "$in":
            if cur not in val:
                return False
        elif op == "$ne":
            if cur == val:
                return False
        elif op == "$gte":
            if cur is None or cur < val:
                return False
        elif op == "$lt":
            if cur is None or cur >= val:
                return False
        elif op == "$exists":
            if bool(val) != bool(exists):
                return False
        else:
            return False
    return True


def _match(doc, query):
    for k, v in (query or {}).items():
        cur, exists = _get_path(doc, k)
        if isinstance(v, dict) and any(str(x).startswith("$") for x in v):
            if not _match_cond(cur, v, exists):
                return False
        elif cur != v:
            return False
    return True


def _set_path(doc, key, value):
    parts = str(key).split(".")
    cur = doc
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _unset_path(doc, key):
    parts = str(key).split(".")
    cur = doc
    for p in parts[:-1]:
        cur = cur.get(p)
        if not isinstance(cur, dict):
            return
    cur.pop(parts[-1], None)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction=1):
        self.rows.sort(key=lambda r: r.get(key) or "", reverse=direction < 0)
        return self

    async def to_list(self, n=None):
        return list(self.rows if n is None else self.rows[:n])

    def __aiter__(self):
        self._i = iter(list(self.rows))
        return self

    async def __anext__(self):
        try:
            return next(self._i)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = [dict(r) for r in (rows or [])]

    async def find_one(self, query=None, *a, **k):
        for r in self.rows:
            if _match(r, query):
                return dict(r)
        return None

    def find(self, query=None, *a, **k):
        return FakeCursor([dict(r) for r in self.rows if _match(r, query)])

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("_id") or doc.get("id"))

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if _match(r, query):
                for k2, v2 in (update.get("$set") or {}).items():
                    _set_path(r, k2, v2)
                for k2 in (update.get("$unset") or {}):
                    _unset_path(r, k2)
                return SimpleNamespace(modified_count=1)
        if upsert:
            base = {k2: v for k2, v in (query or {}).items() if not isinstance(v, dict)}
            base.update(update.get("$set") or {})
            self.rows.append(base)
        return SimpleNamespace(modified_count=0)

    async def find_one_and_update(self, query, update, upsert=False):
        """Motor-Parität für den CAS-Claim (R13): atomarer Match+Update,
        Rückgabe des Dokuments VOR dem Update (Mongo-Default)."""
        for r in self.rows:
            if _match(r, query):
                before = dict(r)
                for k2, v2 in (update.get("$set") or {}).items():
                    _set_path(r, k2, v2)
                for k2 in (update.get("$unset") or {}):
                    _unset_path(r, k2)
                return before
        return None

    async def replace_one(self, query, doc, upsert=False):
        for i, r in enumerate(self.rows):
            if _match(r, query):
                self.rows[i] = dict(doc)
                return SimpleNamespace(modified_count=1)
        if upsert:
            self.rows.append(dict(doc))
        return SimpleNamespace(modified_count=0)

    async def delete_one(self, query):
        for i, r in enumerate(self.rows):
            if _match(r, query):
                del self.rows[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, query):
        keep = [r for r in self.rows if not _match(r, query)]
        n = len(self.rows) - len(keep)
        self.rows = keep
        return SimpleNamespace(deleted_count=n)

    async def update_many(self, query, update):
        n = 0
        for r in self.rows:
            if _match(r, query):
                r.update(update.get("$set") or {})
                n += 1
        return SimpleNamespace(modified_count=n)


class FakeDB:
    def __init__(self):
        self._cols = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._cols.setdefault(name, FakeCollection())
